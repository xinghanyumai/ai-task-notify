"""安装器：把 hooks 写进宿主配置，并创建桌面 / 开机自启快捷方式。

用法：
    python install.py install      # 写入 hooks（默认 WorkBuddy + CodeBuddy）
    python install.py shortcut     # 创建桌面快捷方式 + 开机自启
    python install.py all          # 上面两步一起做
    python install.py status       # 查看安装状态
    python install.py uninstall    # 移除 hooks 与快捷方式

设计原则：
- 幂等：重复执行只会替换掉「我们自己的」hook 条目，不碰别人的。
- 安全：改动前自动备份 settings.json。
- 可逆：uninstall 能干净移除。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from wbn import config  # noqa: E402
from wbn.winapi import APP_USER_MODEL_ID  # noqa: E402

HOOK_PATH = ROOT / "hook.py"
# 桌面快捷方式指向的唯一入口。**W5 之前这里是 `ui.py`（tkinter 版）**，
# 那个文件已经删了——不改这一行的后果是：老用户升级后桌面图标还在，
# 双击报「找不到文件」，而且他想不到是这次更新弄坏的。
UI_PATH = ROOT / "app.py"
TRAY_PATH = ROOT / "tray.py"
ICON_PATH = ROOT / "assets" / "icon.ico"

# 需要挂 hook 的事件 -> 超时秒数
HOOK_EVENTS: dict[str, int] = {
    "SessionStart": 10,
    "UserPromptSubmit": 10,
    "Stop": 15,
    "StopFailure": 10,
    "PostToolUseFailure": 10,
    "PermissionRequest": 10,
    "Notification": 10,
    "TaskCompleted": 10,
    "SessionEnd": 10,
}

# 宿主配置位置。第一项是 Windows 上 WorkBuddy 的配置主目录。
TARGETS = [
    ("WorkBuddy", Path.home() / ".workbuddy-ai" / "settings.json"),
    ("CodeBuddy", Path.home() / ".codebuddy" / "settings.json"),
]
LEGACY_TARGET = ("WorkBuddy(legacy)", Path.home() / ".workbuddy" / "settings.json")


# ------------------------------------------------------------------ 基础


def python_exe() -> Path:
    """hook 使用的解释器：优先当前 venv 的 pythonw.exe（不闪黑框）。"""
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return cand if cand.exists() else exe


def hook_command(app: str) -> str:
    """生成 hook 命令行。路径统一用正斜杠并加引号，兼容含空格 / 中文的目录。"""
    if getattr(sys, "frozen", False):
        # 打包后没有 hook.py 这个文件：单 exe 只有一个入口，靠子命令分发。
        # 引号同样不能省——exe 本身也可能被放在含空格的目录里。
        exe = str(sys.executable)
        return f'"{exe}" hook --app {app}'
    py = str(python_exe()).replace("\\", "/")
    hp = str(HOOK_PATH).replace("\\", "/")
    return f'"{py}" "{hp}" --app {app}'


def _norm(p: str) -> str:
    return str(p or "").replace("\\", "/").lower()


def is_our_hook(entry: dict) -> bool:
    """判断某条 hook 配置是不是本程序写入的。"""
    target = _norm(str(HOOK_PATH))
    # 打包后写进宿主的是 `"<exe>" hook --app x`，没有 `hook.py` 这个文件名，
    # 只认 HOOK_PATH 的话 uninstall 会认不出自己写的条目 → 残留一堆指向
    # 已删除 exe 的 hook，宿主每次事件都白等超时。所以两种身份都认。
    exe = _norm(str(sys.executable)) if getattr(sys, "frozen", False) else ""
    for h in entry.get("hooks", []) or []:
        cmd = _norm(str(h.get("command", "")))
        if "hook.py" in cmd and (target in cmd or "wbn" in cmd):
            return True
        if exe and exe in cmd and " hook " in f" {cmd} ":
            return True
    return False


def build_hooks(app: str) -> dict:
    cmd = hook_command(app)
    out = {}
    for ev, timeout in HOOK_EVENTS.items():
        out[ev] = [
            {
                "hooks": [
                    {"type": "command", "command": cmd, "timeout": timeout}
                ]
            }
        ]
    return out


def merge_hooks(existing: dict, app: str) -> dict:
    """把我们的 hooks 合并进已有配置，保留第三方 hook。"""
    ours = build_hooks(app)
    merged = dict(existing or {})
    for ev, entries in ours.items():
        current = merged.get(ev)
        current = current if isinstance(current, list) else []
        kept = [e for e in current if not (isinstance(e, dict) and is_our_hook(e))]
        merged[ev] = kept + entries
    return merged


def strip_hooks(existing: dict) -> dict:
    """移除我们的 hooks，其它原样保留。"""
    out = {}
    for ev, entries in (existing or {}).items():
        if not isinstance(entries, list):
            out[ev] = entries
            continue
        kept = [e for e in entries if not (isinstance(e, dict) and is_our_hook(e))]
        if kept:
            out[ev] = kept
    return out


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        # from None：这是给用户看的 CLI 提示，不需要 Python 再把 JSON 解析器的
        # 原始 traceback 挂在后面
        raise SystemExit(f"✘ 无法解析 {path}：{e}\n  请先手动修好该文件再重试。") from None


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
        print(f"   已备份 → {backup.name}")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ------------------------------------------------------------------ 图标


def ensure_icon() -> Path | None:
    """确保 assets/icon.ico 存在。

    图标由 tools/make_icon.py 统一生成（品牌青绿 + 白铃铛，9 档尺寸），
    这里只负责在文件缺失时补生成一次，不再另画一套，
    避免「快捷方式用一套图标、窗口用另一套」的割裂。
    """
    if ICON_PATH.exists():
        return ICON_PATH
    try:
        # make_icon 住在 tools/，不在 ROOT 上。只在这里补一次路径，
        # 不往全局 sys.path 里塞——安装器的其它部分不需要它。
        tools_dir = str(ROOT / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import make_icon
    except Exception:
        return None
    try:
        make_icon.main()
    except Exception:
        return None
    return ICON_PATH if ICON_PATH.exists() else None


def icon_location() -> str | None:
    """快捷方式 `IconLocation` 的值。

    打包后**不能用 `ensure_icon()` 返回的路径**：那是 `_MEIPASS` 里的
    `assets/icon.ico`，程序一退就被删，快捷方式图标随即变成白纸。
    图标已经用 `icon='assets/icon.ico'` 嵌进 exe 了，直接指 exe 的第 0 号图标。
    """
    if getattr(sys, "frozen", False):
        return f"{sys.executable},0"
    icon = ensure_icon()
    return str(icon) if icon else None


# ------------------------------------------------------------------ AUMID
#
# 任务栏按 AppUserModelID 分组，并据此决定按钮上显示哪个图标。`.lnk` 里没有
# AUMID 时，任务栏用**进程可执行文件**（pythonw.exe）的图标——用户看到一条
# 快捷方式却顶着 Python 图标。
#
# `WScript.Shell` 的 `Shortcut` 对象没有 AppUserModelID 属性，只能走
# `IPropertyStore`。下面这段是跑通过的实现，**五个细节一个都不能改**：
#
# | 细节 | 错了会怎样 |
# |---|---|
# | `shell32.dll` 不是 `propsys.dll` | AttributeError: function ... not found |
# | `VT_LPWSTR = 31` | SetValue 失败，或写进垃圾 |
# | 必须调 `Commit()` | SetValue 返回成功但**磁盘上什么都没变** |
# | vtable 索引 SetValue=6 / Commit=7 / Release=2 | 调错函数，轻则失败重则崩 |
# | 先 `CoInitialize(None)` | 返回 CO_E_NOTINITIALIZED |
#
# 结构体布局沿用验证过的那一版，不要"顺手整理"成更规范的样子——
# PROPVARIANT 的真实布局比这里复杂，能跑通的是这个简化版。

_VT_LPWSTR = 31
_GPS_READWRITE = 2


def _set_lnk_aumid(lnk: Path, app_id: str) -> tuple[bool, str]:
    """把 AppUserModelID 写进 .lnk 的属性存储。返回 (成功, 说明)。"""
    if sys.platform != "win32":
        return False, "非 Windows，跳过"

    # **这 10 个名字必须在函数里导入，不能放模块顶层。**
    # `HRESULT` 是 Windows 的 `ctypes` 才有的名字，顶层一导入，Linux 上
    # `import install` 直接 ImportError——CI 的 ubuntu job 就是这样在**收集阶段**
    # 崩掉的（exit 2，4 个测试模块连用例都没跑到，W9 §2.1）。
    # 它们只在下面这段 COM 代码里用，放这里零成本。
    from ctypes import (  # noqa: PLC0415
        HRESULT,
        POINTER,
        WINFUNCTYPE,
        Structure,
        byref,
        c_ubyte,
        c_ulong,
        c_ushort,
        c_void_p,
        c_wchar_p,
    )

    class GUID(Structure):
        _fields_ = [("Data1", c_ulong), ("Data2", c_ushort), ("Data3", c_ushort),
                    ("Data4", c_ubyte * 8)]

    class PROPERTYKEY(Structure):
        _fields_ = [("fmtid", GUID), ("pid", c_ulong)]

    class PROPVARIANT(Structure):
        _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort),
                    ("r3", c_ushort), ("p", c_void_p), ("p2", c_void_p)]

    iid_property_store = GUID(
        0x886D8EEB, 0x8CF2, 0x4446,
        (c_ubyte * 8)(0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99))
    pkey_app_user_model_id = PROPERTYKEY(
        GUID(0x9F4C2855, 0x9F79, 0x4B39,
             (c_ubyte * 8)(0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3)), 5)

    try:
        ole32 = ctypes.windll.ole32
        shell32 = ctypes.windll.shell32
        ole32.CoInitialize(None)

        # Win7+ 导出在 shell32.dll（Vista 时代在 propsys.dll，已被转发）
        get_store = shell32.SHGetPropertyStoreFromParsingName
        get_store.argtypes = [c_wchar_p, c_void_p, ctypes.c_int,
                              POINTER(GUID), POINTER(c_void_p)]
        get_store.restype = HRESULT

        store = c_void_p()
        hr = get_store(str(lnk), None, _GPS_READWRITE,
                       byref(iid_property_store), byref(store))
        if hr < 0:
            return False, f"SHGetPropertyStoreFromParsingName hr=0x{hr & 0xFFFFFFFF:08X}"

        vtbl = ctypes.cast(store, POINTER(POINTER(c_void_p))).contents
        set_value = WINFUNCTYPE(HRESULT, c_void_p, POINTER(PROPERTYKEY),
                                POINTER(PROPVARIANT))(vtbl[6])
        commit = WINFUNCTYPE(HRESULT, c_void_p)(vtbl[7])
        release = WINFUNCTYPE(c_ulong, c_void_p)(vtbl[2])

        pv = PROPVARIANT()
        pv.vt = _VT_LPWSTR
        pv.p = ctypes.cast(c_wchar_p(app_id), c_void_p)
        hr = set_value(store, byref(pkey_app_user_model_id), byref(pv))
        if hr < 0:
            release(store)
            return False, f"SetValue hr=0x{hr & 0xFFFFFFFF:08X}"
        # 不 Commit 的话 SetValue 返回成功、磁盘上什么都没变——最坏的一种失败：
        # 报成功，但用户看到的还是 Python 图标。
        hr = commit(store)
        release(store)
        if hr < 0:
            return False, f"Commit hr=0x{hr & 0xFFFFFFFF:08X}"
        return True, "OK"
    except Exception as exc:                      # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


# ------------------------------------------------------------------ 快捷方式


def _ps(script: str) -> str:
    """执行一段 PowerShell，返回 stdout。"""
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=0x08000000,
        )
        return (proc.stdout or "").strip()
    except Exception as e:
        return f"__ERR__{e}"


def _desktop_dir() -> Path:
    out = _ps("[Environment]::GetFolderPath('Desktop')")
    if out and not out.startswith("__ERR__"):
        return Path(out)
    return Path.home() / "Desktop"


def _shell_folder(csidl: int) -> Path | None:
    """用 `shell32.SHGetFolderPathW` 取一个系统已知文件夹。取不到返回 `None`。

    **为什么不走 `_ps("[Environment]::GetFolderPath(...)")`**：起一次
    PowerShell 实测 **1.28 秒**（2026-10-30 本机）。状态页每次加载都要读一次
    自启状态，那等于给首屏加一秒多空白；托盘菜单每展开一次也要读。
    这个 API 是进程内调用，耗时约 0.1ms，而且是同一个已知文件夹定义。
    """
    if sys.platform != "win32":
        return None
    try:
        buf = ctypes.create_unicode_buffer(260)          # MAX_PATH
        # SHGFP_TYPE_CURRENT = 0：要「当前」路径，不是默认路径
        hr = ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
        if hr != 0 or not buf.value:
            return None
        return Path(buf.value)
    except Exception:                                    # noqa: BLE001
        # 取不到只是慢一点 / 退回环境变量，不该让安装器或界面崩掉
        return None


def _startup_dir() -> Path:
    p = _shell_folder(0x07)                              # CSIDL_STARTUP
    if p is not None:
        return p
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft/Windows/Start Menu/Programs/Startup"


def _psq(text: str) -> str:
    """包成 PowerShell 单引号字符串。

    单引号串里唯一的转义是「两个单引号表示一个单引号」。
    Windows 文件名允许出现单引号，不转义的话
    `D:\\it's here\\` 这样的路径会直接把脚本截断。
    """
    return "'" + str(text).replace("'", "''") + "'"


def _psdq(text: str) -> str:
    """包成 PowerShell 双引号字符串。

    双引号串里 ` $ 都是元字符，必须用反引号转义。
    Windows 文件名不允许 "，但路径里可能有 $（如 C:\\$Recycle.Bin），
    所以 $ 这一条是实打实需要的。
    """
    esc = str(text).replace("`", "``").replace('"', '`"').replace("$", "`$")
    return f'"{esc}"'


def make_shortcut(
    lnk: Path,
    script: Path,
    args: str = "",
    desc: str = "",
    window_style: int = 1,
) -> bool:
    """用 WScript.Shell 创建 .lnk。window_style: 1 正常 / 7 最小化。"""
    icon = icon_location()
    if getattr(sys, "frozen", False):
        # 打包后 exe 自己就是入口：TargetPath 指 exe，Arguments 只放子命令。
        # 不能再把脚本路径塞进去——exe 里没有 `app.py` 这个文件，
        # 塞进去的症状和 W7.0 那个 P0 一样：双击毫无反应。
        py = str(sys.executable)
        arg_line = args
    else:
        py = str(python_exe())
        # **必须加双引号。** 本项目路径含空格（`D:\Code\Work buddy 通知`），
        # 而 Windows 把 Arguments 拼在 TargetPath 后面时不做任何转义：裸路径会被
        # 拆成 `D:\Code\Work` + `buddy` + `通知\app.py`，Python 拿第一段当脚本路径
        # → 文件不存在 → pythonw.exe 静默退出（无控制台、无 stderr）。
        # 用户看到的就是「双击图标毫无反应」。同一个文件里 hook_command() 一直是
        # 加引号的，两处不一致就是 W7.0 那个 P0。
        arg_line = f'"{script}"'
        if args:
            # args 是 `--silent` 这类不带空格的开关，裸写才对；
            # 写成 `f'"{script}" {args}'` 之外再包一层引号会把开关吞进路径里。
            arg_line += f" {args}"

    ps = (
        "$ws = New-Object -ComObject WScript.Shell;"
        f"$s = $ws.CreateShortcut({_psdq(lnk)});"
        f"$s.TargetPath = {_psdq(py)};"
        f"$s.Arguments = {_psq(arg_line)};"
        # 工作目录同样要跟着形态走：frozen 时是 exe 旁边（可写、持久），
        # 源码时是仓库根。指 `_MEIPASS` 会让子进程的工作目录随退出消失。
        f"$s.WorkingDirectory = {_psdq(config.ROOT)};"
        f"$s.Description = {_psdq(desc or 'AI 任务通知')};"
        + (f"$s.IconLocation = {_psdq(icon)};" if icon else "")
        + f"$s.WindowStyle = {int(window_style)};"
        "$s.Save();"
        'Write-Output "OK"'
    )
    out = _ps(ps)
    ok = out.strip().endswith("OK") and lnk.exists()
    if ok:
        # 写 AUMID 失败**不算致命**：任务栏图标会退回 Python 图标，但程序能跑。
        # 「能启动」是根本，「图标好看」是锦上添花——不许因为后者 return False。
        wrote, why = _set_lnk_aumid(lnk, APP_USER_MODEL_ID)
        if not wrote:
            print(f"  ! 快捷方式已建，但任务栏图标关联失败（会显示成 Python 图标）：{why}")
    return ok


# ------------------------------------------------------------------ 开机自启
#
# **自启只有一个真相：启动文件夹里那个 .lnk 在不在。**
# 不读注册表（`Run` 键是另一条路，本项目从来没走过），也不读 `config.json`——
# 往 config 里塞一个 `autostart: true` 就会造出「配置说开、实际没开」的第二真相，
# 那正是本项目一路在躲的东西（W9 §1.2 第 3 条）。

AUTOSTART_NAME = "AI 任务通知.lnk"


def _autostart_probe() -> tuple[Path | None, str]:
    """(那个 .lnk 的路径 | `None`, 读不到的原因)。

    只负责「路径能不能确定」，**不负责「目录在不在」**：`create_shortcuts`
    在测试里被 monkeypatch 成临时目录时目录本来就不存在，那不该算「读不到」，
    否则 `install all` 的既有行为会被改掉。
    """
    if sys.platform != "win32":
        return None, "非 Windows 系统，没有启动文件夹"
    try:
        return _startup_dir() / AUTOSTART_NAME, ""
    except OSError as exc:
        return None, f"读启动目录失败：{exc}"


def autostart_path() -> Path:
    """启动文件夹里那个 .lnk 的完整路径。"""
    return _startup_dir() / AUTOSTART_NAME


def autostart_state() -> tuple[bool | None, str]:
    """三态：`True` 开 / `False` 关 / `None` **读不到启动目录**。

    为什么要三态：读不到时画成「已关闭」是在替系统撒谎——用户会以为自己
    关过它，然后去别处找开关。界面上必须说「无法读取启动目录」。
    """
    lnk, why = _autostart_probe()
    if lnk is None:
        return None, why
    if not lnk.parent.is_dir():
        return None, f"启动目录不存在：{lnk.parent}"
    try:
        return lnk.exists(), ""
    except OSError as exc:
        return None, f"读快捷方式失败：{exc}"


def autostart_enabled() -> bool:
    """开机自启是否开着。**只认文件系统事实。**"""
    state, _ = autostart_state()
    return state is True


def autostart_report() -> dict:
    """给界面的一次性快照：`{enabled, path, ok, msg}`。

    `ok=False` 有三种来源：非 Windows、启动目录不存在、读不了那个文件。
    界面据此显示「无法读取启动目录」而不是「已关闭」。
    """
    lnk, why = _autostart_probe()
    if lnk is None:
        return {"enabled": False, "path": str(autostart_path()),
                "ok": False, "msg": why}
    if not lnk.parent.is_dir():
        return {"enabled": False, "path": str(lnk),
                "ok": False, "msg": f"启动目录不存在：{lnk.parent}"}
    try:
        on = lnk.exists()
    except OSError as exc:
        return {"enabled": False, "path": str(lnk),
                "ok": False, "msg": f"读快捷方式失败：{exc}"}
    return {"enabled": on, "path": str(lnk), "ok": True, "msg": ""}


def _autostart_target() -> tuple[Path, str]:
    """自启快捷方式指向什么。

    两种形态的差异和 `create_shortcuts` 原来那段一模一样，抽出来给
    `set_autostart` 复用——**不许写第二份**（写两份就是「同一件事两个写法」，
    这个项目的老病）。
    """
    if getattr(sys, "frozen", False):
        # 单 exe 只有一个入口，托盘靠子命令而不是 `tray.py` 这个文件。
        return UI_PATH, "tray --silent"
    return (TRAY_PATH if TRAY_PATH.exists() else UI_PATH), "--silent"


def set_autostart(on: bool) -> tuple[bool, str]:
    """开 / 关开机自启，返回 (成功?, 人话说明)。

    **删之前先 `exists()`，文件本来就不在不算失败**——用户点「关」时它可能
    早被手动删了，这时报错只会让人以为坏了。
    """
    lnk, why = _autostart_probe()
    if lnk is None:
        return False, why

    if not on:
        if not lnk.exists():
            return True, "已经是关闭状态"
        try:
            lnk.unlink()
        except OSError as exc:
            # **接 `OSError` 而不是 `PermissionError`**：Windows 删文件失败
            # （被占用 / 只读 / 回收站机制）抛的经常是裸 `OSError`，
            # 只接 `PermissionError` 会让异常直接穿出去。这个坑本项目踩过。
            return False, f"删除失败：{exc}"
        if lnk.exists():
            return False, "删除后文件仍在"
        return True, "已关闭"

    target, args = _autostart_target()
    if not make_shortcut(lnk, target, args, "AI 任务通知 · 开机自启", window_style=7):
        return False, "创建快捷方式失败（WScript.Shell 没有返回 OK）"
    return True, "已开启"


def create_shortcuts(with_autostart: bool = True) -> None:
    print("\n[创建快捷方式]")
    desktop = _desktop_dir()
    ok1 = make_shortcut(
        desktop / "AI 任务通知.lnk",
        UI_PATH,
        "",
        "AI 任务通知 · 设置中心",
        window_style=1,
    )
    print(f"  {'✔' if ok1 else '✘'} 桌面：{desktop / 'AI 任务通知.lnk'}")

    if with_autostart:
        # 走 `set_autostart` 而不是在这里再拼一遍：桌面和自启曾经写在一起，
        # 结果界面上没法单独开关（W9 的起因）。现在只有一条实现路径。
        ok2, why = set_autostart(True)
        print(f"  {'✔' if ok2 else '✘'} 开机自启：{autostart_path()}")
        if ok2:
            print("     （托盘常驻，可在托盘菜单里暂停 / 测试 / 打开设置）")
        else:
            print(f"     {why}")


def remove_shortcuts() -> None:
    print("\n[移除快捷方式]")
    for d in (_desktop_dir(), _startup_dir()):
        p = d / "AI 任务通知.lnk"
        try:
            if p.exists():
                p.unlink()
                print(f"  ✔ 已删除 {p}")
        except OSError as e:
            print(f"  ✘ 删除失败 {p}: {e}")


# ------------------------------------------------------------------ 主流程


def install(also_legacy: bool = False) -> None:
    config.ensure_dirs()
    created = config.save_default_if_missing()
    if created:
        print(f"✔ 已生成默认配置 {config.CONFIG_PATH}")

    targets = list(TARGETS)
    if also_legacy:
        targets.append(LEGACY_TARGET)

    print("\n[写入 hooks]")
    for app, path in targets:
        if not path.parent.exists():
            print(f"  · 跳过 {app}（未安装：{path.parent}）")
            continue
        try:
            data = read_json(path)
            data["hooks"] = merge_hooks(data.get("hooks", {}), app)
            write_json(path, data)
            print(f"  ✔ {app}: {path}")
        except SystemExit:
            raise
        except Exception as e:
            print(f"  ✘ {app} 写入失败: {e}")

    print(
        "\n⚠ 重要：hooks 配置在宿主启动时读取并缓存，"
        "\n   必须【完全退出并重启】WorkBuddy / CodeBuddy 才会生效。"
        "\n   注意 WorkBuddy 点窗口关闭按钮只是隐藏到托盘，"
        "\n   要在托盘图标上右键 → 退出，或用任务管理器结束进程。"
    )


def uninstall() -> None:
    print("[移除 hooks]")
    for app, path in TARGETS + [LEGACY_TARGET]:
        if not path.exists():
            continue
        try:
            data = read_json(path)
            if "hooks" in data:
                data["hooks"] = strip_hooks(data.get("hooks", {}))
                if not data["hooks"]:
                    data.pop("hooks", None)
                write_json(path, data)
                print(f"  ✔ {app}: 已清理 {path}")
        except Exception as e:
            print(f"  ✘ {app}: {e}")
    remove_shortcuts()
    print("\n完成。配置文件与日志保留在项目目录，可手动删除。")


def hooks_status() -> list[str]:
    """返回各宿主的 hooks 安装状态描述，供 selftest / UI 使用。"""
    lines = []
    for app, path in TARGETS:
        if not path.exists():
            lines.append(f"· {app:10s} 未安装（{path.parent} 不存在）")
            continue
        try:
            data = read_json(path)
        except SystemExit as e:
            lines.append(f"✘ {app:10s} 配置损坏：{e}")
            continue
        hooks = data.get("hooks", {}) or {}
        installed = [ev for ev, entries in hooks.items() if any(
            isinstance(e, dict) and is_our_hook(e) for e in (entries or [])
        )]
        if installed:
            lines.append(
                f"✔ {app:10s} 已挂载 {len(installed)} 个事件（{', '.join(sorted(installed)[:4])}…）"
            )
        else:
            lines.append(f"· {app:10s} 未安装 hooks")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 任务通知 · 安装器")
    ap.add_argument(
        "action",
        nargs="?",
        default="all",
        choices=["install", "shortcut", "all", "uninstall", "status"],
    )
    ap.add_argument("--no-autostart", action="store_true", help="不创建开机自启")
    ap.add_argument("--also-legacy", action="store_true", help="同时写入 ~/.workbuddy")
    args = ap.parse_args()

    if args.action == "status":
        from selftest import show_status

        show_status()
        return 0

    if args.action == "uninstall":
        uninstall()
        return 0

    if args.action in ("install", "all"):
        install(also_legacy=args.also_legacy)
    if args.action in ("shortcut", "all"):
        create_shortcuts(with_autostart=not args.no_autostart)

    if args.action == "all":
        print("\n[安装后自检]")
        for line in hooks_status():
            print("  " + line)
        print("\n下一步：双击桌面「AI 任务通知」，选一个手机通道并点『发送测试』。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
