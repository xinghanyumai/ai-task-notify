"""补间动画驱动。

Tkinter 没有动画系统，只有 `after()`。这里封一层可取消的补间：

- 固定步长（默认 13ms），用真实时间算进度，不受调度抖动影响
- 同一个 `(控件, 键名)` 同时只跑一个补间，新的会自动顶掉旧的。
  这一条很关键：用户快速连点通道时，多个滑入动画叠加会把控件推到
  错误的偏移量上，最后停在半路——看起来就是「动画有问题」。
- 控件被销毁时自动停止，不会抛 TclError
"""

from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Callable

# (id(widget), key) -> 当前有效的补间序号
_active: dict[tuple[int, str], int] = {}

# 「减弱动效」开关。开启后所有补间直接跳到终点，不做逐帧绘制。
# 系统级无障碍设置，前庭功能敏感的用户会打开它。
_reduced = False


def set_reduced(on: bool) -> None:
    global _reduced
    _reduced = bool(on)


def reduced() -> bool:
    return _reduced


def detect_reduced_motion() -> bool:
    """读 Windows 的「在 Windows 中显示动画」开关。

    SPI_GETCLIENTAREAANIMATION = 0x1042，返回 0 表示用户关掉了动画。
    读不到就当作不减弱（默认行为）。
    """
    import sys

    if sys.platform != "win32":
        return False
    try:
        import ctypes

        enabled = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            0x1042, 0, ctypes.byref(enabled), 0
        )
        return bool(ok) and enabled.value == 0
    except Exception:
        return False


# ---------------------------------------------------------------- 缓动


def linear(t: float) -> float:
    return t


def ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def ease_out_quint(t: float) -> float:
    return 1 - (1 - t) ** 5


def ease_in_out_cubic(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_out_back(t: float, s: float = 1.25) -> float:
    """末端轻微过冲，用于开关滑块这类「有物理感」的元素。"""
    t -= 1
    return t * t * ((s + 1) * t + s) + 1


# ---------------------------------------------------------------- 驱动


def tween(
    widget: tk.Misc,
    key: str,
    duration: int,
    on_frame: Callable[[float], None],
    ease: Callable[[float], float] = ease_out_cubic,
    interval: int = 13,
    on_done: Callable[[], None] | None = None,
) -> None:
    """在 widget 上跑一个补间。on_frame 每帧收到已过缓动的进度 0~1。"""
    slot = (id(widget), key)
    token = _active.get(slot, 0) + 1
    _active[slot] = token

    # 减弱动效：只画终点那一帧，行为结果完全一致，只是不逐帧演。
    if _reduced or duration <= 0:
        try:
            on_frame(ease(1.0))
        except tk.TclError:
            pass
        if on_done is not None and _alive(widget):
            try:
                on_done()
            except tk.TclError:
                pass
        _active.pop(slot, None)
        return

    start = time.perf_counter()
    span = max(1, duration)

    def frame() -> None:
        # 被新的补间接管 / 控件已销毁 → 立刻退出，不碰任何控件
        if _active.get(slot) != token:
            return
        if not _alive(widget):
            _active.pop(slot, None)
            return
        t = min(1.0, (time.perf_counter() - start) * 1000.0 / span)
        try:
            on_frame(ease(t))
        except tk.TclError:
            _active.pop(slot, None)
            return
        if t >= 1.0:
            _active.pop(slot, None)
            if on_done is not None and _alive(widget):
                try:
                    on_done()
                except tk.TclError:
                    pass
        else:
            try:
                widget.after(interval, frame)
            except tk.TclError:
                _active.pop(slot, None)

    frame()


def stop(widget: tk.Misc, key: str) -> None:
    """取消某个补间（控件即将销毁时调用）。"""
    _active.pop((id(widget), key), None)


def _alive(widget: tk.Misc) -> bool:
    try:
        return bool(widget.winfo_exists())
    except Exception:
        return False


def later(widget: tk.Misc, ms: int, fn: Callable[[], None]) -> str | None:
    """安全的 after 包装：控件已销毁就什么也不做。"""
    if not _alive(widget):
        return None
    try:
        return widget.after(ms, fn)
    except tk.TclError:
        return None
