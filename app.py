"""唯一入口：`python app.py [子命令]`，将来也是 exe 的唯一入口。

通知链路不经过这里。`hook` / `dispatch` 只 import 各自的模块——UI 崩溃、
pywebview 没装、WebView2 缺失，都不影响发通知。所以本文件**只 import 标准库**，
`webview` 到真正要开窗那一行才导入。

    子命令        做什么                            交给谁
    ui（默认）    开设置窗口（pywebview）           本文件
    tray          托盘常驻                          tray.main()
    hook          宿主事件钩子（stdin 读 payload）   hook.main()
    dispatch      后台发通知（hook 派生的子进程）    dispatch.main()
    install       写 hooks / 建快捷方式              install.main()
    selftest      自检与诊断                        selftest.main()

    python app.py                            # = ui
    python app.py selftest --status
    python app.py install all --no-autostart
    python app.py hook --app WorkBuddy < payload.json
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WIN_TITLE = "AI 任务通知"
SUBCOMMANDS = ("ui", "tray", "hook", "dispatch", "install", "selftest")

# 窗口开出来之后，前端多少秒内必须调通一次 bridge。正常机器上 < 1s。
# 超时说明 WebView2 只是"建了窗口但没渲染"，见 run_ui() 里的说明。
UI_ALIVE_TIMEOUT = 10.0

# `Local\` 不是 `Global\`：Local 是「每个登录会话一个」，正是我们要的粒度。
# 全局命名跨用户生效，而 focus_existing() 用的 FindWindowW 是会话内查找——
# 换成 Global 的后果是「另一个登录用户点开程序，什么都不会发生」，
# 比开出两个窗口更糟。见 W1 汇报里的偏差说明。
MUTEX_NAME = "Local\\WBN_UI_SINGLETON"
ERROR_ALREADY_EXISTS = 183

# WebView2 运行时在注册表里的三个可能位置（HKLM 32/64 位 + HKCU 用户级安装）
WEBVIEW2_KEYS = [
    (r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
     r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", "HKEY_LOCAL_MACHINE"),
    (r"SOFTWARE\Microsoft\EdgeUpdate\Clients"
     r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", "HKEY_LOCAL_MACHINE"),
    (r"SOFTWARE\Microsoft\EdgeUpdate\Clients"
     r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}", "HKEY_CURRENT_USER"),
]

_mutex = None      # 必须存全局：句柄一丢，锁就没了


# ------------------------------------------------------------------ 单实例


def acquire_single_instance() -> bool:
    """拿不到锁说明已有一个实例在跑，返回 False。

    任何异常/失败都返回 True（放行）。宁可开两个窗口，也不能因为探测失败
    让用户彻底打不开设置——单实例是体验优化，不是安全边界。
    """
    global _mutex
    if sys.platform != "win32":
        return True
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        k32.CreateMutexW.restype = ctypes.c_void_p
        _mutex = k32.CreateMutexW(None, 0, MUTEX_NAME)
        if not _mutex:
            return True
        return ctypes.get_last_error() != ERROR_ALREADY_EXISTS
    except Exception:
        return True


def focus_existing() -> bool:
    """把已经开着的窗口拉到前台。窗口还没建好时返回 False。"""
    if sys.platform != "win32":
        return False
    try:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        u32.FindWindowW.restype = ctypes.c_void_p
        hwnd = u32.FindWindowW(None, WIN_TITLE)
        if not hwnd:
            return False
        u32.ShowWindow(hwnd, 9)          # SW_RESTORE：最小化的也能拉回来
        u32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


# ------------------------------------------------------- WebView2 与降级


def has_webview2() -> bool:
    """注册表探测。探不到不等于一定没有（便携版可能不写注册表），
    所以它只是「提前判断」，最终仍以 webview.start() 抛不抛异常为准。"""
    if sys.platform != "win32":
        return False
    import winreg

    for path, hive in WEBVIEW2_KEYS:
        try:
            with winreg.OpenKey(getattr(winreg, hive), path) as key:
                version = str(winreg.QueryValueEx(key, "pv")[0] or "")
            if version and version != "0.0.0.0":
                return True
        except OSError:
            continue
    return False


def fallback_to_tkinter() -> None:
    """W1 阶段先指向旧的 ui.py（方便并排对照）；W5 删 ui.py 时改成原生提示框。
    见 DECISIONS D1——最终不保留 legacy，退路靠 git tag `tkinter-final`。"""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    subprocess.Popen(
        [str(pyw if pyw.exists() else exe), str(ROOT / "ui.py")],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(ROOT),
        creationflags=0x08000000 if sys.platform == "win32" else 0,
    )


# ------------------------------------------------------------------ 开窗


def run_ui() -> int:
    if not acquire_single_instance():
        focus_existing()
        return 0
    if not has_webview2():
        fallback_to_tkinter()
        return 0

    try:
        import webview

        from wbn.bridge import Bridge
        from wbn.theme import set_app_user_model_id
    except Exception:
        fallback_to_tkinter()
        return 0

    # 任务栏按 AppUserModelID 分组，不设的话按钮上是 Python 的图标
    set_app_user_model_id()

    # **Chromium 的沙箱在本机起不来**：浏览器进程静默退出，窗口开出来是一整块
    # 黑，而 pywebview 的初始化回调照样报 IsSuccess=True、前端也照样能调通
    # bridge——所以 Python 侧看不出任何异常（排查过程见 architecture.md 的
    # 「已知问题」）。加 --no-sandbox 才能画出来。
    #
    # 关沙箱的代价：这个窗口只加载本地 webui/index.html，不加载任何远端页面，
    # 实际暴露面很小。**一旦以后要在这个窗口里打开外部网页，必须重新评估**。
    # 想恢复沙箱：设环境变量 WBN_WEBVIEW_SANDBOX=1。
    if os.environ.get("WBN_WEBVIEW_SANDBOX") != "1":
        os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--no-sandbox")

    alive = threading.Event()
    api = Bridge(on_activity=alive.set)
    index = (ROOT / "webui" / "index.html").as_uri()   # 中文/空格会被 percent-encode
    win = webview.create_window(
        WIN_TITLE, index,
        js_api=api,
        width=1180, height=780, min_size=(880, 600),
        background_color="#fafafa",
    )
    if win is None:
        fallback_to_tkinter()
        return 0

    # 通道页的异步测试要把结果推回页面，需要窗口句柄。建窗口时还没有它，
    # 所以建完再补一次——bridge 里不 import webview，保持纯 Python 可测。
    # **必须走 `_attach()`**：挂成公开属性 `api.window` 会让 pywebview 的
    # js_api 枚举递归展开 Window 对象，API 注入直接失败（详见 Bridge 类注释）。
    api._attach(win)

    # 关窗拦截：前端有未保存的改动时先问一句。
    #
    # **用 pywebview 自己的 `create_confirmation_dialog`**，不自绘：原生对话框
    # 才有正确的键盘行为（Esc = 取消、Enter = 默认按钮）和读屏支持，自绘一个
    # 只会得到「能点但不会用」的东西。
    #
    # 返回值语义和直觉相反，别改反了：pywebview 的 `Event.set()` 把
    # **返回 False 当作「取消关闭」**（见 `event.py` 的 `false_values`，
    # winforms 后端据此 `args.Cancel = True`）。所以这里返回的必须是
    # 「允许关闭」，正好等于对话框的返回值（True = 点了确定）。
    def _on_closing() -> bool:
        if not api._unsaved:
            return True
        try:
            return bool(win.create_confirmation_dialog(
                "关闭", "有未保存的改动，确定关闭？"))
        except Exception as e:            # 对话框弹不出来，不能让窗口关不掉
            _log(f"[app] 关窗确认框没弹出来，直接放行：{e}")
            return True

    win.events.closing += _on_closing

    # 存活探针。`has_webview2()` 探到运行时、`create_window` 也没抛异常，窗口
    # 照样可能是**一块黑**：WebView2 初始化回调报成功，控制器随后失效，页面
    # 一次都没画出来（本机 2026-10 实测，MSHTML 后端同代码正常）。
    # 唯一可信的判据是「前端到底调通没调通 bridge」，所以拿 config_get 当心跳。
    def _watchdog() -> None:
        if alive.wait(UI_ALIVE_TIMEOUT):
            return
        _log(
            f"WebView2 窗口 {UI_ALIVE_TIMEOUT:.0f}s 内没有调通 bridge，"
            "判定 UI 未启动，降级到 tkinter"
        )
        fallback_to_tkinter()
        for w in list(getattr(webview, "windows", [])):
            try:
                w.destroy()
            except Exception:
                pass

    threading.Thread(target=_watchdog, daemon=True).start()

    try:
        webview.start(debug=os.environ.get("WBN_DEBUG") == "1")
    except Exception:
        fallback_to_tkinter()
    return 0


def _log(msg: str) -> None:
    """往 logs/ 落一条。UI 起不来时用户看不到控制台，只能靠这个查。"""
    try:
        from wbn import store

        store.log(f"[app] {msg}")
    except Exception:
        pass


# -------------------------------------------------------------- 子命令分发


def _delegate(cmd: str, rest: list[str]) -> int:
    """把 sys.argv 改写成「被调模块自己的视角」，再调它的 main()。

    hook / install / selftest 的 main() 直接读 sys.argv 走 argparse，
    不改写的话 `app.py selftest --status` 会被当成两个位置参数而报错。
    """
    import importlib

    mod = importlib.import_module(cmd)
    saved, sys.argv = sys.argv, [cmd] + rest
    try:
        return mod.main(rest) if cmd == "dispatch" else mod.main()
    except SystemExit as e:
        return int(e.code or 0)
    except Exception:
        if cmd != "hook":
            raise
        # 宿主不受影响是硬要求：hook 任何异常都吞掉，只落一条日志
        try:
            from wbn import store

            store.log(f"hook 未捕获异常: {traceback.format_exc()}")
        except Exception:
            pass
        return 0
    finally:
        sys.argv = saved


def usage() -> str:
    return __doc__ or ""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help", "help"):
        print(usage())
        return 0

    if argv and argv[0] in SUBCOMMANDS:
        cmd, rest = argv[0], argv[1:]
    elif argv and not argv[0].startswith("-"):
        print(f"未知子命令：{argv[0]}\n\n{usage()}", file=sys.stderr)
        return 2
    else:
        cmd, rest = "ui", argv      # 没有子命令 = ui，允许直接 `python app.py`

    return run_ui() if cmd == "ui" else _delegate(cmd, rest)


if __name__ == "__main__":
    sys.exit(main())
