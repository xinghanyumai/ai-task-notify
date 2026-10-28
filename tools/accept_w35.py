"""W3.5 验收：不需要 GUI 的那几条（A / B / C）。

跑法：

    .venv/Scripts/python.exe tools/accept_w35.py

B 那一条会**真的发一条通知**——发到本机起的一个 200 服务，不碰外网。
"""

from __future__ import annotations

import datetime
import http.server
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import w35_scenarios as scen  # noqa: E402

from wbn import config, insight, pipeline  # noqa: E402

PORT = 8765


def line(t: str = "") -> None:
    print(t)


def head(t: str) -> None:
    line()
    line("=" * 72)
    line(t)
    line("=" * 72)


# ---------------------------------------------------------------- A


def check_a() -> None:
    head("A · 测试发送不许进健康判定（验收第 4 条）")
    tmp = scen.build("empty")
    try:
        cfg = config.load()
        log = config.LOG_DIR / f"notify-{datetime.date.today().isoformat()}.log"

        log.write_text(
            "[09:12:00] test-send type=wxmp ok=False msg=errcode=40037 模板 ID 无效\n",
            encoding="utf-8")
        from wbn import bridge
        last = bridge._last_send_times()
        text, level = insight.chain_sentence(cfg, last)
        line("① 只写一条测试失败：")
        line(f"   _last_send_times() = {json.dumps(last, ensure_ascii=False)}")
        line(f"   chain_sentence()   = ({level}) {text}")
        assert level == "ok", f"测试失败不许降级，实际 {level}"
        assert "送到你手机" in text
        line("   ✅ 仍是 ok：一次故意的试探不算投递失败")

        log.write_text(
            "[09:12:00] test-send type=wxmp ok=False msg=errcode=40037 模板 ID 无效\n"
            "[09:13:00] channel=wxmp ok=False msg=errcode=40037 模板 ID 无效\n",
            encoding="utf-8")
        last = bridge._last_send_times()
        text, level = insight.chain_sentence(cfg, last)
        line("② 再加一条真实失败：")
        line(f"   _last_send_times() = {json.dumps(last, ensure_ascii=False)}")
        line(f"   chain_sentence()   = ({level}) {text}")
        assert level == "danger", f"真实失败必须降级，实际 {level}"
        line("   ✅ 变成 danger：真实失败立刻反映")
    finally:
        scen.clean(tmp)


# ---------------------------------------------------------------- B


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):                       # noqa: N802 - http.server 的约定
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):               # 别把访问日志打到终端
        pass


def check_b() -> None:
    head("B · 关掉 log 通道，「最近通知」照样有（验收第 5 条）")
    tmp = scen.build("nolog")
    srv = http.server.HTTPServer(("127.0.0.1", PORT), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = config.load()
        on = [c["type"] for c in config.enabled_channels(cfg)]
        line(f"启用通道: {on}")
        assert "log" not in on, "log 通道必须是关的"

        res = pipeline.process(
            {"hook_event_name": "Stop", "session_id": "w35",
             "cwd": "D:/Code/demo", "last_assistant_message": "关掉 log 通道也要能看见"},
            "WorkBuddy", cfg=cfg, ignore_limits=True)
        line(f"process() → sent={res['sent']} skipped={res.get('skipped')}")

        np = config.LOG_DIR / "notifications.log"
        line(f"notifications.log 存在: {np.exists()}")
        assert not np.exists(), "log 通道关着，这个文件不该被创建"

        s = insight.stats(config.load())
        line(f"stats() → today={s['today']} ok={s['ok']} fail={s['fail']}")
        for r in s["recent"]:
            line(f"   recent: [{r['day']}] {r['time']} "
                 f"{'✓' if r['ok'] else '✗'} {r['summary']}")
        assert s["today"] == 1
        assert len(s["recent"]) == 1, "最近通知不许因为 log 通道关着就空着"
        line("   ✅ 关掉 log 通道，最近通知里仍有这一条")
    finally:
        srv.shutdown()
        scen.clean(tmp)


# ---------------------------------------------------------------- C


def check_c() -> None:
    head("C · 跨天条目带 day 标记（验收第 6 条）")
    tmp = scen.build("yesterday")
    try:
        s = insight.stats(config.load())
        line(f"stats() → today={s['today']}（只数今天）")
        for r in s["recent"]:
            shown = ("昨天 " + r["time"][:5]) if r["day"] == "yesterday" else r["time"][:5]
            line(f"   显示文本: {shown!r}   (day={r['day']}, summary={r['summary']})")
        assert [r["day"] for r in s["recent"]] == ["today", "yesterday"]
        assert s["today"] == 1
        line("   ✅ 昨天的条目带 day=yesterday，前端显示「昨天 23:50」")
    finally:
        scen.clean(tmp)


def main() -> int:
    check_a()
    check_b()
    check_c()
    line()
    line("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
