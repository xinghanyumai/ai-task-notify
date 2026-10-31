"""单实例交棒：父进程必须硬退、子进程必须接管锁（W10 §1，P0）。

**为什么要有这个文件**：2026-10-31 实测，双击一次留下**一对**进程——父进程
（无窗口、持锁）和子进程（有窗口、不持锁）。父进程卡在 `webview.start()` 里
等它自己返回，而它不会返回，于是：

- 窗口开着的时候，父进程一直是僵尸，锁在它手里；
- 用户开 7 个窗口就是 14 个进程（PM 的原始快照），每个 ~110MB；
- 有窗口的进程反而没有锁，单实例形同虚设。

两个缺陷叠在一起，所以这里守四条：

1. 交棒顺序必须是 `spawn → 放锁 → os._exit(0)`，**不是 return**
2. 交棒来的子进程（`WBN_REPLACING_SELF=1`）**照常 acquire**，只是不去 focus
3. `release_single_instance()` 真能把名字放掉（不是"调了就算"）
4. 非 Windows / 异常路径都不许抛

## 关于 `release_single_instance` 的实现方式（实测偏离任务书）

任务书 §1.2① 给的配方是 `OpenMutexW` + `ReleaseMutex` + `CloseHandle`。
本机实测**这个配方不生效**：

    OpenMutex: 588 err= 183
    ReleaseMutex(h3) = 0 err= 288      ← 288 = ERROR_NOT_OWNER，没释放成功
    CloseHandle(h3)  = 1
    配方执行后再建: 588 err= 183        ← 仍然被占用

原因是 `acquire_single_instance` 用 `CreateMutexW(None, 0, name)` 建对象，
`bInitialOwner=0` 意味着**没有任何进程"拥有"它**——"被占用"的判据是对象存在，
而对象只要还有一个句柄开着就存在。所以真正的释放是**关掉我们自己那个句柄**，
`ReleaseMutex` 对一个没人拥有的互斥量只会返回 ERROR_NOT_OWNER。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

import app
from wbn import winapi

# ---------------------------------------------------------------- winapi 层


@pytest.mark.skipif(sys.platform != "win32", reason="互斥量是 Win32 API")
def test_release_actually_frees_the_name():
    """释放之后，同名的 CreateMutexW 必须拿不到 183。

    这是唯一能证明"真的放掉了"的判据。只断言"函数没抛"是不够的——
    任务书那个配方就是不抛但没放掉。
    """
    name = "Local\\WBN_TEST_RELEASE_ME"
    assert winapi.acquire_single_instance(name) is True
    assert _probe(name) == 183, "刚拿到锁，第二个实例应该被拦"

    winapi.release_single_instance(name)

    assert _probe(name) == 0, "release 之后锁必须空出来，否则子进程接管不了"


def test_release_is_a_noop_on_non_windows(monkeypatch):
    """POSIX 上直接 return：CI 的 ubuntu job 也要 import 这个模块。"""
    monkeypatch.setattr(sys, "platform", "linux")
    winapi.release_single_instance("Local\\WBN_TEST_POSIX")   # 不抛即通过


def test_release_swallows_unknown_name():
    """释放一个从来没拿过的名字：不抛（释放失败不该让交棒失败）。"""
    winapi.release_single_instance("Local\\WBN_TEST_NEVER_HELD")


def _probe(name: str) -> int:
    """只读探测：0 = 名字空着，183 = 已被占用。

    **探测本身也会建句柄，必须关掉。** 忘了关的话，探测者自己就成了占用者——
    第一次探测（期望 183）留下的句柄会让 release 之后仍然读到 183，
    用例于是永远红，而且看起来像 release 没生效。
    """
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
    k32.CreateMutexW.restype = ctypes.c_void_p
    handle = k32.CreateMutexW(None, 0, name)
    err = ctypes.get_last_error()
    if handle:
        k32.CloseHandle(ctypes.c_void_p(handle))
    return err


# ---------------------------------------------------------------- 交棒顺序


class _HardExit(Exception):
    """替身：`os._exit` 之后什么都不该发生。"""


def test_handoff_spawns_then_releases_then_hard_exits(monkeypatch):
    """`spawn → 放锁 → os._exit(0)`，一步都不能少、顺序不能换。

    - 少了 `os._exit`（改回 `return`）：父进程卡在 `webview.start()`，
      永远持锁 → 这就是 P0 的僵尸进程。
    - 少了 `release`：父进程退出前那一小段窗口期里子进程拿不到锁，
      它会以为自己是被拦的那个。
    """
    calls: list = []

    monkeypatch.delenv("WBN_WEBVIEW_SANDBOX", raising=False)
    monkeypatch.delenv("WBN_WEBVIEW_NOSANDBOX", raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append("popen"))
    monkeypatch.setattr(app, "release_single_instance", lambda: calls.append("release"))
    # 用例不许往真日志里写：交棒前那一行 `_log` 会落到 logs/ 里，
    # 把「今天有几次降级」这个证据污染掉。
    monkeypatch.setattr(app, "_log", lambda msg: calls.append("log"))

    def _exit(code: int) -> None:
        calls.append(("_exit", code))
        raise _HardExit

    monkeypatch.setattr(app.os, "_exit", _exit)

    with pytest.raises(_HardExit):
        app._retry_without_sandbox()

    assert calls == ["popen", "log", "release", ("_exit", 0)], (
        f"交棒顺序必须是 spawn → 落日志 → release → _exit(0)，实际 {calls}"
    )


def test_handoff_failure_does_not_release_or_exit(monkeypatch):
    """Popen 抛异常 = 没交棒成功，这时**不能**放锁也不能退。

    放了锁又没起子进程，等于把窗口让出去：用户双击一次，程序自己关了，
    而且下一次启动会以为自己拿到了锁。
    """
    calls: list = []

    monkeypatch.delenv("WBN_WEBVIEW_SANDBOX", raising=False)
    monkeypatch.delenv("WBN_WEBVIEW_NOSANDBOX", raising=False)

    def _boom(*a, **k):
        raise OSError("起不来")

    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(app, "release_single_instance", lambda: calls.append("release"))
    monkeypatch.setattr(app.os, "_exit", lambda code: calls.append(("_exit", code)))

    assert app._retry_without_sandbox() is False
    assert calls == [], "没交棒成功就不该放锁/退出"


# ------------------------------------------------------- 子进程要不要拿锁


def test_replacing_child_acquires_lock_but_does_not_focus(monkeypatch):
    """交棒来的子进程必须**照常拿锁**，拿不到就安静退出，且不去 focus。

    focus 是给"用户又双击了一次"用的。交棒场景下父窗口正在销毁，
    focus 到它等于什么都没发生。
    """
    attempts: list = []
    focus: list = []

    monkeypatch.setenv("WBN_REPLACING_SELF", "1")
    monkeypatch.delenv("WBN_BENCH", raising=False)
    monkeypatch.setattr(app, "acquire_single_instance", lambda: attempts.append(1) or False)
    monkeypatch.setattr(app, "focus_existing", lambda: focus.append(1) or True)

    assert app._acquire_lock_or_focus() is False
    assert attempts == [1], "交棒来的子进程也要试一次锁——不试就是「有窗口的进程没锁」"
    assert focus == [], "交棒场景下不许 focus 一个正在销毁的窗口"


def test_second_launch_focuses_existing_window(monkeypatch):
    """普通第二次启动：拿不到锁 → focus 已有窗口 → 退出。"""
    focus: list = []

    monkeypatch.delenv("WBN_REPLACING_SELF", raising=False)
    monkeypatch.delenv("WBN_BENCH", raising=False)
    monkeypatch.setattr(app, "acquire_single_instance", lambda: False)
    monkeypatch.setattr(app, "focus_existing", lambda: focus.append(1) or True)

    assert app._acquire_lock_or_focus() is False
    assert focus == [1]


def test_bench_mode_skips_lock_entirely(monkeypatch):
    """`tools/bench_ui.py` 要连起好几个进程，这时锁必须完全不参与。"""
    attempts: list = []

    monkeypatch.setenv("WBN_BENCH", "1")
    monkeypatch.setattr(app, "acquire_single_instance", lambda: attempts.append(1) or False)

    assert app._acquire_lock_or_focus() is True
    assert attempts == [], "bench 模式下连试都不该试"
