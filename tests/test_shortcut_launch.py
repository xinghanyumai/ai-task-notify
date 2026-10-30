"""桌面快捷方式能不能真的启动 —— 三条守卫。

## 为什么要有这个文件

用户报「双击桌面图标打不开」。真因不是 `UI_PATH` 指错（那是上一轮的误判），
而是 `make_shortcut()` 里这一行：

```python
arg_line = str(script)          # ← 裸路径，没有双引号
```

脚本路径是 `D:\\Code\\Work buddy 通知\\app.py`，含两个空格。Windows 把
`Arguments` 拼在 `TargetPath` 后面时**不做任何转义**，于是 Python 收到
`['pythonw.exe', 'D:\\\\Code\\\\Work', 'buddy', '通知\\\\app.py']`，把第一个当脚本路径
→ 文件不存在 → `pythonw.exe` 静默退出（无控制台、无 stderr），用户看到「毫无反应」。

**同一个文件里 `hook_command()` 是加引号的。** 一处加一处不加，就是这个项目
反复出现的「同一件事两个写法」。

## 判据的选法

三条都不用「看字符串长什么样」，而用**Windows 自己的解析器**
（`shell32.CommandLineToArgvW`）去切。`assert args.startswith('"')` 那种写法
连「裸路径但没有空格」都拦不住，测的是长相不是行为。

第 2 条故意用 `python.exe` 起、不用 `pythonw.exe`：`pythonw` 没有 stderr，
错了也读不到原因。同一条命令行、同一个解析路径，只有解释器不同——
这是唯一能拿到「can't open file」证据的办法。
"""

from __future__ import annotations

import ast
import ctypes
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import install  # noqa: E402  （要在 ROOT 进 sys.path 之后）

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason=".lnk 与 CommandLineToArgvW 只在 Windows 上存在，其它平台无法验证这件事",
)


# ---------------------------------------------------------------- 读 .lnk


def _lnk_strings(lnk: Path) -> dict[str, str]:
    """按 MS-SHLLINK 解析 .lnk 的 StringData 段，取回真实写进磁盘的字符串。

    不走 WScript.Shell 回读：那读的是「COM 认为它写了什么」，
    这里要的是「磁盘上到底是什么」。两者在编码/截断出错时会不一样。
    """
    raw = lnk.read_bytes()
    assert len(raw) >= 0x4C, f"{lnk} 太小，不是合法 .lnk（{len(raw)} 字节）"
    hdr = struct.unpack_from("<I", raw, 0)[0]
    flags = struct.unpack_from("<I", raw, 0x14)[0]
    off = hdr

    if flags & 0x01:                                  # HasLinkTargetIDList
        idl = struct.unpack_from("<H", raw, off)[0]
        off += 2 + idl
    if flags & 0x02:                                  # HasLinkInfo
        off += struct.unpack_from("<I", raw, off)[0]

    uni = bool(flags & 0x80)
    out: dict[str, str] = {}
    order = ((0x04, "NAME"), (0x08, "RELATIVE"), (0x10, "WORKDIR"),
             (0x20, "ARGUMENTS"), (0x40, "ICON"))
    for bit, name in order:
        if not flags & bit:
            continue
        count = struct.unpack_from("<H", raw, off)[0]
        off += 2
        nbytes = count * 2 if uni else count
        enc = "utf-16-le" if uni else "mbcs"
        out[name] = raw[off:off + nbytes].decode(enc, "replace")
        off += nbytes
    return out


def _argv(cmdline: str) -> list[str]:
    """交给 Windows 自己的解析器切参数，不自己写 split。"""
    shell32 = ctypes.windll.shell32
    shell32.CommandLineToArgvW.argtypes = [
        ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argc = ctypes.c_int()
    buf = shell32.CommandLineToArgvW(cmdline, ctypes.byref(argc))
    try:
        return [buf[i] for i in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(buf)


def _spaced_dir(tmp_path: Path) -> Path:
    """造一个含空格的目录。

    **这是这个文件成立的前提**：路径没有空格的话，缺引号的 bug 在测试里
    永远复现不出来——CI 的 checkout 目录是 `D:\\a\\repo\\repo`，一个空格都没有。
    """
    d = tmp_path / "with space"
    d.mkdir(parents=True)
    return d


# ---------------------------------------------------------------- 第 1 条


def test_shortcut_arguments_parse_to_one_existing_path(tmp_path):
    """把 .lnk 的 Arguments 交给 CommandLineToArgvW，断言切出的第一个参数
    就是完整的脚本路径，且文件存在。

    不写 `assert args.startswith('"')` —— 那是「看长相」，改成裸路径但没空格
    也能通过。这里测的是「Windows 会怎么理解」，和真实启动同一把尺子。
    """
    # (a) 假的脚本，放在含空格的目录里：这一半保证「缺引号」在**任何**
    #     机器上都复现得出来，不依赖本项目放在哪。
    d = _spaced_dir(tmp_path)
    fake = d / "app.py"
    fake.write_text("# 假脚本，只为让路径存在\n", encoding="utf-8")
    lnk = d / "探针.lnk"
    assert install.make_shortcut(lnk, fake, "", "probe") is True, "造 .lnk 失败"

    args = _lnk_strings(lnk)["ARGUMENTS"]
    argv = _argv(args)
    assert len(argv) == 1, (
        f"Arguments 被 Windows 切成了 {len(argv)} 段，不是 1 段：{argv}\n"
        f"原始 Arguments = {args!r}")
    assert Path(argv[0]) == fake, f"第一段不是完整脚本路径：{argv[0]!r}"
    assert Path(argv[0]).exists()

    # (b) 真的入口：这一半保证 UI_PATH 指着的东西存在（M3 变异靠它）。
    #     真入口路径本身含空格（`D:\Code\Work buddy 通知`），所以同时也
    #     再验一次引号。
    real = _spaced_dir(tmp_path / "real")
    lnk2 = real / "真入口.lnk"
    assert install.make_shortcut(lnk2, install.UI_PATH, "", "probe") is True
    argv2 = _argv(_lnk_strings(lnk2)["ARGUMENTS"])
    assert len(argv2) == 1, f"真入口的 Arguments 也被切碎了：{argv2}"
    assert Path(argv2[0]) == install.UI_PATH, (
        f"UI_PATH = {install.UI_PATH}，但 .lnk 里切出来的是 {argv2[0]!r}")
    assert install.UI_PATH.exists(), (
        f"UI_PATH 指向的文件不存在：{install.UI_PATH}\n"
        "桌面图标双击必然报「找不到文件」——W5 删 ui.py 时没改这一行就是这种病。")


# ---------------------------------------------------------------- 第 2 条


@pytest.mark.slow
def test_shortcut_actually_launches(tmp_path):
    """用 .lnk 里真实存的那串 Arguments 起一次进程，断言它真的跑起来了。

    慢（约 0.5 秒），但这是唯一能证明「用户双击能开」的测试。
    红了就是用户打不开。

    用 `python.exe` 而不是 `.lnk` 里的 `pythonw.exe`：**同一条命令行、同一个
    解析路径**，只是解释器换成了有 stderr 的那个。`pythonw` 没有 stderr，
    错了也读不到「can't open file」，那就只剩下「进程消失了」这一个信号。
    """
    d = _spaced_dir(tmp_path)
    script = d / "app.py"
    script.write_text('import sys\nsys.stdout.write("LAUNCH_OK")\n', encoding="utf-8")
    lnk = d / "launch.lnk"
    assert install.make_shortcut(lnk, script, "", "launch") is True

    args = _lnk_strings(lnk)["ARGUMENTS"]
    # ShellExecute 干的事就是「TargetPath + 空格 + Arguments」拼成命令行，
    # 交给 CreateProcess。这里原样复现，不自己加引号。
    cmdline = f'"{install.python_exe()}" {args}'
    proc = subprocess.run(
        cmdline, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30)

    assert "can't open file" not in (proc.stderr or "").lower(), (
        f"Python 把参数切碎了，找不到脚本：\n命令行 = {cmdline}\n{proc.stderr[:500]}")
    assert proc.returncode == 0, (proc.returncode, (proc.stderr or "")[:500])
    assert "LAUNCH_OK" in (proc.stdout or ""), (
        f"脚本没跑到最后一行，stdout = {proc.stdout!r}")


# ---------------------------------------------------------------- 第 3 条

# 表示「文件系统路径」的变量名。列在这里而不是「凡是占位符都要加引号」，
# 是因为 `args`（`--silent`）**必须**裸写——给它加引号会变成脚本路径的一部分。
_PATH_NAMES = {"py", "hp", "script", "lnk", "icon", "target"}


def _is_path_expr(node: ast.AST) -> bool:
    """占位符里放的是不是「文件系统路径」。

    只认**裸变量名**和两个生成路径的调用。刻意不认 `a.B_PATH` 这种属性访问：
    `config.CONFIG_PATH` 出现在一堆 print 里（"已生成默认配置 {config.CONFIG_PATH}"），
    那是给人看的提示，不是命令行；把它算进来会逼出豁免名单，而豁免名单
    正是这条守卫要防的东西。两处拼接用的都是裸名（`py` / `hp` / `script`）。
    """
    if isinstance(node, ast.Name):
        return node.id in _PATH_NAMES or node.id.endswith("_PATH")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in ("python_exe", "ensure_icon")
    return False


def _unquoted_path_placeholders(src: str) -> list[str]:
    """扫出「f-string 里拼了路径但两侧没引号」的占位符。"""
    bad = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.JoinedStr):
            continue
        vals = node.values
        for i, v in enumerate(vals):
            if not isinstance(v, ast.FormattedValue) or not _is_path_expr(v.value):
                continue
            prev = vals[i - 1].value if i > 0 and isinstance(vals[i - 1], ast.Constant) else ""
            nxt = vals[i + 1].value if i + 1 < len(vals) and isinstance(
                vals[i + 1], ast.Constant) else ""
            quoted = str(prev).endswith('"') and str(nxt).startswith('"')
            if not quoted:
                name = getattr(v.value, "id", None) or ast.unparse(v.value)
                bad.append(f"install.py:{v.lineno}: {{{name}}} 两侧没双引号 "
                           f"(前={str(prev)[-12:]!r} 后={str(nxt)[:12]!r})")
    return bad


def test_command_line_builders_quote_paths_the_same_way():
    """两处命令行拼接必须用同一种写法：路径一律加双引号。

    这条防的是「改一处忘一处」——`hook_command` 一直是对的，
    `make_shortcut` 一直错，因为没人把这两处放在一起看过。

    两把尺子：
    - **行为**：`hook_command()` 的产出交给 CommandLineToArgvW，切出来必须
      正好是 [解释器, hook.py, --app, 宿主名]。缺引号时 `hook.py` 会被空格
      切开（本项目路径含空格），当场红。
    - **结构**：AST 扫所有 f-string，凡是拼了路径的占位符，两侧都必须有引号。
      这一半管「将来新加的命令行拼接」——行为那半只覆盖已知的两处。
    """
    src = (ROOT / "install.py").read_text(encoding="utf-8")

    # 行为
    argv = _argv(install.hook_command("WorkBuddy"))
    assert Path(argv[0]) == install.python_exe(), f"hook 解释器被切碎：{argv[0]!r}"
    assert Path(argv[1]) == install.HOOK_PATH, (
        f"hook 脚本路径被切碎：{argv[1]!r}（应为 {install.HOOK_PATH}）")
    assert install.HOOK_PATH.exists()
    assert argv[2:] == ["--app", "WorkBuddy"], f"尾部参数不对：{argv[2:]}"

    # 结构
    bad = _unquoted_path_placeholders(src)
    assert not bad, "这些命令行拼接里的路径没加双引号：\n" + "\n".join(bad)


# 第 3 条的 AST 一半要有自检：扫描器扫不出东西的守卫是装饰。
def test_ast_scanner_bites_on_a_known_bad_snippet():
    bad = 'x = f"{script} --silent"\n'
    assert _unquoted_path_placeholders(bad), "扫描器连裸路径都扫不出来，等于没写"
    good = 'x = f\'"{script}" --silent\'\n'
    assert not _unquoted_path_placeholders(good), "扫描器把正确写法也报了，会逼人加豁免"
