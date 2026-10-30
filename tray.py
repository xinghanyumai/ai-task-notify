"""系统托盘常驻程序（可选组件）。

作用：
- 让「通知服务」在任务栏有一个可见入口，随时知道它活着
- 一键暂停 / 恢复、发送测试、打开设置与日志
- 开机自启后静默驻留（--silent）

真正的通知逻辑不依赖它：即使托盘没开，hooks 照样工作。
托盘挂掉或没装 pystray，只是少了个快捷入口而已。
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from wbn import channels as ch_mod  # noqa: E402
from wbn import config, pipeline  # noqa: E402
from wbn.spawn import child_env  # noqa: E402
from wbn.winapi import (  # noqa: E402
    acquire_single_instance,
    set_app_user_model_id,
)

# `Local\` = 每个登录会话一个。见 wbn/winapi.acquire_single_instance 的说明。
TRAY_MUTEX_NAME = "Local\\WBN_TRAY_SINGLETON"

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # 没装依赖时优雅退出
    pystray = None


def pythonw() -> str:
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand) if cand.exists() else str(exe)


def make_image() -> Image.Image:
    """托盘图标：直接复用 assets/icon.ico，和窗口、桌面快捷方式保持一致。"""
    # 走 config.resource：打包后图标在 `_MEIPASS` 里，不在 exe 旁边
    from wbn import config as _cfg
    ico = _cfg.resource("assets", "icon.ico")
    try:
        img = Image.open(ico)
        img.size = (64, 64)          # 从 ICO 里挑 64px 那一帧
        return img.convert("RGBA")
    except Exception:
        pass
    # 图标文件缺失时现画一个兜底，至少不是空白
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 2, size - 2], radius=14, fill=(0, 194, 154, 255))
    d.pieslice([19, 16, 45, 42], 180, 360, fill=(255, 255, 255, 255))
    d.rectangle([19, 29, 45, 43], fill=(255, 255, 255, 255))
    d.rounded_rectangle([16, 41, 48, 48], radius=3, fill=(255, 255, 255, 255))
    d.rectangle([30, 12, 34, 17], fill=(255, 255, 255, 255))
    d.ellipse([27, 46, 37, 55], fill=(255, 255, 255, 255))
    return img


def status_text() -> str:
    cfg = config.load()
    if not cfg.get("enabled", True):
        return "AI 任务通知 · 已暂停"
    if ch_mod.has_remote_channel(cfg):
        n = sum(
            1
            for c in cfg.get("channels", [])
            if c.get("enabled") and c.get("type") not in ("log",)
        )
        return f"AI 任务通知 · 运行中（{n} 个手机通道）"
    return "AI 任务通知 · 未配置手机通道"


def open_ui() -> None:
    # 走 app.py：它带单实例锁，点两次托盘不会再开出两个各存一份配置的窗口，
    # 也会在 WebView2 缺失时弹出原生提示框（不再降级到 tkinter——W5 已下线）。
    # 打包后没有 app.py 这个文件：exe 不带子命令就是 ui。
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        cmd = [pythonw(), str(ROOT / "app.py")]
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        # cwd 必须是 exe 旁边（`config.ROOT`），不是 `__file__` 的目录：
        # 打包后后者是 `_MEIPASS`，退出即删，拿它当工作目录等于把子进程拴在
        # 一个待删的临时目录上。
        cwd=str(config.ROOT),
        # 清掉 `_PYI_*`：不清的话子进程会去复用本进程的 `_MEIPASS`，
        # 而托盘一退出那个目录就没了——「点打开设置没反应」。
        # 详见 `wbn/spawn.py`。
        env=child_env(),
        creationflags=0x08000000 if sys.platform == "win32" else 0,
    )


def run(icon: pystray.Icon) -> None:  # pragma: no cover - GUI 回调
    def on_open(_i=None, _it=None):
        open_ui()

    def on_test(_i=None, _it=None):
        cfg = config.load()
        pipeline.send_test(cfg)
        icon.notify("测试通知已发出，请查看手机。", "AI 任务通知")

    def on_toggle(_i=None, _it=None):
        cfg = config.load()
        cfg["enabled"] = not bool(cfg.get("enabled", True))
        config.save(cfg)
        icon.title = status_text()
        icon.update_menu()

    def on_logs(_i=None, _it=None):
        try:
            import os

            config.ensure_dirs()
            os.startfile(str(config.LOG_DIR))  # type: ignore[attr-defined]
        except Exception:
            pass

    def on_quit(_i=None, _it=None):
        icon.stop()

    def is_enabled(_it=None):
        return bool(config.load().get("enabled", True))

    def on_autostart(_i=None, _it=None):
        # 自启只有一个真相：启动文件夹里那个 .lnk。这里不碰 config.json，
        # 也不缓存——`autostart_enabled()` 现在是一次进程内 Win32 调用（约 0.1ms），
        # 快得没必要缓存（W9）。
        import install

        install.set_autostart(not install.autostart_enabled())
        icon.update_menu()

    def is_autostart(_it=None):
        import install

        return install.autostart_enabled()

    menu = pystray.Menu(
        pystray.MenuItem(lambda _: status_text(), on_open, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("打开设置中心", on_open),
        pystray.MenuItem("发送测试通知", on_test),
        pystray.MenuItem("通知已开启", on_toggle, checked=is_enabled),
        pystray.MenuItem("开机自启", on_autostart, checked=is_autostart),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("打开日志目录", on_logs),
        pystray.MenuItem("退出", on_quit),
    )
    icon.menu = menu
    icon.title = status_text()
    icon.run()


def main() -> int:
    # 托盘自己也要一把锁。**没有它的时候 2026-10-27 实测机器上同时跑着两个
    # pythonw**——托盘没有窗口，用户完全看不出来，但两个托盘会各自重试失败队列，
    # 同一条通知就可能发两遍。
    #
    # 和 UI 用**不同的**名字：两把锁各管各的。共用一把的后果是「开着设置窗口时
    # 托盘起不来」——那是两个互不相干的组件。
    if not acquire_single_instance(TRAY_MUTEX_NAME):
        return 0

    if pystray is None:
        # 依赖缺失：直接打开设置中心，功能不受影响
        open_ui()
        return 0

    set_app_user_model_id()   # 托盘也挂同一个 AppUserModelID，避免出现两组图标

    config.ensure_dirs()
    config.save_default_if_missing()

    # 启动时顺手重试一次失败队列
    threading.Thread(target=lambda: pipeline.retry_queue(), daemon=True).start()

    icon = pystray.Icon("wbn", make_image(), status_text())
    run(icon)
    return 0


if __name__ == "__main__":
    sys.exit(main())
