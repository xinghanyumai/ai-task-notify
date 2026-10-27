"""给 pywebview 窗口截图。用 Windows Graphics Capture（WGC）。

**为什么不能用普通的截图方式**

WebView2 的内容是 DirectComposition 硬件合成，不是画在窗口 DC 上的：

| 方式 | 结果 |
|---|---|
| `PIL.ImageGrab.grab(bbox=窗口矩形)` | 内容区**一整块黑**（只有标题栏正常） |
| `PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT)` | 只拍到 WinForms 父窗口的 `background_color`，内容区是纯色 |
| **Windows Graphics Capture** | ✅ 正常 |

这三条是 2026-10-27 实测的，别再试前两个。

**依赖**：`pip install windows-capture`（会连带装 numpy + opencv-python）。
这三个包**只在截图时需要**，运行程序不需要，也不要打进 exe。

用法：

    python tools/shot_webview.py 输出.png
    python tools/shot_webview.py 输出.png --title "AI 任务通知" --wait 8
    python tools/shot_webview.py --list          # 列出有标题的窗口，找 title 用

配合 `app.py` 用的典型流程（截图脚本在另一个进程里跑，窗口那个进程不用改）：

    start  python app.py
    timeout /t 6
    python tools/shot_webview.py assets/screenshots/w2/light.png
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time
from pathlib import Path

DEFAULT_TITLE = "AI 任务通知"


def _require_wgc():
    try:
        from windows_capture import (  # noqa: F401
            InternalCaptureControl,
            WindowsCapture,
        )
    except ImportError:
        sys.exit(
            "缺少截图依赖。装一下：\n"
            "    pip install windows-capture\n"
            "（会连带装 numpy / opencv-python。只在截图时需要，运行程序不需要）"
        )
    from windows_capture import InternalCaptureControl, WindowsCapture

    return WindowsCapture, InternalCaptureControl


def _find_window(title: str) -> int:
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.FindWindowW.restype = ctypes.c_void_p
    return u32.FindWindowW(None, title) or 0


def list_windows() -> None:
    """列出所有带标题的可见顶层窗口，找不到 title 时用它。"""
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.GetWindowTextLengthW.argtypes = (ctypes.c_void_p,)
    proc_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    rows: list[tuple[str, int]] = []

    def cb(hwnd, _lparam):
        if not u32.IsWindowVisible(hwnd):
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        rows.append((buf.value, hwnd))
        return True

    u32.EnumWindows(proc_type(cb), 0)
    for title, hwnd in sorted(rows):
        print(f"{hwnd:>10}  {title}")


def shot(out: Path, title: str, wait: float) -> int:
    WindowsCapture, _ = _require_wgc()

    hwnd = _find_window(title)
    if not hwnd:
        print(f"找不到标题为 {title!r} 的窗口。先跑 `{Path(sys.argv[0]).name} --list` 看看。",
              file=sys.stderr)
        return 2
    if wait > 0:
        time.sleep(wait)      # 等页面渲染完；WebView2 首帧比窗口出现晚

    out.parent.mkdir(parents=True, exist_ok=True)
    done = threading.Event()
    failed: list[str] = []

    cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd)

    @cap.event
    def on_frame_arrived(frame, control):
        # 只取第一帧就够了：这是静态截图，不是录屏
        try:
            frame.save_as_image(str(out))
        except Exception as e:                       # pragma: no cover - GUI 回调
            failed.append(f"{type(e).__name__}: {e}")
        control.stop()
        done.set()

    @cap.event
    def on_closed():                                 # pragma: no cover - GUI 回调
        failed.append("窗口在截到第一帧之前就关了")
        done.set()

    cap.start()
    if not done.wait(15):
        print("15 秒没截到画面。窗口是不是最小化了？", file=sys.stderr)
        return 3
    if failed:
        print("截图失败：" + "; ".join(failed), file=sys.stderr)
        return 4

    from PIL import Image

    with Image.open(out) as im:
        print(f"{out}  {im.width}x{im.height}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="给 pywebview 窗口截图（Windows Graphics Capture）")
    ap.add_argument("output", nargs="?", help="输出 PNG 路径")
    ap.add_argument("--title", default=DEFAULT_TITLE, help=f"窗口标题，默认 {DEFAULT_TITLE!r}")
    ap.add_argument("--wait", type=float, default=1.5,
                    help="截之前等多少秒，默认 1.5（窗口刚开时页面还没画完）")
    ap.add_argument("--list", action="store_true", help="列出有标题的窗口")
    args = ap.parse_args(argv)

    if args.list:
        list_windows()
        return 0
    if not args.output:
        ap.print_help()
        return 2
    return shot(Path(args.output), args.title, args.wait)


if __name__ == "__main__":
    sys.exit(main())
