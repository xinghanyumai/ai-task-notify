"""W3.7 验收：测试记录与真实投递分离（D19）。

六个场景，1/2 与 3/4 成对——只做第 1 条会把测试彻底忽略（连昨天真失败
也忽略），只做第 3 条测试失败又会让列表变红。两条一起才说明两个槽真的分开了。

    python tools/accept_w37.py
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

REMOTE = ["wxmp"]


def line(s: str = "") -> None:
    print(s, flush=True)


def head(title: str) -> None:
    line()
    line("=" * 72)
    line(title)
    line("=" * 72)


def _days() -> tuple[str, str]:
    now = datetime.date.today()
    return ((now - datetime.timedelta(days=1)).isoformat(), now.isoformat())


def _real(ok: bool, t: str) -> str:
    return (f"[{t}] channel=wxmp ok={'True' if ok else 'False'} "
            f"msg={'ok' if ok else 'errcode=40037 模板 ID 无效'}")


def _test(ok: bool, t: str) -> str:
    return (f"[{t}] test-send type=wxmp ok={'True' if ok else 'False'} "
            f"msg={'ok' if ok else 'errcode=40037 模板 ID 无效'}")


def _setup(day_lines: list[str], today_lines: list[str]):
    tmp = Path(tempfile.mkdtemp(prefix="wbn-w37-"))
    scen._isolate(tmp)
    cfg = scen._base_cfg()
    scen._ch(cfg, "wxmp").update({
        "appid": "a", "secret": "s", "template_id": "t", "openid": "o",
        "enabled": True, "manual_off": False,
    })
    config.save(cfg)
    y, t = _days()
    for day, lines in ((y, day_lines), (t, today_lines)):
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        (config.LOG_DIR / f"notify-{day}.log").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
    return tmp


def _report(title: str, day_lines: list[str], today_lines: list[str]) -> dict:
    head(title)
    tmp = _setup(day_lines, today_lines)
    try:
        cfg = config.load()
        last = bridge._last_send_times()
        rec = last.get("wxmp")
        health = insight.delivery_health(REMOTE, last)
        text, level = insight.chain_sentence(cfg, last)
        sub, sub_level, lit = insight.channel_state(
            "wxmp", config.get_channel(cfg, "wxmp") or {}, rec)
        rows = {r["type"]: r for r in bridge.Bridge().channels_list()["data"]}

        line(f"_last_send_times() = {rec}")
        line(f"delivery_health()  = {health!r}")
        line(f"chain_sentence()   = ({level}) {text}")
        line(f"channel_state()    = ({sub_level}, lit={lit}) {sub}")
        line(f"channels_list.last = {rows['wxmp']['last']!r}")
        line(f"channels_list.level= {rows['wxmp']['level']!r}")
        return {"rec": rec, "health": health, "level": level,
                "sub_level": sub_level, "sub": sub,
                "last": rows["wxmp"]["last"]}
    finally:
        scen.clean(tmp)


def check1() -> None:
    r = _report("1 · 今天只有一次测试失败 → 上下必须一致（绿）", [], [_test(False, "08:00:00")])
    assert r["rec"]["ok"] is None, r["rec"]
    assert r["rec"]["test"] is True
    assert r["rec"]["time"] == "08:00", "时间仍显示测试那条"
    assert r["health"] == "none", r["health"]
    assert r["level"] == "ok", r["level"]
    assert r["sub"] == "已启用", r["sub"]
    assert r["sub_level"] == "ok", r["sub_level"]
    line("✅ 侧栏绿 + 列表绿，不再一红一绿")


def check2() -> None:
    r = _report("2 · 今天只有一次测试成功 → 同上（成功也不代表投递过）",
                [], [_test(True, "08:00:00")])
    assert r["rec"]["ok"] is None, "测试成功也不许把 ok 写成 True"
    assert r["health"] == "none"
    assert r["level"] == "ok" and r["sub_level"] == "ok"
    line("✅ 测试成功同样不构成「投递过」")


def check3() -> None:
    r = _report("3 · 昨天真实失败 + 今天测试 → 不许被遮蔽（红）",
                [_real(False, "23:50:00")], [_test(False, "08:00:00")])
    assert r["rec"]["ok"] is False, "ok 必须来自昨天那条真实失败"
    assert r["rec"]["ok_day"] == "yesterday", r["rec"]
    assert r["rec"]["time"] == "08:00", "时间仍取今天那条测试"
    assert r["rec"]["test"] is True
    assert r["health"] == "fail", r["health"]
    assert r["level"] == "danger", r["level"]
    assert r["sub_level"] == "danger", r["sub_level"]
    assert r["sub"].startswith("最近一次失败"), r["sub"]
    assert r["last"] == "08:00", "「最后发送时间」显示今天的测试"
    line("✅ 测试记录不再遮蔽昨天的真实失败；时间仍显示今天 08:00")


def check4() -> None:
    r = _report("4 · 昨天真实失败 + 今天真实成功 → 今天赢（绿）",
                [_real(False, "23:50:00")], [_real(True, "09:00:00")])
    assert r["rec"]["ok"] is True, r["rec"]
    assert r["rec"]["ok_day"] == "today"
    assert r["rec"]["test"] is False
    assert r["health"] == "ok" and r["level"] == "ok" and r["sub_level"] == "ok"
    assert r["last"] == "09:00"
    line("✅ ok_day=today，全绿")


def check5() -> None:
    r = _report("5 · 昨天真实成功 + 今天测试失败 → 仍绿",
                [_real(True, "23:50:00")], [_test(False, "08:00:00")])
    assert r["rec"]["ok"] is True, "昨天的成功仍是最近一次真实投递"
    assert r["rec"]["ok_day"] == "yesterday"
    assert r["rec"]["time"] == "08:00"
    assert r["health"] == "ok" and r["level"] == "ok" and r["sub_level"] == "ok"
    line("✅ 测试失败不许把「昨天送到过」改判成失败")


def _old_stats() -> dict:
    """用 git HEAD 版的 `insight.py` 跑同一份日志，作为「没动」的对照。

    硬编码 `29` 会随真实日志漂移（每跑一次通知就多一条），而这次要证明的是
    **「这次改动没有改变任何数字」**——拿改前的代码跑同一份数据才是那个命题。
    """
    import importlib.util
    import subprocess

    src = subprocess.run(
        ["git", "show", "HEAD:wbn/insight.py"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout
    tmp = Path(tempfile.mkdtemp(prefix="wbn-oldinsight-")) / "old_insight.py"
    tmp.write_text(src, encoding="utf-8")
    # 模块名带 `wbn.` 前缀，否则文件里的 `from . import channels` 会报
    # 「attempted relative import with no known parent package」。
    spec = importlib.util.spec_from_file_location("wbn._old_insight_tmp", tmp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.stats(config.load())


def check6() -> None:
    head("6 · 真机三个数不许动（与 git HEAD 版并排）")
    scen._isolate(ROOT)
    s = insight.stats(config.load())
    old = _old_stats()
    line(f"HEAD 版 stats() → today={old['today']} ok={old['ok']} fail={old['fail']}")
    line(f"本次 stats()    → today={s['today']} ok={s['ok']} fail={s['fail']}")
    line("（W3.6 定稿时是 today=2 ok=2 fail=29；之后真机又跑了 2 次通知，"
         "都是微信失败，所以现在 fail 比那时多）")
    assert (s["today"], s["ok"], s["fail"]) == (
        old["today"], old["ok"], old["fail"]), "三个数被改动了"
    line("✅ 与改前逐字相同：这次只动了「最近一次」的取数结构，没碰计数")


def main() -> int:
    check1()
    check2()
    check3()
    check4()
    check5()
    check6()
    line()
    line("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
