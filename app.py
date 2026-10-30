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
    uninstall     移除 hooks / 删快捷方式            install.main()
    selftest      自检与诊断                        selftest.main()
    --version     打印版本号（CI 冒烟用）            本文件

    python app.py                            # = ui
    python app.py selftest --status
    python app.py install all --no-autostart
    python app.py hook --app WorkBuddy < payload.json
    python app.py --version
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


# ---------------------------------------------------------- 输出不许打死进程


def _harden_stdio() -> None:
    """把 stdout/stderr 的 `errors` 改成 `replace`，让一行中文不至于带走进程。

    2026-10-30 的 CI 红就是这么来的，值得写清楚：

    `通知.exe` 是 `console=False` 编的，但**只要 stdout 被重定向**（CI 的冒烟
    步骤、或者用户在 cmd 里 `通知.exe --version > out.txt`），Python 就会按
    **系统 ANSI 代码页**建一个真的 `sys.stdout`。在英文 Windows 上那是 cp1252，
    于是 `print("AI 任务通知 1.0.0")` 抛 `UnicodeEncodeError: 'charmap' codec
    can't encode characters in position 3-6`（position 3-6 正是「任务通知」四个字）。
    异常从 `main()` 里冒出来，PyInstaller 再弹一个
    「Unhandled exception in script」对话框——runner 上没有桌面，没人点得掉，
    进程就永远不退出，冒烟卡满 300 秒。

    本机一直是 cp936（中文），中文编码得出来，所以本地 2.4 秒退出 0，
    这个 bug 在本地永远复现不了。它不是「CI 环境的问题」，是**任何非中文
    Windows 上 `--version` 都会崩**。

    改 `errors="replace"` 而不是硬改 utf-8：能编码的照旧（cp936 下中文一字不差），
    编码不了的降级成 `?`，绝不抛异常。encoding 保持系统原样——真控制台是 cp936
    时改成 utf-8 只会让中文变乱码，那是用一个 bug 换另一个。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            # 双击启动（无控制台）时 PyInstaller 就把它们设成 None，
            # 此时 print() 自己会静默丢弃，不会抛。
            continue
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            # pytest 的捕获流、被替换成 StringIO 的流没有 reconfigure。
            # 拿不到就跳过：这只是加固，不是功能。
            pass


_harden_stdio()

# 进程起算的零点。`tools/bench_ui.py` 量的就是这个口径（W6 §4.1 的 A 数）：
# 双击 → 首屏可交互，含解释器启动 + WebView2 初始化。放在模块顶层而不是
# `run_ui()` 里，是因为「进程起」比「函数进」早一个 import 的时间——
# 那一段也是用户要等的。
_PROCESS_T0 = time.perf_counter()

WIN_TITLE = "AI 任务通知"
SUBCOMMANDS = ("ui", "tray", "hook", "dispatch", "install", "uninstall", "selftest")

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


def _sandbox_off_args() -> bool:
    """要不要给 WebView2 加 `--no-sandbox`。

    | 环境变量 | 含义 |
    |---|---|
    | `WBN_WEBVIEW_SANDBOX=1` | 强制带沙箱，**并且禁止降级**（排查用） |
    | `WBN_WEBVIEW_NOSANDBOX=1` | 强制关沙箱（降级重启时由父进程设） |
    | 都不设 | 带沙箱；只有看门狗判定黑窗时才降级一次 |

    单独抽成函数是为了能测：这条判断以前是内联的 `if ... != "1"`，
    谁也没法在不真起窗口的前提下验证它。
    """
    if os.environ.get("WBN_WEBVIEW_SANDBOX") == "1":
        return False
    return os.environ.get("WBN_WEBVIEW_NOSANDBOX") == "1"


def _retry_without_sandbox() -> bool:
    """黑窗降级：带 `--no-sandbox` 重启一次自己。返回 True = 已经交棒。

    **为什么是重启而不是改参数重来**：`webview.start()` 在一个进程里只能调
    一次，参数在它之前就定死了。要换 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS`
    只能换进程。

    三道闸，防止重启风暴：
      1. 用户显式要求带沙箱（`WBN_WEBVIEW_SANDBOX=1`）→ 不降级
      2. 已经降级过一次（`WBN_WEBVIEW_NOSANDBOX=1`）→ 不再降级，走提示框
      3. 起不来（Popen 抛异常）→ 返回 False，走提示框
    """
    if os.environ.get("WBN_WEBVIEW_SANDBOX") == "1":
        return False
    if os.environ.get("WBN_WEBVIEW_NOSANDBOX") == "1":
        return False

    import subprocess

    from wbn import config as _cfg
    from wbn.spawn import child_env

    env = child_env()
    env["WBN_WEBVIEW_NOSANDBOX"] = "1"
    # 让子进程跳过单实例锁：父进程此刻还握着它。
    env["WBN_REPLACING_SELF"] = "1"
    # 打点文件别传给子进程，否则 bench 会把「重启后那次」当成第二轮
    env.pop("WBN_BENCH_FILE", None)

    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        py = Path(sys.executable).with_name("pythonw.exe")
        cmd = [str(py if py.exists() else sys.executable), str(Path(__file__).resolve())]

    try:
        subprocess.Popen(
            cmd,
            cwd=str(_cfg.ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
    except Exception as exc:
        _log(f"带 --no-sandbox 重启失败：{exc}")
        return False
    return True


def run_ui() -> int:
    # 基准测试时跳过单实例锁：`tools/bench_ui.py` 要连续起好几个进程，
    # 而锁的语义是「只许一个」——不跳的话第二次起就静默退出，量不到东西。
    #
    # `WBN_REPLACING_SELF` 同理：黑窗降级会带 `--no-sandbox` 重启自己，
    # 那时父进程还握着锁没放，不跳的话子进程会以为「已有实例在跑」→
    # 去 focus 一个马上就要销毁的窗口 → 直接退出。降级变成死路。
    if os.environ.get("WBN_BENCH") != "1" and os.environ.get(
        "WBN_REPLACING_SELF"
    ) != "1" and not acquire_single_instance():
        focus_existing()
        return 0

    # **这个 try 只包 `import webview`。** 它是唯一「装没装第三方包」的判断。
    #
    # 我们自己的模块 import 失败**必须炸出来**——那是打包漏文件、改名、
    # 或者删错了文件，属于开发错误。伪装成「WebView2 缺失」的后果（W5 §2.4）：
    # 静默降级 → 去起一个不存在的入口 → pythonw 退出、零输出、窗口不出现，
    # 用户看到「双击没反应」，而所有代码都"没报错"。
    _t_thirdparty = 0.0
    _t0 = time.perf_counter()
    try:
        import webview
    except Exception:
        _log(f"import webview 失败：{traceback.format_exc()}")
        fallback_no_webview("no_package")
        return 0
    _t_thirdparty += time.perf_counter() - _t0

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
        _t0 = time.perf_counter()
        try:
            from webview.platforms import winforms as _wf
        except Exception as exc:            # 版本变化 / 换了后端，不许崩
            _log(f"读不到 webview.platforms.winforms，跳过引擎检查：{exc}")
        else:
            _t_thirdparty += time.perf_counter() - _t0
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

    # **沙箱默认开着。** 2026-10-27 那台机器上 Chromium 沙箱起不来，窗口是一片
    # 黑（浏览器进程静默退出，而 pywebview 的初始化回调照样报 IsSuccess=True、
    # 前端也照样能调通 bridge——所以 Python 侧看不出任何异常），当时加了
    # `--no-sandbox` 才画得出来。
    #
    # **2026-10-30（W7）重测：这个故障已经不复现了。** 源码形态和 exe 形态
    # 在不加 `--no-sandbox` 的情况下都能正常渲染（截图见 tmp/w7/sandbox_on.png，
    # 首屏打点也通）。所以不再无条件关沙箱——一个没有必要的安全降级会跟着
    # exe 发给每一个用户，那是净损失。
    #
    # 但「这台机器能跑」不等于「所有机器能跑」：AppLocker、杀软、残留的
    # WebView2 用户数据目录都可能让浏览器进程起不来。所以留一条**失败即降级**
    # 的路：看门狗判定「bridge 超时没通」（正是黑窗的症状）时，带
    # `--no-sandbox` 重启一次自己，见 `_retry_without_sandbox()`。
    if _sandbox_off_args():
        os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--no-sandbox")

    alive = threading.Event()

    def _first_activity() -> None:
        """前端第一次调通 bridge。既当存活探针，也当「首屏可交互」的打点。"""
        if alive.is_set():
            return
        alive.set()
        if os.environ.get("WBN_BENCH") == "1":
            ms = (time.perf_counter() - _PROCESS_T0) * 1000
            _bench(f"[bench] first-interactive {ms:.0f}ms")

    api = Bridge(on_activity=_first_activity)

    # ---- B 数：自研代码段 --------------------------------------------------
    #
    # 「进程起 → Bridge() 完成」里有三段：解释器启动 + 我们的 import、两个
    # 第三方 import（pywebview 与 WinForms 程序集）、我们的 import + 配置 +
    # 日志 + Bridge 构造。只有**最后一段**是我们的。
    #
    # 为什么不直接量「Bridge 构造耗时」：`import app` 自己的模块级 import
    # （config / sanitize / events…）也在用户等待里，漏掉它，B 数就会好看而
    # 无用。所以口径是「减去两个第三方 import 之后的余量」，这样以后谁在
    # 模块顶层加一个重 import，B 会涨。
    #
    # 这条是 W6.3 §2 的判据 2：**A 是架构决定的，B 才是防回归的**。
    if os.environ.get("WBN_BENCH") == "1":
        ms = (time.perf_counter() - _PROCESS_T0) * 1000 - _t_thirdparty * 1000
        _bench(f"[bench] self-code {ms:.0f}ms")
    # 前端资源从**只读资源根**取，不是从 ROOT：打包后 ROOT 是 exe 旁边（可写，
    # 放配置和日志），webui/ 在 `_MEIPASS` 那个临时解压目录里。混用的后果是
    # 源码态正常、exe 里白屏（W7 §3.1 ①）。
    from wbn import config as _cfg
    index = _cfg.resource("webui", "index.html").as_uri()   # 中文/空格会被 percent-encode
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
        # 先试一次「关沙箱重启」——2026-10-27 那个黑窗就是这个原因。
        # 成功交棒的话就不再弹提示框（用户会看到新进程的窗口）。
        if _retry_without_sandbox():
            _log("已带 --no-sandbox 重启一次（黑窗降级）")
        else:
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
    # 看门狗交棒了：新进程已经带 `--no-sandbox` 起来了，本进程安静退出。
    return 0


def _log(msg: str) -> None:
    """往 logs/ 落一条。UI 起不来时用户看不到控制台，只能靠这个查。"""
    try:
        from wbn import store

        store.log(f"[app] {msg}")
    except Exception:
        pass


def _bench(line: str) -> None:
    """打一条耗时点。stdout + （可选）文件。

    为什么不能只 print：exe 是 `console=False` 编的，**双击启动时没有 stdout**
    （PyInstaller 把它设成 `None`，print 直接进黑洞）。重定向是另一回事——
    有重定向时 Python 会建一个真的 stdout，但它的编码是系统 ANSI 代码页，
    在英文 Windows 上是 cp1252，中文一 print 就抛（见 `_harden_stdio`）。

    所以多给一条文件通道：`WBN_BENCH_FILE` 指向哪就写哪（`tools/bench_ui.py
    --exe` 会传一个临时文件）。源码形态不设这个变量，行为和以前完全一样。
    """
    print(line, flush=True)
    path = os.environ.get("WBN_BENCH_FILE")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# -------------------------------------------------------------- 子命令分发


def _delegate(cmd: str, rest: list[str]) -> int:
    """把 sys.argv 改写成「被调模块自己的视角」，再调它的 main()。

    hook / install / selftest 的 main() 直接读 sys.argv 走 argparse，
    不改写的话 `app.py selftest --status` 会被当成两个位置参数而报错。
    """
    import importlib

    # `uninstall` 不是独立模块：卸载逻辑住在 install.py，是它的一个 action。
    # 对外仍然给一个平级的子命令名——用户想的是「卸载」，不是
    # 「用安装器执行卸载动作」。
    if cmd == "uninstall":
        mod_name, argv = "install", ["install", "uninstall", *rest]
    else:
        mod_name, argv = cmd, [cmd, *rest]
    mod = importlib.import_module(mod_name)
    saved, sys.argv = sys.argv, argv
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

    # `--version` 单独处理：它是 CI 的冒烟判据（exe 能不能起来），
    # 不许落到下面的 else 分支去开窗口——那会让 CI 挂住 10 秒然后超时。
    if argv and argv[0] in ("-V", "--version", "version"):
        from wbn import __version__

        print(f"AI 任务通知 {__version__}")
        return 0

    if argv and argv[0] in SUBCOMMANDS:
        cmd, rest = argv[0], argv[1:]
    elif argv and not argv[0].startswith("-"):
        print(f"未知子命令：{argv[0]}\n\n{usage()}", file=sys.stderr)
        return 2
    else:
        cmd, rest = "ui", argv      # 没有子命令 = ui，允许直接 `python app.py`

    return run_ui() if cmd == "ui" else _delegate(cmd, rest)


def _main_guarded() -> int:
    """`__main__` 的唯一入口：兜住未捕获异常，不让 PyInstaller 弹对话框。

    frozen 的 exe 里，从脚本冒出去的异常落到引导器手里，它弹一个
    「Unhandled exception in script」对话框，**然后等用户点确定**。有桌面的
    机器上这还能接受；CI 的 runner 上没有桌面，那个框永远点不掉，进程就永远
    不退出——2026-10-30 的冒烟就是这么把「退出码 1」拖成「300 秒超时」的，
    连真正的报错都看不见（对话框正文没人读得到，只有 exe 的 stderr 里留了一份）。

    所以自己兜：traceback 打到 stderr（已被 `_harden_stdio` 加固，编码不了也
    不会二次抛），同一份内容再落进应用日志（用户能截图发过来），退出码 1。

    `SystemExit` 必须原样放行——`main()` 里的 `return 0` 走的是 `sys.exit(0)`，
    被这里吞掉的话每个子命令都会变成失败。
    """
    try:
        return main()
    except SystemExit:
        raise
    except BaseException:
        tb = traceback.format_exc()
        try:
            print(tb, file=sys.stderr)
        except Exception:
            pass
        try:
            from wbn import store

            store.log(f"未捕获异常，进程退出：\n{tb}")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(_main_guarded())
