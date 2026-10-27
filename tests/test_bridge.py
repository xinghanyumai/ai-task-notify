"""`wbn/bridge.py` 的守卫。

这层代码的失败方式全是**静默**的，所以每条都要钉住：

- 返回值不可序列化 → 前端收到 `undefined`，不报错，页面上少一行字
- 异常外抛 → 前端 Promise reject，只显示 `[object Object]`
- `config_save` 拿内存整份覆盖 → 不报错，只是「改了没保存」或「别的改动没了」

第三条最贵：它会悄无声息地吃掉 hook 进程在 UI 打开期间写进去的改动。
所以 `test_save_keeps_changes_written_by_other_process` 是本文件的重点。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from wbn import bridge, config

# ---------------------------------------------------------------- _guard


def test_guard_wraps_success_as_ok_data():
    @bridge._guard
    def fn():
        return {"a": 1}

    assert fn() == {"ok": True, "data": {"a": 1}}


def test_guard_swallows_exception_into_ok_false():
    """异常不许外抛。前端只认 ok 字段，抛出去它就只看到 [object Object]。"""

    @bridge._guard
    def fn():
        raise ValueError("坏了")

    res = fn()
    assert res["ok"] is False
    assert "ValueError" in res["error"] and "坏了" in res["error"]


def test_guard_redacts_credentials_in_error():
    """错误信息会进前端、也可能进日志，凭证必须打掉。"""

    @bridge._guard
    def fn():
        raise RuntimeError("请求失败 secret=abcdef123456")

    err = fn()["error"]
    assert "abcdef123456" not in err
    assert "abc***56" in err


def test_guard_rejects_unserializable_return():
    """返回 Path / datetime 时不能静默变成 undefined，要当场变成 ok=false。"""

    @bridge._guard
    def fn():
        return {"p": config.CONFIG_PATH}      # Path 不是 JSON 类型

    res = fn()
    assert res["ok"] is False
    assert "JSON" in res["error"]


def test_guard_truncates_long_error():
    @bridge._guard
    def fn():
        raise RuntimeError("x" * 2000)

    assert len(fn()["error"]) <= 300


# ------------------------------------------------------------ config_get


def test_config_get_returns_serializable_payload():
    res = bridge.Bridge().config_get()
    assert res["ok"] is True
    json.dumps(res)          # 整包必须能过 JSON，否则前端拿到 undefined
    data = res["data"]
    assert isinstance(data["path"], str)
    assert data["path"].endswith("config.json")
    assert data["theme"] in ("light", "dark", "auto", "system")


def test_config_get_reflects_disk(isolated_state):
    """读的是磁盘，不是某个内存缓存。"""
    cfg = config.load()
    cfg["app_name"] = "磁盘上的名字"
    config.save(cfg)

    data = bridge.Bridge().config_get()["data"]
    assert data["cfg"]["app_name"] == "磁盘上的名字"


# ----------------------------------------------------------- config_save


def test_config_save_only_touches_patched_field(isolated_state):
    before = config.load()
    res = bridge.Bridge().config_save({"enabled": False})
    assert res["ok"] is True

    after = config.load()
    assert after["enabled"] is False
    # 其它顶层字段一个没动
    for key in ("app_name", "triggers", "filters", "templates", "titles", "advanced", "ui"):
        assert after[key] == before[key], f"{key} 被意外改动"
    # 10 个通道和它们的凭证都还在
    assert len(after["channels"]) == len(before["channels"]) == 10
    assert after["channels"] == before["channels"]


def test_save_keeps_changes_written_by_other_process(isolated_state):
    """**读-改-写**的意义所在。

    场景：UI 开着 → hook 进程把 triggers.task_done 改成 False → 用户回窗口
    点保存。如果 config_save 拿的是 UI 启动时读的那份内存副本，这次保存会
    把 task_done 悄悄改回 True，用户完全看不出来。
    """
    stale = config.load()                 # UI 启动时读到的那一份
    assert stale["triggers"]["task_done"] is True

    fresh = config.load()                 # hook 进程改了磁盘
    fresh["triggers"]["task_done"] = False
    fresh["advanced"]["http_timeout"] = 30
    config.save(fresh)

    bridge.Bridge().config_save({"enabled": False})   # 用户只改了 enabled

    disk = config.load()
    assert disk["triggers"]["task_done"] is False, "别的进程写的改动被冲掉了"
    assert disk["advanced"]["http_timeout"] == 30
    assert disk["enabled"] is False


def test_save_channels_does_not_wipe_other_channels(isolated_state):
    """只传一个通道时，其它通道的凭证不能被整条替换掉。"""
    cfg = config.load()
    smtp = config.get_channel(cfg, "smtp")
    smtp["user"] = "me@example.com"
    smtp["password"] = "授权码不该丢"
    config.save(cfg)

    res = bridge.Bridge().config_save({
        "channels": [{"type": "bark", "enabled": True, "key": "barkkey"}]
    })
    assert res["ok"] is True

    disk = config.load()
    assert config.get_channel(disk, "smtp")["password"] == "授权码不该丢"
    bark = config.get_channel(disk, "bark")
    assert bark["key"] == "barkkey"
    assert bark["server"] == "https://api.day.app"      # 没传的字段保留默认值


def test_save_returns_auto_enabled_channels(isolated_state):
    """填齐凭证后自动启用，是现有行为，bridge 不能弄丢。"""
    res = bridge.Bridge().config_save({
        "channels": [{"type": "bark", "key": "k" * 20}]
    })
    assert res["ok"] is True
    assert any("Bark" in str(x) for x in res["data"]["turned_on"])


def test_save_rejects_non_dict_patch():
    res = bridge.Bridge().config_save(["不是对象"])
    assert res["ok"] is False
    assert "patch" in res["error"]


def test_save_with_empty_patch_changes_nothing(isolated_state):
    before = config.load()
    assert bridge.Bridge().config_save({})["ok"] is True
    assert config.load() == before


# ---------------------------------------------------------- 状态页三个只读方法


def test_stats_today_matches_insight_stats(isolated_state):
    """四张统计卡的数字必须和 `insight.stats()` 逐项相等。

    这一条是 W2 验收第 3 条（「和 insight.stats() 输出逐项对照」）的机器版：
    只要有人给 stats() 改了统计口径而忘了同步 bridge，这里立刻红。
    """
    from wbn import insight

    want = insight.stats(config.load())
    got = bridge.Bridge().stats_today()["data"]

    assert got["today"] == want["today"]
    assert got["ok"] == want["ok"]
    assert got["fail"] == want["fail"]
    assert got["enabled_channels"] == want["channels"]
    assert got["recent"] == want["recent"]


def test_stats_today_supplies_denominator(isolated_state):
    """`channels` 改名成 `enabled_channels`，另给 `total_channels` 做分母。

    原名和「通道总数」只差两个字，前端写 `d.channels` 迟早当成分母用，
    「启用通道 2/2」就会冒充「2/10」。
    """
    got = bridge.Bridge().stats_today()["data"]
    assert got["total_channels"] == len(config.load()["channels"]) == 10
    assert "channels" not in got


def test_chain_sentence_matches_insight(isolated_state):
    from wbn import insight

    want_text, want_level = insight.chain_sentence(config.load())
    got = bridge.Bridge().chain_sentence()["data"]
    assert got == {"text": want_text, "level": want_level}


def test_chain_sentence_tracks_mute(isolated_state):
    """静音之后那句话必须跟着变——侧栏说「已静音」而顶栏开关还绿着，
    是比没有这句话更糟的体验。"""
    bridge.Bridge().config_save({"enabled": False})
    got = bridge.Bridge().chain_sentence()["data"]
    assert got["level"] == "warn"
    assert "静音" in got["text"]


def test_channels_list_covers_every_channel(isolated_state):
    """10 条通道一条不漏，状态文案来自 `insight.channel_state`，一字不差。"""
    from wbn import insight

    cfg = config.load()
    rows = bridge.Bridge().channels_list()["data"]
    assert len(rows) == 10

    for row in rows:
        want = insight.channel_state(row["type"], config.get_channel(cfg, row["type"]))
        assert (row["state"], row["level"], row["on"]) == want
        assert row["label"], f"{row['type']} 没有短名"
        assert isinstance(row["last"], (str, type(None)))


def test_channels_list_reads_last_send_from_today_log(isolated_state):
    """「最后发送时间」取今天日志里该通道的**最后**一条，不是第一条。

    三种行都要认：普通发送、测试发送（`test` 前缀让 channel= 不紧跟时间戳）、
    以及 `event=Stop ... remote_ok=False` 那种不含 `channel=` 的汇总行（要跳过）。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    (config.LOG_DIR / f"notify-{today}.log").write_text(
        "[00:06:38] channel=wxmp ok=False msg=errcode=40037 模板 ID 无效\n"
        "[00:06:39] test channel=log ok=True msg=已记录\n"
        "[09:12:00] channel=wxmp ok=True msg=ok\n"
        "[09:13:00] event=Stop kind=task_done sent=True remote_ok=False elapsed=1.0s\n",
        encoding="utf-8",
    )

    by_type = {r["type"]: r for r in bridge.Bridge().channels_list()["data"]}
    assert by_type["wxmp"]["last"] == "09:12"
    assert by_type["log"]["last"] == "00:06"
    assert by_type["bark"]["last"] is None


def test_channels_list_survives_missing_log(isolated_state):
    """今天还没有日志文件是很常见的（刚过零点）。不许因此整页报错。"""
    rows = bridge.Bridge().channels_list()["data"]
    assert len(rows) == 10
    assert all(r["last"] is None for r in rows)


def test_read_only_methods_do_not_touch_disk(isolated_state):
    """三个方法都是只读的。写盘只允许发生在 config_save 里。"""
    config.save_default_if_missing()
    path = config.CONFIG_PATH
    before = path.read_bytes()

    b = bridge.Bridge()
    b.stats_today()
    b.chain_sentence()
    b.channels_list()

    assert path.read_bytes() == before


def test_read_only_methods_feed_the_liveness_probe(isolated_state):
    """状态页是首屏，靠它调通 bridge 来证明 WebView2 真的画出来了。"""
    hits = []
    b = bridge.Bridge(on_activity=lambda: hits.append(1))
    b.stats_today()
    b.chain_sentence()
    b.channels_list()
    assert len(hits) == 3


# ---------------------------------------------------------- 接口面约束


def test_bridge_exposes_only_implemented_methods():
    """没有「存在但一调就报错」的空壳方法。

    空壳会让前端的特性检测（`if (api.channels_list)`）误判为已实现，
    比完全没有更难查。W3 加通道页时再一起加（channel_test / open_path /
    app_quit）。
    """
    public = {
        n for n in dir(bridge.Bridge)
        if not n.startswith("_") and callable(getattr(bridge.Bridge, n))
    }
    assert public == {
        "config_get", "config_save",
        # W2 状态页要的三个只读方法
        "stats_today", "chain_sentence", "channels_list",
    }


def test_bridge_does_not_import_webview():
    """bridge 是纯 Python 逻辑，不许绑死到某个 GUI 库——它要被 pytest 直接调用。"""
    src = (config.ROOT / "wbn" / "bridge.py").read_text(encoding="utf-8")
    assert "import webview" not in src
    assert "import tkinter" not in src


# ---------------------------------------------------------- 存活探针


def test_activity_hook_fires_on_both_methods(isolated_state):
    """app.py 靠这个判断「WebView2 到底画没画出来」。

    只建了窗口、页面一次都没画的情况下，Python 侧看不出任何异常——
    唯一可信的判据是前端有没有调通 bridge。所以心跳必须挂在真方法上。
    """
    hits = []
    b = bridge.Bridge(on_activity=lambda: hits.append(1))
    b.config_get()
    b.config_save({"enabled": False})
    assert len(hits) == 2


def test_activity_hook_failure_does_not_break_calls(isolated_state):
    """探针自己抛异常，不能连累正常调用。"""

    def boom():
        raise RuntimeError("探针炸了")

    b = bridge.Bridge(on_activity=boom)
    assert b.config_get()["ok"] is True
    assert b.config_save({"enabled": False})["ok"] is True


def test_bridge_without_hook_still_works(isolated_state):
    """不传 on_activity 时行为完全不变（pytest 默认就是这样）。"""
    b = bridge.Bridge()
    assert b.config_get()["ok"] is True


@pytest.mark.parametrize("patch", [{"enabled": False}, {"app_name": "x"}, {}])
def test_every_result_is_json_serializable(isolated_state, patch):
    res = bridge.Bridge().config_save(patch)
    json.dumps(res, ensure_ascii=False)


def test_merge_save_is_used_not_plain_save(isolated_state, monkeypatch):
    """钉住实现路径：config_save 必须走 merge_save，不许自己调 config.save。"""
    called = {}

    def fake_merge_save(patch):
        called["patch"] = patch
        return {"ok": True, "turned_on": [], "turned_off": [], "cfg": {}}

    monkeypatch.setattr(bridge.insight, "merge_save", fake_merge_save)
    monkeypatch.setattr(
        bridge.config, "save",
        lambda cfg: pytest.fail("config_save 不许直接整份覆盖"),
    )
    assert bridge.Bridge().config_save({"enabled": False})["ok"] is True
    assert called["patch"] == {"enabled": False}
