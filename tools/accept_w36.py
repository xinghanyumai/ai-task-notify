"""W3.6 验收：跨天边界（D18）。

六条，全部用临时目录隔离（四个路径常量一起改，见 `w35_scenarios._isolate`）。

    python tools/accept_w36.py

第 1 条同时打印「改前」的基线：改前 = 通道行两天都数，那个数是从同一批
日志行里手算出来的，不是回忆。
"""

from __future__ import annotations

import datetime
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import w35_scenarios as scen  # noqa: E402

from wbn import bridge, config, insight  # noqa: E402

LINE: list[str] = []


def line(s: str = "") -> None:
    LINE.append(s)
    print(s, flush=True)


def head(title: str) -> None:
    line()
    line("=" * 72)
    line(title)
    line("=" * 72)


def _days() -> tuple[str, str]:
    now = datetime.date.today()
    return ((now - datetime.timedelta(days=1)).isoformat(), now.isoformat())


def _write_day(day: str, lines: list[str]) -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    (config.LOG_DIR / f"notify-{day}.log").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def _ch(ok: bool, t: str = "09:00:00", ctype: str = "wxmp") -> str:
    return (f"[{t}] channel={ctype} ok={'True' if ok else 'False'} "
            f"msg={'ok' if ok else 'errcode=40037 模板 ID 无效'}")


def _ev(ok: bool, t: str = "09:00:01", title: str = "任务完成") -> str:
    return (f"[{t}] event=Stop kind=task_done sent=True "
            f"remote_ok={'True' if ok else 'False'} elapsed=1.00s title={title}")


def _setup(day_lines: list[str], today_lines: list[str]) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="wbn-w36-"))
    scen._isolate(tmp)
    cfg = scen._base_cfg()
    scen._ch(cfg, "wxmp").update({
        "appid": "a", "secret": "s", "template_id": "t", "openid": "o",
        "enabled": True, "manual_off": False,
    })
    config.save(cfg)
    y, t = _days()
    _write_day(y, day_lines)
    _write_day(t, today_lines)
    return tmp


def _count(lines: list[str]) -> tuple[int, int]:
    """手算：非 log 通道行的 (ok, fail)。"""
    ok = fail = 0
    for ln in lines:
        m = insight._CHANNEL_LINE.match(ln)
        if not m or m.group(2) == "log" or insight._is_test_line(ln):
            continue
        if m.group(3) == "True":
            ok += 1
        else:
            fail += 1
    return ok, fail


# ---------------------------------------------------------------- 1


def check1() -> None:
    head("1 · ok/fail 只数今天（改前 / 改后并排）")
    y_lines = [_ch(True), _ch(True, "09:01:00"), _ch(True, "09:02:00"),
               _ch(False, "09:03:00"), _ch(False, "09:04:00")]
    t_lines = [_ch(True, "10:00:00")]
    tmp = _setup(y_lines, t_lines)
    try:
        s = insight.stats(config.load())
        y_ok, y_fail = _count(y_lines)
        t_ok, t_fail = _count(t_lines)
        line(f"昨天通道行: {len(y_lines)} 条 → ok={y_ok} fail={y_fail}")
        line(f"今天通道行: {len(t_lines)} 条 → ok={t_ok} fail={t_fail}")
        line()
        line(f"改前（两天都数）   ok={y_ok + t_ok} fail={y_fail + t_fail}")
        line(f"改后 stats()       ok={s['ok']} fail={s['fail']}")
        assert s["ok"] == 1 and s["fail"] == 0, s
        line("✅ ok=1 fail=0")
    finally:
        scen.clean(tmp)


# ---------------------------------------------------------------- 2


def check2() -> None:
    head("2 · 真机 logs/：手算 vs stats()")
    scen._isolate(ROOT)          # 直接用仓库的真实路径
    s = insight.stats(config.load())
    y, t = _days()
    real = config.LOG_DIR / f"notify-{t}.log"
    assert real.exists(), f"今天没有日志：{real}"
    text = real.read_text(encoding="utf-8", errors="replace")

    ok = fail = 0
    for ln in text.splitlines():
        m = insight._CHANNEL_LINE.match(ln)
        if not m or m.group(2) == "log" or insight._is_test_line(ln):
            continue
        if m.group(3) == "True":
            ok += 1
        else:
            fail += 1

    yf = config.LOG_DIR / f"notify-{y}.log"
    y_ok = y_fail = 0
    if yf.exists():
        for ln in yf.read_text(encoding="utf-8", errors="replace").splitlines():
            m = insight._CHANNEL_LINE.match(ln)
            if not m or m.group(2) == "log" or insight._is_test_line(ln):
                continue
            if m.group(3) == "True":
                y_ok += 1
            else:
                y_fail += 1

    line(f"文件: notify-{t}.log")
    line(f"今天手算     ok={ok} fail={fail}")
    line(f"stats()      ok={s['ok']} fail={s['fail']}  today={s['today']}")
    line(f"改前会是     ok={ok + y_ok} fail={fail + y_fail}"
         f"（+昨天 ok={y_ok} fail={y_fail}）")
    assert s["ok"] == ok and s["fail"] == fail, "手算和 stats 不一致"
    line("✅ 与手算一致")
    line(f"（对照：任务书里的旧值 fail=66，今天真实失败 {fail}）")


# ---------------------------------------------------------------- 3 / 4


def _health_pair(title: str, y_lines: list[str]) -> None:
    head(title)
    tmp = _setup(y_lines, [])
    try:
        cfg = config.load()
        last = bridge._last_send_times()
        text, level = insight.chain_sentence(cfg, last)
        sub, sub_level, lit = insight.channel_state(
            "wxmp", config.get_channel(cfg, "wxmp") or {}, last.get("wxmp"))
        health = insight.delivery_health(["wxmp"], last)
        line(f"_last_send_times() = {last}")
        line(f"delivery_health()  = {health!r}")
        line(f"chain_sentence()   = ({level}) {text}")
        line(f"channel_state()    = ({sub_level}, lit={lit}) {sub}")
        return last, health, level, sub_level, sub
    finally:
        scen.clean(tmp)


def check34() -> None:
    last, health, level, sub_level, sub = _health_pair(
        "3 · 昨天有失败、今天无记录 → 不许回绿",
        [_ch(False, "23:50:00")])
    assert health == "fail", health
    assert level == "danger", level
    assert sub_level == "danger", sub_level
    assert sub.startswith("最近一次失败"), sub
    line("✅ 仍是 danger + 「最近一次失败」")

    last, health, level, sub_level, sub = _health_pair(
        "4 · 昨天全成功、今天无记录 → 不许误报（与第 3 条成对）",
        [_ch(True, "23:50:00")])
    assert health == "ok", health
    assert level == "ok", level
    assert sub_level == "ok", sub_level
    assert sub == "已启用", sub
    line("✅ 仍是 ok：昨天成功就是成功，不许因为「不是今天」降级")


# ---------------------------------------------------------------- 5


def check5() -> None:
    head("5 · 今天覆盖昨天（昨天失败 + 今天成功 → ok）")
    tmp = _setup([_ch(False, "23:50:00")], [_ch(True, "08:00:00")])
    try:
        cfg = config.load()
        last = bridge._last_send_times()
        health = insight.delivery_health(["wxmp"], last)
        text, level = insight.chain_sentence(cfg, last)
        line(f"_last_send_times() = {last}")
        line(f"day={last['wxmp']['day']!r} time={last['wxmp']['time']!r} "
             f"ok={last['wxmp']['ok']}")
        line(f"delivery_health()  = {health!r}")
        line(f"chain_sentence()   = ({level}) {text}")
        assert last["wxmp"]["day"] == "today"
        assert health == "ok" and level == "ok"
        line("✅ 今天那条赢，昨天的失败不再算「最近一次」")
    finally:
        scen.clean(tmp)


# ---------------------------------------------------------------- 6


def check6() -> None:
    head("6 · last 三种文本")
    y, t = _days()

    tmp = _setup([_ch(False, "23:50:00")], [])
    try:
        rows = {r["type"]: r for r in bridge.Bridge().channels_list()["data"]}
        got = rows["wxmp"]["last"]
        line(f"只有昨天  → last={got!r}")
        assert got == "昨天 23:50", got
    finally:
        scen.clean(tmp)

    tmp = _setup([], [_ch(True, "22:39:00")])
    try:
        rows = {r["type"]: r for r in bridge.Bridge().channels_list()["data"]}
        got = rows["wxmp"]["last"]
        line(f"只有今天  → last={got!r}")
        assert got == "22:39", got
    finally:
        scen.clean(tmp)

    tmp = _setup([], [])
    try:
        rows = {r["type"]: r for r in bridge.Bridge().channels_list()["data"]}
        got = rows["wxmp"]["last"]
        line(f"两天都没有 → last={got!r}（前端显示「未发送」）")
        assert got is None, got
        line("✅ 三种文本都对")
    finally:
        scen.clean(tmp)


def main() -> int:
    check1()
    check2()
    check34()
    check5()
    check6()
    line()
    line("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
