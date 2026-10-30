#!/usr/bin/env python
"""打包成单文件 exe，并在打包后把「只有干净机器才暴露」的三件事各验一遍。

    python tools/build_exe.py            # onefile（默认，交付形态）
    python tools/build_exe.py --onedir   # onedir（只用于对比启动耗时）
    python tools/build_exe.py --no-verify # 只打包，跳过打包后的核对

产物：`dist/通知.exe`。

--------------------------------------------------------------------------
为什么需要这个脚本，而不是直接 `pyinstaller notify.spec`
--------------------------------------------------------------------------
打包这个动作**在本机几乎不可能失败**：`.venv` 里所有依赖都在，
`import` 走的是源码目录，`webui/` 是真实存在的目录。所以「本机能跑」
什么都不能证明。真正会翻车的是三件只在干净机器上暴露的事：

1. `hiddenimports` 漏配 → 某个模块在 exe 里 ImportError（本机有 .venv 兜着）
2. `datas` 漏文件 → 前端白屏 / 没样式（本机走的是仓库里的真实目录）
3. `excludes` 排错 → 某一页点开就崩（本机不会走 excludes）

第 2 件是这个脚本能**完全自动化**验的（`check_webui`：源码 N 个文件，
exe 里必须也是这 N 个，逐个点名对）。第 1、3 件只能自动验到「exe 能起、
`--version` 能返回」，剩下的五页 + 发一条消息必须人工过——那部分在
`docs/prompts/W7-单文件打包.md` §4 的第 4、5 条，不许用本脚本代替。

--------------------------------------------------------------------------
脚本会自己做的检查
--------------------------------------------------------------------------
    打包前  preflight()      spec / manifest / version_info / 资源目录都在
    打包后  check_layout()   dist/ 里只有 通知.exe（onefile 的判据）
            check_webui()    源码 webui 文件数 == exe 内文件数，且逐个点名对
            check_smoke()    `通知.exe --version` 退出码为 0
            report()         体积、耗时

**`check_webui` 是这里唯一不能省的一条。** 数字对不上就是 `datas` 漏了，
去看是哪个文件漏的，不许改 `datas` 的写法把数字糊过去。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "notify.spec"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_NAME = "通知"
EXE = DIST / f"{APP_NAME}.exe"

# 体积上限（任务书 §4 第 2 条）。超了不是「警告一下」——它意味着
# 有东西没排干净（最典型的是 numpy / cv2 被某个 hook 又拖回来）。
SIZE_LIMIT_MB = 40

# 除 webui/ 之外必须存在的**数据文件**（相对 exe 根）。
#
# `webview/js/` 是 pywebview 注入 `window.pywebview` 用的脚本，**上游 hook 没收**。
# 漏了之后：窗口开、CSS 在、骨架屏在，只有 bridge 永远不返回——
# 页面停在「正在读取…」，而 Python 侧一行异常都没有。
# 这一条不查，就只能靠人盯着截图看出「怎么一直转圈」。
REQUIRED_DATAS = [
    "webview/js/api.js",
    "webview/js/finish.js",
    "webview/js/state.js",
    "webview/lib/Microsoft.Web.WebView2.WinForms.dll",
    "assets/icon.ico",
]

# **必须出现在 exe 里的模块。** 这一条是「exe 能起 ≠ 能用」的自动化版本。
#
# 为什么单列：`app.py` 的 `_delegate()` 用 `importlib.import_module(cmd)` 分发，
# cmd 是运行时变量，PyInstaller 静态分析看不见。第一版 spec 就漏了这五个，
# 结果 exe 12.1MB、`--version` 退出码 0、webui 11==11，全绿——
# 而 `通知.exe hook` 在干净机器上是 ModuleNotFoundError，
# 也就是「整个产品最核心的那条链路根本没进包」。
# 这台机器上永远测不出来（`python app.py hook` 走的是源码）。
REQUIRED_MODULES = [
    # 注：不列 `app`。入口脚本是 CArchive 里的 `app` 条目，不在 PYZ 里，
    # 列进来会永远报「没进包」——检查项自己先假红，比不查更糟。
    "hook",         # 宿主事件 → 收通知（核心链路）
    "dispatch",     # 发通知的子进程
    "tray",         # 托盘
    "install",      # 安装器
    "selftest",     # 自检
    "wbn.config",   # 路径解析（frozen 分支就住在这里）
    "wbn.pipeline",
    "wbn.bridge",
    "webview.platforms.winforms",   # Windows 上真正用的 GUI 后端
    "clr",                          # pythonnet，WebView2 靠它
]

# 打包前必须存在的文件。少一个就别开跑，省得打到一半才发现。
REQUIRED = [
    "app.py",
    "notify.spec",
    "notify.manifest",
    "version_info.txt",
    "config.example.json",
    "assets/icon.ico",
    "webui/index.html",
]


def _mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


# ------------------------------------------------------------------ 打包前


def preflight(onedir: bool) -> None:
    """打包前的最低检查。失败直接退出——打到一半才发现更快吗？不，更慢。"""
    print("[1/7] 打包前检查")
    missing = [p for p in REQUIRED if not (ROOT / p).exists()]
    if missing:
        sys.exit(f"  ✘ 缺文件：{', '.join(missing)}")

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("  ✘ 没装 PyInstaller。装一下：pip install pyinstaller")

    import PyInstaller

    print(f"  ✔ PyInstaller {PyInstaller.__version__}")
    print(f"  ✔ 形态：{'onedir' if onedir else 'onefile'}")

    # 上一轮的产物必须清掉。留着的话，这一轮失败时 `dist/通知.exe`
    # 还是旧的，人会误以为打包成功了。
    for junk in ([DIST / APP_NAME] if onedir else [EXE]):
        if junk.is_dir():
            shutil.rmtree(junk, ignore_errors=True)
        elif junk.exists():
            junk.unlink()
    # 上一轮**跑过** exe 的话，`dist/` 里还会有 config.json / state / logs
    # （那是正确行为：配置和日志就该写在 exe 旁边）。它们不属于构建产物，
    # 清掉，否则下面「dist 里只有一个文件」的判据会被自己上一次的测试结果搞红。
    for junk in (DIST / "logs", DIST / "state", DIST / "config.json"):
        if junk.is_dir():
            shutil.rmtree(junk, ignore_errors=True)
        elif junk.exists():
            junk.unlink()


# ------------------------------------------------------------------ 打包


def build(onedir: bool, clean: bool) -> float:
    print("\n[2/7] PyInstaller")
    env = dict(os.environ)
    env["WBN_ONEDIR"] = "1" if onedir else "0"
    # 让 PyInstaller 的日志走 UTF-8：Windows 默认 cp936，中文名 `通知.exe`
    # 出现在日志里会变成乱码，出问题时没法 grep。
    env["PYTHONUTF8"] = "1"

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm"]
    if clean:
        cmd.append("--clean")
    cmd.append(str(SPEC))

    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, check=False)
    dt = time.perf_counter() - t0
    if proc.returncode != 0:
        sys.exit(f"  ✘ PyInstaller 退出码 {proc.returncode}（日志见 build/）")
    print(f"  ✔ 打包完成，用时 {dt:.1f}s")
    return dt


# ------------------------------------------------------------------ 打包后


def check_layout(onedir: bool) -> Path:
    """onefile 的判据是「dist/ 里只有一个文件」。"""
    print("\n[3/7] 产物形态")
    if not DIST.exists():
        sys.exit("  ✘ dist/ 不存在——PyInstaller 没产出东西，去看 build/ 的日志")

    entries = sorted(p.name for p in DIST.iterdir())
    # 运行过 exe 之后，`dist/` 里会多出 config.json / state / logs——那是
    # 正确行为（配置和日志写在 exe 旁边），不是构建产物。preflight 会清，
    # 这里再容一次，免得「跑完测试再打包」被判成失败。
    runtime = {"config.json", "state", "logs"}
    stale = [e for e in entries if e not in runtime]
    if onedir:
        target = DIST / APP_NAME
        if not (target / f"{APP_NAME}.exe").exists():
            sys.exit(f"  ✘ 没找到 {target / f'{APP_NAME}.exe'}；dist/ 里有 {entries}")
        print(f"  ✔ onedir：{APP_NAME}/（{len(list(target.rglob('*')))} 个条目）")
        return target / f"{APP_NAME}.exe"

    if stale != [f"{APP_NAME}.exe"]:
        sys.exit(f"  ✘ dist/ 里应该只有 {APP_NAME}.exe，实际有：{stale}")
    if entries != stale:
        print(f"  · 另有运行期产物（正常）：{sorted(set(entries) - set(stale))}")
    print(f"  ✔ onefile：dist/ 里只有 {APP_NAME}.exe")
    return EXE


def _names_in_exe(exe: Path) -> set[str]:
    """列出 exe 内归档里的所有条目名（统一成正斜杠）。"""
    from PyInstaller.archive.readers import CArchiveReader

    reader = CArchiveReader(str(exe))
    try:
        raw = list(reader.toc.keys())
    finally:
        close = getattr(reader, "close", None)
        if callable(close):
            close()
    return {n.replace("\\", "/") for n in raw}


def _pyz_modules(exe: Path, onedir: bool) -> set[str]:
    """取出 exe 里那个 PYZ 归档中的模块名集合。

    PYZ 是**第二个**归档（CArchive 里套一个 zlib 压缩的 .pyz），所以不能只读
    外层 TOC——外层只有二进制和 datas，纯 Python 模块全在里层。
    第一版核对只看了外层，于是「webui 11==11、体积 12MB、冒烟通过」，
    而五个子命令模块一个都没进包。
    """
    import tempfile

    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    if onedir:
        # onedir 把 PYZ 单独放在 _internal 下，不用从 exe 里挖。
        cand = sorted((DIST / APP_NAME / "_internal").glob("PYZ*.pyz"))
        if not cand:
            return set()
        return set(ZlibArchiveReader(str(cand[0])).toc.keys())

    reader = CArchiveReader(str(exe))
    try:
        inner = next(k for k in reader.toc if k.endswith(".pyz"))
        blob = reader.extract(inner)
    finally:
        close = getattr(reader, "close", None)
        if callable(close):
            close()

    with tempfile.NamedTemporaryFile(suffix=".pyz", delete=False) as fh:
        fh.write(blob)
        tmp = Path(fh.name)
    try:
        return set(ZlibArchiveReader(str(tmp)).toc.keys())
    finally:
        tmp.unlink(missing_ok=True)


def check_modules(exe: Path, onedir: bool) -> None:
    """REQUIRED_MODULES 必须全在 PYZ 里。**这是「能不能用」的第一道门。**"""
    print("\n[4/7] 子命令模块核对")
    got = _pyz_modules(exe, onedir)
    if not got:
        print("  · 读不到 PYZ（onedir 布局变了？）——跳过这一条，请人工跑一遍子命令")
        return

    missing = [m for m in REQUIRED_MODULES if m not in got]
    if missing:
        print("  ✘ 这些模块没进包：")
        for m in missing:
            print(f"      - {m}")
        sys.exit(
            "  ✘ 多半是 app.py 用 importlib.import_module(变量) 动态分发，"
            "PyInstaller 静态分析看不见。去 notify.spec 的 hiddenimports 里补上。"
        )
    print(f"  ✔ {len(REQUIRED_MODULES)} 个关键模块全在（PYZ 共 {len(got)} 个）")


def check_datas(exe: Path, onedir: bool) -> None:
    """REQUIRED_DATAS 必须都在。**「界面能画出来但点了没反应」就靠这条挡。**"""
    print("\n[5/7] 运行期数据文件")
    if onedir:
        base = DIST / APP_NAME / "_internal"
        got = {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()}
    else:
        got = _names_in_exe(exe)

    missing = [d for d in REQUIRED_DATAS if d not in got]
    if missing:
        print("  ✘ 这些文件没进 exe：")
        for d in missing:
            print(f"      - {d}")
        sys.exit(
            "  ✘ 缺 `webview/js/*` 的话界面会一直停在「正在读取…」"
            "（bridge 注入失败，Python 侧无异常）。去 notify.spec 的 datas 里补。"
        )
    print(f"  ✔ {len(REQUIRED_DATAS)} 个关键数据文件都在")


def check_webui(exe: Path, onedir: bool) -> tuple[int, int]:
    """源码 webui 文件数 vs exe 内 webui 文件数。**两个数必须相等。**

    这一条单列，是因为它漏了以后的症状最阴：源码形态完全正常，
    exe 里白屏或没样式，而本机测不出来（本机走的是仓库里的真实目录）。
    """
    print("\n[6/7] 前端资源核对")
    src = sorted(
        p.relative_to(ROOT).as_posix() for p in (ROOT / "webui").rglob("*") if p.is_file()
    )

    if onedir:
        inner_dir = DIST / APP_NAME / "_internal" / "webui"
        if not inner_dir.exists():
            sys.exit(f"  ✘ onedir 里没有 {inner_dir}")
        got = {
            p.relative_to(DIST / APP_NAME / "_internal").as_posix()
            for p in inner_dir.rglob("*")
            if p.is_file()
        }
    else:
        got = {n for n in _names_in_exe(exe) if n.startswith("webui/")}

    print(f"  源码 webui/：{len(src)} 个文件")
    print(f"  exe  内    ：{len(got)} 个文件")

    missing = [p for p in src if p not in got]
    extra = sorted(got - set(src))
    if missing:
        print("  ✘ exe 里缺这些文件（datas 漏了）：")
        for p in missing:
            print(f"      - {p}")
    if extra:
        # 多出来的不算错误（PyInstaller 可能补目录占位），但要看得见。
        print("  · exe 里多出来的条目（一般无害）：")
        for p in extra[:10]:
            print(f"      + {p}")

    if len(src) != len(got) or missing:
        sys.exit(f"  ✘ 数字不等：源码 {len(src)} ≠ exe {len(got)}。见上面缺的文件。")
    print(f"  ✔ 两边都是 {len(src)} 个，且逐个点名对上了")
    return len(src), len(got)


def check_smoke(exe: Path) -> None:
    """`通知.exe --version` 能不能起来。

    **判据是退出码，不是 stdout**：spec 里 `console=False`，exe 没有控制台，
    `print()` 的输出直接丢弃，重定向也读不到。所以这里只看它有没有
    正常退出——它至少证明了「Python 运行时解压成功 + 入口模块 import 成功」。
    """
    print("\n[7/7] 冒烟：--version")
    try:
        proc = subprocess.run(
            [str(exe), "--version"],
            cwd=str(ROOT),
            capture_output=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired:
        sys.exit("  ✘ 120s 没退出。多半是 `--version` 落到了开窗口那条路上。")
    if proc.returncode != 0:
        out = (proc.stdout or b"").decode("utf-8", "replace")
        err = (proc.stderr or b"").decode("utf-8", "replace")
        sys.exit(f"  ✘ 退出码 {proc.returncode}\n{out}\n{err}")
    print("  ✔ 退出码 0")


def report(exe: Path, build_s: float) -> None:
    print("\n" + "=" * 60)
    size = _mb(exe)
    print(f"产物：{exe}")
    print(f"体积：{size:.1f} MB（上限 {SIZE_LIMIT_MB} MB）")
    print(f"打包：{build_s:.1f}s")
    print("=" * 60)
    if size > SIZE_LIMIT_MB:
        sys.exit(
            f"\n✘ 超上限 {SIZE_LIMIT_MB}MB。先查是谁把东西拖回来的：\n"
            f"    python -m PyInstaller.utils.cliutils.archive_viewer -l {exe}\n"
            "  常见原因：某个 hook 把 numpy / cv2 又收进来了。\n"
            "  **不许为了让体积过关而把在用的模块加进 excludes。**"
        )
    print(
        "\n还剩两条只能人工过（任务书 §4 第 4、5 条）：\n"
        "  · 干净机器双击 → 窗口出现 → 五页都能进\n"
        "  · 干净机器配好 hook → 宿主跑个任务 → 手机收到消息"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 通知.exe")
    ap.add_argument("--onedir", action="store_true", help="出 onedir（只用于对比启动耗时）")
    ap.add_argument("--no-clean", action="store_true", help="不传 --clean（复用 build 缓存，快）")
    ap.add_argument("--no-verify", action="store_true", help="只打包，跳过打包后的核对")
    args = ap.parse_args()

    preflight(args.onedir)
    dt = build(args.onedir, clean=not args.no_clean)
    exe = check_layout(args.onedir)
    if not args.no_verify:
        check_modules(exe, args.onedir)
        check_datas(exe, args.onedir)
        check_webui(exe, args.onedir)
        check_smoke(exe)
    report(exe, dt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
