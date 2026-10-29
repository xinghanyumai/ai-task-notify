"""Windows 专有的进程级 API，只依赖 `ctypes` / `sys`。

**为什么单独一个模块**：这些函数原来住在 `wbn/theme.py` 里。W5 删 `theme.py`
（色板已搬进 `tokens.css`）时，如果连函数一起删，`app.py` 的 `from wbn.theme import
set_app_user_model_id` 会抛 `ImportError`——而那一行正躺在一个 `try` 里，于是被
当成「WebView2 缺失」，静默走降级、去起一个同样被删掉的 `ui.py`，
pythonw 退出、零输出、窗口不出现。用户看到的是「双击没反应」，所有代码都"没报错"。

**这里不许 import tkinter。** W7 要打单文件 exe，带上 tkinter 会多 5MB 并拖慢启动
（见 W7 §0.2）。本模块只用 `ctypes.windll`，POSIX 上全部直接 return——
CI 矩阵里 ubuntu 也要跑 pytest。
"""

from __future__ import annotations

import ctypes
import sys

APP_USER_MODEL_ID = "Wbn.TaskNotify.Desktop.1"

ERROR_ALREADY_EXISTS = 183

# 互斥量句柄必须活到进程结束：句柄一丢，锁就没了。
# 用列表而不是单个变量，是因为这里要同时服务 UI 和托盘两把锁（同一个进程里
# 不会都拿，但共用一份代码更省事，也避免将来加第三把锁时改结构）。
_mutexes: list[int] = []


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> None:
    """给当前进程一个独立的 AppUserModelID。

    Windows 任务栏按 AppUserModelID 分组，并据此决定按钮上显示哪个图标。
    不显式设置的话，pythonw.exe 启动的窗口会被归到 Python 名下，
    任务栏和 Alt+Tab 里看到的就是 Python 的图标，而不是我们的 .ico。
    **必须在创建任何窗口之前调用**——窗口建好之后再设，任务栏已经分好组了。

    失败一律吞掉：设不上只是图标不对，不该让程序起不来。
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def read_app_user_model_id() -> str | None:
    """读回当前进程的 AppUserModelID，没设过返回 None。

    `tests/test_platform.py` 用它断言「真的设上了」。放在这里而不是测试里拼
    ctypes，是因为「怎么读」和「怎么写」是同一个 API 的两半，分开放两边容易改歪。
    """
    if sys.platform != "win32":
        return None
    try:
        buf = ctypes.c_wchar_p()
        ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(
            ctypes.byref(buf))
        return buf.value
    except Exception:
        return None


def acquire_single_instance(name: str) -> bool:
    """尝试持有会话内命名互斥量。已被别的实例占着返回 False。

    **任何异常/失败都返回 True（放行）。** 宁可开两个窗口，也不能因为探测失败
    让用户彻底打不开——单实例是体验优化，不是安全边界。

    名字用 `Local\\` 而不是 `Global\\`：Local 是「每个登录会话一个」，正是我们要
    的粒度。全局命名跨用户生效，而 `focus_existing()` 用的 FindWindowW 是会话内
    查找——换成 Global 的后果是「另一个登录用户点开程序，什么都不会发生」，
    比开出两个窗口更糟。
    """
    if sys.platform != "win32":
        return True
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        k32.CreateMutexW.restype = ctypes.c_void_p
        handle = k32.CreateMutexW(None, 0, name)
        if not handle:
            return True
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            return False
        _mutexes.append(handle)
        return True
    except Exception:
        return True
