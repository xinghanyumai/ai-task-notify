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
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 进程起算的零点。`tools/bench_ui.py` 量的就是这个口径（W6 §4.1 的 A 数）：
# 双击 → 首屏可交互，含解释器启动 + WebView2 初始化。放在模块顶层而不是
# `run_ui()` 里，是因为「进程起」比「函数进」早一个 import 的时间——
# 那一段也是用户要等的。
_PROCESS_T0 = time.perf_counter()

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


# ------------------------------------------------------------------ 单实例


def acquire_single_instance() -> bool:
    """拿不到锁说明已有一个实例在跑，返回 False。

    **实现住在 `wbn.winapi`**：托盘也要一把同样语义的锁（`Local\\WBN_TRAY_SINGLETON`），
    两处各写一遍就是「同一件事两个说法」——改一处忘一处，症状还很难看出来
    （只是偶尔开出两个窗口）。这里保留本函数名，是因为它比
    `winapi.acquire_single_instance(MUTEX_NAME)` 在 `run_ui()` 里读起来清楚。
    """
    from wbn.winapi import acquire_single_instance as acquire

    return acquire(MUTEX_NAME)


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


# ------------------------------------------------------------ 降级提示框
#
# **不做任何 WebView2 预检。** W6.1 删掉了原来那个查 EdgeUpdate 注册表键的
# 探测函数（名字里带 webview2，已在 R47 删除，历史见 `git log -S`）。
# 它的 docstring 写着「最终仍以 start() 抛不抛异常为准」，但代码里探到 False
# 就直接 return，一次都没试过。2026-10-29 真实事故：用 regedit 改键名时粘贴
# 带进一个换行，键名变成 `{F3017226-…E4C5}\n`，键看着还在、版本号也在，探测
# 却找不到，于是程序对用户说「这台机器缺少 Microsoft WebView2 运行时」——
# 运行时明明装着。**这是用户无法自行判断的假话，比不提示更糟。**
#
# 现在只保留真实信号，四种原因各说各话，见 FALLBACK_REASONS。
#
# **no_engine 不是把预检加回来。** R47 禁的是「我们猜」：自己查注册表、探不到就
# 当运行时不存在。no_engine 读的是 `pywebview` **自己**在 import 时算好的结论
# （`winforms.is_chromium`，见 run_ui 里的注释）——那是「它会用哪个引擎」的事实，
# 不是对运行时存在与否的猜测，所以不会有假阴性。


FALLBACK_REASONS: dict[str, tuple[str, str]] = {
    # 首句必须能让人区分这四件事：没装包 / 引擎不对 / 建不出窗口 / 建了画不出。
    "no_package": (
        "没有安装 pywebview，设置界面无法启动。",
        "装一下就好：在项目目录执行 pip install -r requirements.txt",
    ),
    # **首句不许说「缺少」。** 这个分支的触发条件是 pywebview 判定「引擎不是
    # Chromium」，键名坏掉时运行时明明装着——说「缺少」就又是 R47 那句假话。
    "no_engine": (
        "检测到界面引擎不可用（WebView2 运行时定位不到），设置界面无法启动。",
        "重装一遍 WebView2 Runtime 再打开——缺它、或者它坏了，都会让界面引擎"
        "回落到 IE，本界面在 IE 下显示不出来。",
    ),
    "no_runtime": (
        "缺少 Microsoft WebView2 运行时，设置界面无法启动。",
        "它是 Windows 自带的组件，正常机器上都有；"
        "缺了从微软官网装「WebView2 Runtime」再打开。",
    ),
    "no_render": (
        "界面没能显示出来，设置窗口打不开。",
        "重开一次通常就好；还不行就重装一遍 WebView2 运行时——"
        "缺它、或者它坏了，表现都是「窗口开了但页面不出来」。"
        "反复出现请把 logs/ 里最新那份日志发出来。",
    ),
}

# 原因串写错时的兜底文案。**它说的是「不知道」，不是猜一个**——
# 猜错的代价就是上面那个事故。
UNKNOWN_REASON = (
    "设置界面无法启动（原因未知，详见日志）。",
    "logs/ 里最新那份日志记了具体是哪一步失败的。",
)


def fallback_text(reason: str) -> tuple[str, str]:
    """返回 `(首句, 补充说明)`。纯函数，方便测试断言三种原因说三句话。"""
    return FALLBACK_REASONS.get(reason, UNKNOWN_REASON)


def fallback_message(reason: str) -> str:
    """拼出对话框正文。单独拿出来是为了能测——弹窗本身没法断言文字。"""
    headline, hint = fallback_text(reason)
    return (
        f"{headline}\n\n"
        f"{hint}\n\n"
        "通知功能不受影响，仍然正常工作——只是没有图形设置界面。\n\n"
        "「是」：打开配置目录（可手动编辑 config.json）\n"
        "「否」：打开日志目录（查看通知是否发出）\n"
    )


def fallback_no_webview(reason: str) -> None:
    """设置界面起不来：给一个能改配置、能看日志的原生对话框。

    `reason` 取 `FALLBACK_REASONS` 的键之一，**故意不给默认值**——给了默认值
    就一定有人不传，然后三种失败又退回同一句话，正是 R47 要治的病。

    **用 ctypes 调 MessageBoxW，不许 import tkinter**——W7 要打单文件 exe，
    带上 tkinter 会多 5MB，还会拖慢启动（见 W7 §0.2）。所以这里连
    `from wbn import config` 都是函数内 import：它只用标准库，但保持本模块
    顶层「只有标准库」的约定。

    三个按钮各有去处，不是只有一句「装个运行时」：
        「是」   → 打开配置目录（config.json 可以手改）
        「否」   → 打开日志目录（通知链路还在跑，能看它到底发没发出去）
        「取消」 → 关掉

    这条路径**不影响通知**：hook / dispatch 不经过 UI，配置和日志都在磁盘上。
    """
    text = fallback_message(reason)
    headline = text.splitlines()[0]        # 日志里记的就是用户看到的那一句
    _log(f"设置界面无法启动（{reason}）：{headline}")

    if sys.platform != "win32":
        print(text, file=sys.stderr)
        return

    from wbn import config

    MB_YESNOCANCEL, MB_ICONWARNING = 0x03, 0x30
    IDYES, IDNO = 6, 7
    try:
        r = ctypes.windll.user32.MessageBoxW(
            None, text, "设置界面无法启动", MB_YESNOCANCEL | MB_ICONWARNING)
    except Exception as e:                  # 连对话框都弹不出来，至少留日志
        _log(f"MessageBoxW 调用失败：{e}")
        return
    if r == IDYES:
        os.startfile(str(config.ROOT))      # noqa: S606 - 打开自己的目录
    elif r == IDNO:
        config.ensure_dirs()
        os.startfile(str(config.LOG_DIR))   # noqa: S606



# ------------------------------------------------------------------ 开窗


def run_ui() -> int:
    # 基准测试时跳过单实例锁：`tools/bench_ui.py` 要连续起好几个进程，
    # 而锁的语义是「只许一个」——不跳的话第二次起就静默退出，量不到东西。
    if os.environ.get("WBN_BENCH") != "1" and not acquire_single_instance():
        focus_existing()
        return 0

    # **这个 try 只包 `import webview`。** 它是唯一「装没装第三方包」的判断。
    #
    # 我们自己的模块 import 失败**必须炸出来**——那是打包漏文件、改名、
    # 或者删错了文件，属于开发错误。伪装成「WebView2 缺失」的后果（W5 §2.4）：
    # 静默降级 → 去起一个不存在的入口 → pythonw 退出、零输出、窗口不出现，
    # 用户看到「双击没反应」，而所有代码都"没报错"。
    try:
        import webview
    except Exception:
        _log(f"import webview 失败：{traceback.format_exc()}")
        fallback_no_webview("no_package")
        return 0

    # **这不是预检，是读 pywebview 自己已经做完的判断。**
    #
    # R47 删掉的是「我们猜」：自己查注册表，探不到就当运行时不存在，于是对用户
    # 说了一句假话。这里读的 `wf.is_chromium` 是 pywebview 在 `import webview`
    # 时**模块级**求值出来的（`webview/platforms/winforms.py:131`），它的
    # `_is_chromium()`（同文件第 68 行）查的就是同一套 EdgeUpdate 键。
    #
    # 值为 False 时 pywebview 不会报错，它**静默换成 MSHTML（IE 引擎）**：
    # 窗口照样建得出来（所以 create_window 不失败），但本界面在 IE 下画不出来。
    # 用户体感是「双击 → 白窗口 → 干等 10 秒 → 告诉我打不开」。读这个值等于
    # 把那 10 秒变成 0 秒，而且不需要我们自己再判断一次运行时在不在。
    if sys.platform == "win32":
        try:
            from webview.platforms import winforms as _wf
        except Exception as exc:            # 版本变化 / 换了后端，不许崩
            _log(f"读不到 webview.platforms.winforms，跳过引擎检查：{exc}")
        else:
            is_chromium = getattr(_wf, "is_chromium", None)
            if is_chromium is None:
                _log("pywebview 没有 is_chromium 属性（版本变了？），跳过引擎检查")
            elif not is_chromium:
                _log(
                    f"界面引擎不可用：is_chromium={is_chromium!r}，"
                    f"renderer={getattr(_wf, 'renderer', '?')!r}"
                    f"（pywebview 已回落到 MSHTML），不建窗口直接降级"
                )
                fallback_no_webview("no_engine")
                return 0

    # pythonw.exe 下 stderr 是 None，栈会被吞掉。所以先落一条日志，
    # 再让异常继续往外走（退出码非 0，快捷方式 / 任务计划层面能看出来）。
    try:
        from wbn.bridge import Bridge
        from wbn.winapi import set_app_user_model_id
    except Exception:
        _log(f"UI 模块 import 失败（不是 WebView2 的问题）：{traceback.format_exc()}")
        raise

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

    def _first_activity() -> None:
        """前端第一次调通 bridge。既当存活探针，也当「首屏可交互」的打点。"""
        if alive.is_set():
            return
        alive.set()
        if os.environ.get("WBN_BENCH") == "1":
            ms = (time.perf_counter() - _PROCESS_T0) * 1000
            print(f"[bench] first-interactive {ms:.0f}ms", flush=True)

    api = Bridge(on_activity=_first_activity)
    index = (ROOT / "webui" / "index.html").as_uri()   # 中文/空格会被 percent-encode
    try:
        win = webview.create_window(
            WIN_TITLE, index,
            js_api=api,
            width=1180, height=780, min_size=(880, 600),
            background_color="#fafafa",
        )
    except Exception:
        # WebView2 运行时不可用时，有的 pywebview 版本在这里就抛，有的返回 None
        _log(f"create_window 抛异常：{traceback.format_exc()}")
        fallback_no_webview("no_runtime")
        return 0
    if win is None:
        _log("create_window 返回 None")
        fallback_no_webview("no_runtime")
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

    # 存活探针。`create_window` 没抛异常、也返回了对象，窗口照样可能是
    # **一块黑**：WebView2 初始化回调报成功，控制器随后失效，页面
    # 一次都没画出来（本机 2026-10 实测，MSHTML 后端同代码正常）。
    # 唯一可信的判据是「前端到底调通没调通 bridge」，所以拿 config_get 当心跳。
    def _watchdog() -> None:
        if alive.wait(UI_ALIVE_TIMEOUT):
            return
        _log(
            f"WebView2 窗口 {UI_ALIVE_TIMEOUT:.0f}s 内没有调通 bridge，"
            "判定 UI 未启动"
        )
        fallback_no_webview("no_render")
        for w in list(getattr(webview, "windows", [])):
            try:
                w.destroy()
            except Exception:
                pass

    threading.Thread(target=_watchdog, daemon=True).start()

    try:
        webview.start(debug=os.environ.get("WBN_DEBUG") == "1")
    except Exception:
        # 与 create_window 失败同一类：运行时在，但初始化起不来
        _log(f"webview.start() 抛异常：{traceback.format_exc()}")
        fallback_no_webview("no_runtime")
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
