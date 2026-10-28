"""为 W3.5 的 GUI 验收准备两个固定目录（截图要能重复拍）。

- `tmp/w35/nolog`：log 通道关着，webhook 指向本机 200 服务，**真发一条**。
  发完服务就关——GUI 起来只读日志，不需要服务活着。
  目的：证明「最近通知」不依赖 `notifications.log`（那个文件压根没被创建）。
- `tmp/w35/yesterday`：昨天一条、今天一条，验跨天显示。

    python tools/prepare_w35_gui.py
"""

from __future__ import annotations

import http.server
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import w35_scenarios as scen  # noqa: E402

from wbn import config, pipeline  # noqa: E402

PORT = 8765


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):                       # noqa: N802 - http.server 的约定
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def main() -> int:
    out = ROOT / "tmp" / "w35"

    # ---- 1. 关 log 通道，真发一条 -------------------------------------------
    d1 = scen.build("nolog", base=out / "nolog")
    srv = http.server.HTTPServer(("127.0.0.1", PORT), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = config.load()
        print("启用通道:", [c["type"] for c in config.enabled_channels(cfg)])
        res = pipeline.process(
            {"hook_event_name": "Stop", "session_id": "w35gui",
             "cwd": "D:/Code/demo",
             "last_assistant_message": "关掉 log 通道也要能看见"},
            "WorkBuddy", cfg=cfg, ignore_limits=True)
        print("process() →", res["sent"], res.get("skipped"))
        print("notifications.log 存在:",
              (config.LOG_DIR / "notifications.log").exists())
    finally:
        srv.shutdown()

    # ---- 2. 跨天 -----------------------------------------------------------
    d2 = scen.build("yesterday", base=out / "yesterday")
    print("yesterday 日志:", sorted(p.name for p in config.LOG_DIR.iterdir()))

    print(f"\nnolog     = {d1}")
    print(f"yesterday = {d2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
