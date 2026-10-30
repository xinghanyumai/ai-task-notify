#!/usr/bin/env python
"""打包成单文件 exe，并在打包后把「只有干净机器才暴露」的三件事各验一遍。

    python tools/build_exe.py            # onefile（默认，交付形态）
    python tools/build_exe.py --onedir   # onedir（只用于对比启动耗时）
    python tools/build_exe.py --no-verify # 只打包，跳过打包后的核对

产物：项目根目录的 `通知.exe`（W7.5 起；之前在 `dist/`）。

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
    打包后  check_layout()   根目录有 通知.exe、没有 通知/、体积在 18–26MB
            check_webui()    源码 webui 文件数 == exe 内文件数，且逐个点名对
            check_smoke()    `通知.exe --version` 退出码为 0
            report()         体积、耗时

**`check_webui` 是这里唯一不能省的一条。** 数字对不上就是 `datas` 漏了，
去看是哪个文件漏的，不许改 `datas` 的写法把数字糊过去。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "notify.spec"
BUILD = ROOT / "build"
APP_NAME = "通知"
# 产物就落在项目根（W7.5 从 `dist/` 挪过来的）。
#
# 刻意**不设 `DIST` 变量**：以前 `DIST = ROOT / "dist"` 是个间接层，改产物位置时
# 只要漏改一处（尤其是 `build()` 没传 `--distpath`），产物就还在 `dist/` 里，
# 而所有常量看上去都改了。现在凡是要写路径的地方直接用 `ROOT`。
EXE = ROOT / f"{APP_NAME}.exe"
ONEDIR_DIR = ROOT / APP_NAME

# 体积上限（任务书 §4 第 2 条）。超了不是「警告一下」——它意味着
# 有东西没排干净（最典型的是 numpy / cv2 被某个 hook 又拖回来）。
SIZE_LIMIT_MB = 40

# 体积**预期**区间（W7.5 §2.1 第 3 条）。挪位置前实测 20,162,588 B ≈ 19.2MB。
# 偏小 = 打出了空壳（`hiddenimports` / `datas` 没进包，第一版就踩过：12.1MB，
# `--version` 退出码 0，全绿，而五个子命令一个都没进包）。
EXE_SIZE_RANGE_MB = (18.0, 26.0)

# 冒烟等多久（秒）。本机 1.2~2.4s；2026-10-30 的 runner 上卡满 300s 不退出。
#
# 根因最后查出来是 exe 里抛 `UnicodeEncodeError`（cp1252 的 stdout + 中文），
# PyInstaller 弹了个没人点得掉的「Unhandled exception in script」对话框——
# **不是慢，是死在对话框上**。`app.py` 的 `_harden_stdio` / `_main_guarded`
# 修完之后 runner 上 1.2s 退出 0。
#
# 300s 留着不是为了让谁绿：真判据仍是退出码，超时只是把「慢」和「死」分开，
# 红的时候带上 `_smoke_diag` 的证据。
SMOKE_TIMEOUT_S = 300

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

    # 上一轮的产物必须清掉。留着的话，这一轮失败时 `通知.exe`
    # 还是旧的，人会误以为打包成功了。
    #
    # **只删 exe 本身（onedir 形态删 `通知/` 目录），一个字节都不许多删。**
    # W7.5 之前产物在 `dist/`，这里还顺手清过 `config.json` / `logs` / `state`
    # ——那是对的，`dist/` 里那三样只是「跑 exe 时写下的副本」。
    # 现在产物就在项目根，`config.json` / `logs/` / `state/` 是**源码模式和 exe
    # 模式共用的真实数据**（里面是微信 appid / secret / openid、全部通知记录、
    # 所有会话的 turn_index）。再清那三样就不是清理，是删库。
    for junk in ([ONEDIR_DIR] if onedir else [EXE]):
        try:
            if junk.is_dir():
                shutil.rmtree(junk)
            elif junk.exists():
                junk.unlink()
        except OSError as e:
            # Windows 上 exe 正在跑时删不掉（WinError 32）；有的环境删除会走
            # 回收站机制，抛的是别的 OSError。两种都收在这里，给一句人话——
            # 直接把 PyInstaller 那串 `[WinError 32]` 或 traceback 甩给用户，
            # 他只会以为脚本坏了。
            sys.exit(
                f"  ✘ 删不掉旧的 {junk.name}，多半是它正在运行——先关掉再打包"
                "（托盘右键退出，或 taskkill /IM 通知.exe /F）\n"
                f"    底层错误：{e}"
            )


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
    # `--distpath` 必须显式传。`notify.spec` 里没写 distpath，PyInstaller 默认
    # 输出到 `cwd/dist`——只改常量（`EXE` 指到根目录）产物不会跟着挪，
    # 结果就是「检查报找不到 exe，而 dist/ 里躺着一个新的」。
    cmd += ["--distpath", str(ROOT)]
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
    """产物形态：onefile 就是根目录一个 `通知.exe`，且**不许**出现 `通知/` 目录。

    判据不能是「目录里只有一个文件」——产物在项目根，根目录有十几个源码文件
    （`app.py` / `notify.spec` / `requirements.txt` …），数文件数会把一次正常
    构建直接判成失败。改成三条正面判据：exe 在、`通知/` 不在、体积在预期区间。
    """
    print("\n[3/7] 产物形态")
    if onedir:
        target = ONEDIR_DIR
        if not (target / f"{APP_NAME}.exe").exists():
            sys.exit(f"  ✘ 没找到 {target / f'{APP_NAME}.exe'}——去看 build/ 的日志")
        print(f"  ✔ onedir：{APP_NAME}/（{len(list(target.rglob('*')))} 个条目）")
        return target / f"{APP_NAME}.exe"

    if not EXE.exists():
        sys.exit(
            f"  ✘ 根目录没有 {APP_NAME}.exe——PyInstaller 没产出东西，去看 build/ 的日志"
        )
    if ONEDIR_DIR.exists():
        sys.exit(
            f"  ✘ 根目录出现了 {APP_NAME}/ 目录——那是 onedir 形态，onefile 不该有。"
            f" 先删掉 {ONEDIR_DIR} 再打包。"
        )

    mb = _mb(EXE)
    lo, hi = EXE_SIZE_RANGE_MB
    if not lo <= mb <= hi:
        sys.exit(
            f"  ✘ {APP_NAME}.exe 是 {mb:.1f}MB，不在 {lo:.0f}–{hi:.0f}MB 区间。\n"
            "  偏小多半是打出了空壳（hiddenimports / datas 没进包）；"
            " 偏大常见是某个 hook 把 numpy / cv2 又拖回来了。"
        )
    print(f"  ✔ onefile：根目录的 {APP_NAME}.exe（{mb:.1f}MB，且没有 {APP_NAME}/ 目录）")
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
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    if onedir:
        # onedir 把 PYZ 单独放在 _internal 下，不用从 exe 里挖。
        cand = sorted((ONEDIR_DIR / "_internal").glob("PYZ*.pyz"))
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
        base = ONEDIR_DIR / "_internal"
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
        inner_dir = ONEDIR_DIR / "_internal" / "webui"
        if not inner_dir.exists():
            sys.exit(f"  ✘ onedir 里没有 {inner_dir}")
        got = {
            p.relative_to(ONEDIR_DIR / "_internal").as_posix()
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


def _die(msg: str) -> None:
    """失败信息一律打到 **stdout**，然后 exit 1。

    为什么不直接 `sys.exit(msg)`：那样是写 stderr。2026-10-30 实测，CI 上这个
    step 的 stderr 根本没进日志——`[7/7] 冒烟：--version` 之后只剩一行
    `##[error]Process completed with exit code 1.`，脚本自己写的那句「✘ 退出码 N」
    一个字都没有。排查时只能靠猜，所以改成 stdout：那是唯一确定看得见的地方。
    """
    print(msg, flush=True)
    sys.exit(1)


def _kill_tree(proc: subprocess.Popen) -> None:
    """连同子孙一起杀掉。

    onefile 的 exe 是「引导器父进程 + 真正跑代码的子进程」两层，只 kill 父进程
    会留一个孤儿继续占着东西。
    """
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, timeout=30, check=False)
    except Exception:                       # noqa: BLE001
        pass
    try:
        proc.kill()
    except Exception:                       # noqa: BLE001
        pass


def _windows(pid: int) -> list[tuple[int, str, str]]:
    """列出某个进程的顶层窗口：`[(hwnd, 类名, 标题)]`。

    **为什么需要这个**：exe 是 GUI 子系统，脚本里抛异常时 PyInstaller 会弹一个
    `Unhandled exception in script` 的对话框等人点。runner 上没有桌面，没人点，
    进程就永远不退出，而 stdout/stderr 一个字都没有——只看「超时」根本分不清
    是解压慢、死循环还是弹了窗。窗口标题是唯一能从外面看见的信号。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    out: list[tuple[int, str, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            n = user32.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, title, n + 1)
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            out.append((hwnd, cls.value, title.value))
        return True

    user32.EnumWindows(cb, 0)
    return out


def _child_texts(hwnd: int) -> list[str]:
    """取一个窗口里所有子控件的文字——MessageBox 的正文就在 Static 控件里。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    texts: list[str] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(child, _lparam):
        n = user32.GetWindowTextLengthW(child)
        if n > 0:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(child, buf, n + 1)
            if buf.value.strip():
                texts.append(buf.value)
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
    return texts


def _dialog_report(pids: list[int]) -> list[str]:
    """把这几些进程的窗口标题（和对话框正文）收成几行诊断。"""
    lines = []
    for pid in pids:
        for hwnd, cls, title in _windows(pid):
            lines.append(f"    pid {pid} 窗口：class={cls!r} title={title!r}")
            if "exception" in title.lower() or "error" in title.lower():
                for text in _child_texts(hwnd):
                    lines.append("      对话框正文：" + text.replace("\n", "\n      "))
    return lines or ["    (这些 pid 没有顶层窗口)"]


def _smoke_diag(exe: Path, proc: subprocess.Popen, elapsed: float,
                out_path: Path, err_path: Path) -> str:
    """冒烟超时时把「卡在哪一层」问出来。

    exe 是 GUI 子系统，出错时**可能弹一个没人能点的对话框**（runner 上没有桌面），
    于是进程永远不退出、stdout/stderr 一个字都没有。光看「超时」分不清是
    「解压慢」「弹窗等人点」还是「代码死循环」，所以这三样一起收：

    - 进程还在不在（在 = 不是崩了，是卡着）
    - `%TEMP%` 下有没有 `_MEI*`（有 = 引导器解压过，问题在解压之后）
    - exe 自己写了什么（GUI 程序正常是空的，非空就是真报错）
    """
    lines = [f"    等了 {elapsed:.0f}s 还没退出（exe={exe}，cwd={ROOT}）",
             f"    进程还活着：{proc.poll() is None}"]
    pids: list[int] = [proc.pid]
    # `/V` 会多一列 **Window Title**。exe 是 GUI 子系统，出错时可能弹一个
    # 没人能点的对话框（runner 上没有桌面），标题就在这一列里。
    try:
        tl = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe.name}", "/V", "/FO", "LIST"],
            capture_output=True, text=True, timeout=30, check=False).stdout.strip()
        csv = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe.name}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=30, check=False).stdout
        for row in re.findall(r'"[^"]*","(\d+)"', csv or ""):
            if int(row) not in pids:
                pids.append(int(row))
    except Exception as exc:                # noqa: BLE001
        tl = f"(tasklist 失败：{exc})"
    lines.append("    tasklist /V：")
    lines.append("\n".join("      " + ln for ln in (tl or "(空)").splitlines()))
    lines.extend(_dialog_report(pids))
    # 解压到哪一步了：`_MEI*` 里文件数 + 总字节。本机实测一份**完整**解压是
    # 104 个文件、约 34MB；数字远小于这个就是**卡在解压**，接近这个就是卡在
    # 解压之后（引导器起子进程 / 弹对话框等人点）。
    try:
        meis = sorted(p for p in Path(tempfile.gettempdir()).glob("_MEI*") if p.is_dir())
        for d in meis[:2]:
            files = [p for p in d.rglob("*") if p.is_file()]
            total = sum(p.stat().st_size for p in files)
            lines.append(f"    {d.name}：{len(files)} 个文件，共 {total / 1e6:.1f} MB")
    except Exception as exc:                # noqa: BLE001
        lines.append(f"    (列 _MEI 失败：{exc})")
    lines.append("    --- exe 的 stdout ---")
    lines.append(out_path.read_bytes().decode("utf-8", "replace") or "(空)")
    lines.append("    --- exe 的 stderr ---")
    lines.append(err_path.read_bytes().decode("utf-8", "replace") or "(空)")
    return "\n".join(lines)


def check_smoke(exe: Path) -> None:
    """`通知.exe --version` 能不能起来。

    **判据是退出码，不是 stdout。** 但别再往下推成「stdout 读不到」——
    那是个把我带沟里 6 轮的错误结论：`console=False` 只保证**双击时不弹黑框**，
    一旦 stdout 被重定向（本函数就是），Python 会照常建一个真的 `sys.stdout`，
    `print()` 的内容**写得进去**（本机实测 `通知.exe --version > out.txt`
    能读到 `AI 任务通知 1.0.0`）。它同时带来了编码问题：那个 stdout 用的是
    系统 ANSI 代码页，英文 Windows 上是 cp1252，中文一 print 就抛。

    所以这里只看退出码——它证明了「Python 运行时解压成功 + 入口模块跑完」；
    stdout/stderr 两份文件只在**失败时**作为证据打出来（见 `_smoke_diag`）。

    ## 为什么**不能**用 `subprocess.run(capture_output=True, timeout=120)`

    2026-10-30 实测：runner 上这一步卡满 `timeout-minutes: 20` 被取消，
    而 PyInstaller 早在第 22 秒就 `Build complete` 了——**超时参数失效了**。

    原因不在 exe，在管道：onefile 的 exe 自己就会派生子进程（引导器 + 应用），
    子进程**继承了 capture_output 建的那两根管道**。`run()` 超时后只 kill 直接
    子进程，然后要再 `communicate()` 一次把管道读干净；那个继承了管道的孙进程
    只要还没退出，这一步就**永远等下去**，`TimeoutExpired` 也就永远轮不到抛。

    本机复现（同一个机制，把孙进程换成睡 12 秒的 python）：
    `run(..., timeout=3)` 实际耗时 **12.7 秒**，超时形同虚设。孙进程不退出
    就是无限等——runner 上正好是这种情况。

    所以改成**重定向到临时文件**：没有管道，就没有「等管道关闭」这一步，
    `wait(timeout=SMOKE_TIMEOUT_S)` 才是真正的墙钟上限。改完之后 runner 上
    如实报出 `✘ 120s 没退出`——管道问题解决了，剩下的是 exe 自己不肯退出。

    ## 最后一层：它不是慢，是死在对话框上

    第 7 轮的诊断把 exe 重定向后的 stderr 打出来了，答案在那里：

        File "app.py", line 562, in main
        File "encodings\\cp1252.py", line 19, in encode
      UnicodeEncodeError: 'charmap' codec can't encode characters in position 3-6

    `console=False` 的 exe 一旦 stdout 被重定向，Python 就按系统 ANSI 代码页
    建一个真的 `sys.stdout`；runner 是英文 Windows → cp1252 → `print("AI 任务通知…")`
    直接抛。异常冒到引导器手里，PyInstaller 弹「Unhandled exception in script」
    等人点确定，runner 上没有桌面 → 永远不退出。**本机 cp936 编码得出来，
    所以本地 6 轮都没复现。** 修在 `app.py` 的 `_harden_stdio` / `_main_guarded`。

    重定向到文件这件事本身也是复现条件，别把它改回 `DEVNULL`：那样即使
    stdout 有问题也看不出来（`DEVNULL` 下 print 成功，只是写进黑洞）。
    """
    print("\n[7/7] 冒烟：--version")
    with tempfile.TemporaryDirectory() as td:
        out_path, err_path = Path(td) / "out.txt", Path(td) / "err.txt"
        with out_path.open("wb") as out_f, err_path.open("wb") as err_f:
            t0 = time.perf_counter()
            proc = subprocess.Popen(
                [str(exe), "--version"],
                cwd=str(ROOT),
                stdout=out_f,
                stderr=err_f,
            )
            try:
                code = proc.wait(timeout=SMOKE_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                elapsed = time.perf_counter() - t0
                diag = _smoke_diag(exe, proc, elapsed, out_path, err_path)
                _kill_tree(proc)
                _die(f"  ✘ {SMOKE_TIMEOUT_S}s 没退出。\n{diag}")
        out = out_path.read_bytes().decode("utf-8", "replace")
        err = err_path.read_bytes().decode("utf-8", "replace")

    if code != 0:
        # 退出码 + 两边捕获到的原文一起打出来：exe 是 GUI 子系统，正常路径上
        # 这两份是空的；一旦有内容，那就是它真正的报错，比退出码有用得多。
        _die(f"  ✘ 退出码 {code}（exe={exe}，cwd={ROOT}）\n"
             f"  --- stdout ---\n{out}\n  --- stderr ---\n{err}")
    print(f"  ✔ 退出码 0（{time.perf_counter() - t0:.1f}s）")


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
    # **异常也必须打到 stdout。** CI 上 stderr 不进日志（见 `_die` 的说明），
    # 不接这一下的话，脚本自己抛异常时日志里同样一个字都没有。
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:                       # noqa: BLE001
        print("\n脚本自己抛异常了，traceback 如下：", flush=True)
        traceback.print_exc(file=sys.stdout)
        sys.exit(1)
