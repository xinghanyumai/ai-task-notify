"""W3.4 截图用的启动器：把配置/日志目录换掉再开窗。

**为什么要单独一个启动器**：`config.CONFIG_PATH` / `LOG_DIR` / `STATE_DIR` /
`SESSION_DIR` 都是模块级常量，`app.py` 没有命令行开关。验收要看「空日志」和
「真实日志」两种侧栏，只能先把常量改掉再进 `run_ui()`。

四个一起改。只改 CONFIG_PATH 会读到真实仓库的日志——W3.2 验收踩过这个坑。

    python tools/launch_w34.py --logs real
    python tools/launch_w34.py --logs empty
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wbn import config  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="W3.4 截图启动器")
    ap.add_argument("--logs", choices=("real", "empty", "partial"), default="real",
                    help="real=照抄本机今天的日志；empty=空目录（验「没发过不误报」）；"
                         "partial=造一成一败（验 warn）")
    ap.add_argument("--page", default="status",
                    help="开哪个页面：status / channels / triggers / template / guide")
    ap.add_argument("--keep", action="store_true", help="保留临时目录，方便事后翻看")
    args = ap.parse_args(argv)

    tmp = Path(tempfile.mkdtemp(prefix="wbn-w34-"))
    config.CONFIG_PATH = tmp / "config.json"
    config.LOG_DIR = tmp / "logs"
    config.STATE_DIR = tmp / "state"
    config.SESSION_DIR = tmp / "sessions"
    config.ensure_dirs()

    # 配置照抄本机的：通道开关、触发器都要和真实一致，否则侧栏那句话的前提就变了
    real_cfg = ROOT / "config.json"
    if real_cfg.exists():
        shutil.copy2(real_cfg, config.CONFIG_PATH)
    else:
        config.save_default_if_missing()

    if args.logs == "real":
        today = __import__("datetime").date.today().isoformat()
        src = ROOT / "logs" / f"notify-{today}.log"
        if src.exists():
            shutil.copy2(src, config.LOG_DIR / src.name)
        else:
            print(f"⚠️ 今天没有日志：{src}")

    if args.logs == "partial":
        # 本机只启用了 wxmp 一个远端通道，凑不出「一成一败」。
        # 给 Bark 填个假 key 打开它，再写一行 Bark 成功——**只写在临时目录里**，
        # 真实 config.json 不动。
        today = __import__("datetime").date.today().isoformat()
        cfg = config.load()
        for ch in cfg["channels"]:
            if ch["type"] == "bark":
                ch.update({"key": "fake-key-for-shot", "enabled": True})
        config.save(cfg)
        (config.LOG_DIR / f"notify-{today}.log").write_text(
            "[09:12:00] channel=wxmp ok=False msg=errcode=40037 模板 ID 无效\n"
            "[09:12:30] channel=bark ok=True msg=HTTP 200\n",
            encoding="utf-8",
        )

    print(f"临时目录: {tmp}")
    print(f"日志: {sorted(p.name for p in config.LOG_DIR.iterdir())}")

    import app

    # 页面路由靠 `#hash`，而 `run_ui()` 里 URL 是写死的。这里包一层
    # `create_window` 把 hash 补上，不改 app.py——验收脚本不该改动被测代码。
    if args.page != "status":
        import webview
        _orig = webview.create_window

        def _patched(title, url, *a, **kw):
            return _orig(f"{title}", f"{url}#{args.page}", *a, **kw)

        webview.create_window = _patched

    try:
        return app.run_ui()
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
