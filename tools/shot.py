"""开发用截图脚本：启动界面 → 等渲染 → 截窗口 → 退出。

用法：
    python tools/shot.py 输出路径 [页面] [通道] [dark|light] [宽x高（逻辑像素）]

例：
    python tools/shot.py assets/a.png channels wxmp dark
    python tools/shot.py assets/b.png triggers "" light 940x640

脱敏：设环境变量 WBN_SCRUB=1 后，微信通道的 appid / secret / 模板 ID /
openid 会被换成等长的假值再渲染。截图要进 README 时务必带上——真值一旦
提交到公开仓库，等于把公众号接口凭证公开了。

    WBN_SCRUB=1 python tools/shot.py assets/ui_channels_light.png channels wxmp light
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import tkinter as tk
from pathlib import Path

ctypes.windll.shcore.SetProcessDpiAwareness(2)

# 脚本住在 tools/，但 wbn 包和 ui.py 在上一级
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wbn import config  # noqa: E402

if os.environ.get("WBN_SCRUB") == "1":
    # 假值保持和真值一样的长度，这样输入框里的视觉宽度不变，截图版式一致。
    _SCRUB = {
        "appid": "wx0000000000000000",
        "secret": "0" * 32,
        "template_id": "0" * 35,
        "openid": "oX_0000000000000000000000000",
        "url": "",
    }
    _real_load = config.load

    def _scrubbed_load() -> dict:
        cfg = _real_load()
        for ch in cfg.get("channels", []):
            if ch.get("type") == "wxmp":
                ch.update(_SCRUB)
        return cfg

    config.load = _scrubbed_load             # ui.App 构造时走的就是这个

import ui  # noqa: E402
from wbn import theme  # noqa: E402

# 不给路径时落到 assets/ 下。这里必须相对 ROOT 而不是 CWD：脚本挪进 tools/
# 之后，从 tools/ 里执行会把图写进 tools/assets/。
out = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "assets" / "_shot.png")
page = sys.argv[2] if len(sys.argv) > 2 else "channels"
chan = (sys.argv[3] if len(sys.argv) > 3 else "") or None
mode = sys.argv[4] if len(sys.argv) > 4 else ""
size = sys.argv[5] if len(sys.argv) > 5 else ""

theme.enable_dpi_awareness()
theme.set_app_user_model_id()
root = tk.Tk()
app = ui.App(root)

if mode in ("dark", "light"):
    app.theme_mode = mode
    app.pal = theme.resolve(mode)
    app._rebuild()

if size and "x" in size:
    w, h = size.split("x")
    root.geometry(f"{theme.S(int(w))}x{theme.S(int(h))}")

root.geometry("+120+100")


def grab():
    from PIL import ImageGrab

    root.update_idletasks()
    hwnd = int(root.wm_frame(), 16)
    r = wt.RECT()
    ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd, 9, ctypes.byref(r), ctypes.sizeof(r))
    im = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
    im.save(out)
    print(f"saved {out} {im.size}")
    root.destroy()


focus_target = None


def go():
    global focus_target
    if chan:
        app._select_channel(chan)
    if page != "channels" or chan is None:
        app._show_page(page)
    if page == "focus":
        app._show_page("channels")
        focus_target = app.save_btn
        app.save_btn._focused = True
        app.save_btn._draw()
    root.lift()
    root.attributes("-topmost", True)
    root.focus_force()
    if focus_target is not None:
        focus_target.focus_set()
    root.after(1000, grab)


root.after(350, go)
root.mainloop()
