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
# 用 **名字 → 句柄** 的字典而不是列表，是因为 `release_single_instance()` 要
# 精确关掉**那一个**名字的句柄（W10 §1 的交棒场景）——列表只能全关，
# 而托盘和 UI 两把锁可能同进程共存（见下面 acquire 的说明）。
_mutexes: dict[str, int] = {}


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
        _mutexes[name] = handle
        return True
    except Exception:
        return True


def release_single_instance(name: str) -> None:
    """显式释放同名互斥量。只给「降级交棒」用。

    为什么必须显式：handle 在 `_mutexes` 里躺着，进程不退出它就一直有效。
    交棒场景下父进程马上就要退出，但**退出前那段窗口期**子进程要拿锁，
    不先放掉的话子进程会以为自己是被拦的那个。

    ## 为什么是「关自己的句柄」，而不是 `ReleaseMutex`

    任务书原本写的是 `OpenMutexW` + `ReleaseMutex` + `CloseHandle`。本机实测
    **那个配方不生效**（2026-10-31，见 `tests/test_single_instance_handoff.py`
    的模块 docstring 有完整输出）：

        ReleaseMutex(h) = 0 err= 288        # 288 = ERROR_NOT_OWNER
        配方执行后再建同名互斥量: err= 183   # 仍然被占用

    因为 `acquire_single_instance` 用的是 `CreateMutexW(None, 0, name)`，
    `bInitialOwner=0` 意味着**没有任何进程"拥有"它**——"被占用"的判据是
    **对象存在**，而对象只要还有句柄开着就存在。所以真正的释放只有一条路：
    把**我们自己创建的那个句柄**关掉，对象随之销毁，别人再建就是全新的。

    任何异常都吞掉：释放失败不该让交棒失败（父进程反正要退出，
    句柄会随进程结束自动关）。
    """
    if sys.platform != "win32":
        return
    try:
        handle = _mutexes.pop(name, None)
        if not handle:
            return
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:
        pass
