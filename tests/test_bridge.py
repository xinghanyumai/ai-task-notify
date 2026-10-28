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
import threading
import time
from datetime import datetime, timedelta

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

    三种行都要认：普通发送、测试发送（`test-send type=` 是 W3.1 起的写法，
    `test channel=` 是历史写法，两种都得认——否则用户刚点完测试，
    列表上还显示昨天的时间）、以及 `event=Stop … remote_ok=False`
    那种不含通道字段的汇总行（要跳过）。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    (config.LOG_DIR / f"notify-{today}.log").write_text(
        "[00:06:38] channel=wxmp ok=False msg=errcode=40037 模板 ID 无效\n"
        "[00:06:39] test channel=log ok=True msg=已记录\n"
        "[09:12:00] channel=wxmp ok=True msg=ok\n"
        "[09:13:00] test-send type=bark ok=False msg=TimeoutError: timed out\n"
        "[09:14:00] event=Stop kind=task_done sent=True remote_ok=False elapsed=1.0s\n",
        encoding="utf-8",
    )

    by_type = {r["type"]: r for r in bridge.Bridge().channels_list()["data"]}
    assert by_type["wxmp"]["last"] == "09:12"
    assert by_type["log"]["last"] == "00:06"
    assert by_type["bark"]["last"] == "09:13", "新写法的测试发送也要认"


def test_channels_list_survives_missing_log(isolated_state):
    """今天还没有日志文件是很常见的（刚过零点）。不许因此整页报错。"""
    rows = bridge.Bridge().channels_list()["data"]
    assert len(rows) == 10
    assert all(r["last"] is None for r in rows)


# --------------------------------------------------- W3.4 投递健康信号（bridge）


def _ready_two_remote() -> None:
    """把 wxmp / bark 填齐并启用，其余关掉——这样缺字段不会抢在前面说话。"""
    cfg = config.load()
    for ch in cfg["channels"]:
        t = ch["type"]
        if t == "wxmp":
            ch.update({"appid": "a", "secret": "s", "template_id": "t", "enabled": True})
        elif t == "bark":
            ch.update({"key": "k", "enabled": True})
        else:
            ch["enabled"] = False
    cfg["enabled"] = True
    cfg["triggers"] = {k: (k == "task_done") for k in cfg["triggers"]}
    config.save(cfg)


def test_channels_list_marks_the_channel_that_failed(isolated_state):
    """**W3.4 验收第 6 条：列表里失败的通道要自己说出来。**

    微信连续失败十几次，列表还写「已启用」+ 绿点，就是界面替失败背书。
    """
    _ready_two_remote()
    today = datetime.now().strftime("%Y-%m-%d")
    (config.LOG_DIR / f"notify-{today}.log").write_text(
        "[09:12:00] channel=wxmp ok=False "
        "msg=errcode=40037 模板 ID 无效，请到公众平台核对\n"
        "[09:12:30] channel=bark ok=True msg=ok\n",
        encoding="utf-8",
    )
    by_type = {r["type"]: r for r in bridge.Bridge().channels_list()["data"]}
    assert by_type["wxmp"]["level"] == "danger"
    assert by_type["wxmp"]["on"] is True, "已启用就该点亮，失败不是「关掉」"
    assert by_type["wxmp"]["state"].startswith("最近一次失败：")
    assert by_type["bark"]["level"] == "ok"
    assert by_type["bark"]["state"] == "已启用"


def test_chain_sentence_reads_today_log(isolated_state):
    """**W3.4 验收第 3 条：侧栏那句话要跟着日志走。**

    配置没变，只有日志里多了一条失败——那句话必须降级。
    """
    _ready_two_remote()

    before = bridge.Bridge().chain_sentence()["data"]
    assert before["level"] == "ok", "空日志不该降级"
    assert "送到你手机" in before["text"]

    today = datetime.now().strftime("%Y-%m-%d")
    (config.LOG_DIR / f"notify-{today}.log").write_text(
        "[09:12:00] channel=wxmp ok=False msg=errcode=40037 模板 ID 无效\n",
        encoding="utf-8",
    )
    after = bridge.Bridge().chain_sentence()["data"]
    # 只有 wxmp 有记录、且它失败了 → 已记录的全挂 = fail。bark 今天没试过，
    # 不参与判定（不是把它算成失败，也不是把它算成成功）。
    assert after["level"] == "danger"
    assert "最近一次没送到" in after["text"]
    assert "errcode" not in after["text"], "报错原文只配去通道页"

    (config.LOG_DIR / f"notify-{today}.log").write_text(
        "[09:12:00] channel=wxmp ok=False msg=errcode=40037 模板 ID 无效\n"
        "[09:12:30] channel=bark ok=True msg=ok\n",
        encoding="utf-8",
    )
    part = bridge.Bridge().chain_sentence()["data"]
    assert part["level"] == "warn", "一成一败才是部分"
    assert "部分没送到" in part["text"]

    (config.LOG_DIR / f"notify-{today}.log").write_text(
        "[09:12:00] channel=wxmp ok=False msg=errcode=40037 模板 ID 无效\n"
        "[09:12:30] channel=bark ok=False msg=TimeoutError: timed out\n",
        encoding="utf-8",
    )
    both = bridge.Bridge().chain_sentence()["data"]
    assert both["level"] == "danger"
    assert "最近一次没送到" in both["text"]


def test_last_send_times_redacts_before_truncating():
    """**先脱敏再截断，顺序反了就是泄漏。**

    `msg` 里可能带 token / key。如果先按 120 字截断，凭证正好跨在边界上
    就会被切成半截——半截凭证照样是可用的线索。脱敏在前，截断在后。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    pad = "a" * 100
    secret = "S3CR3T-VALUE-9f8e7d6c5b4a"
    (config.LOG_DIR / f"notify-{today}.log").write_text(
        f"[09:12:00] channel=bark ok=False msg={pad} token={secret}\n",
        encoding="utf-8",
    )
    rec = bridge._last_send_times()["bark"]
    assert rec["ok"] is False
    assert secret not in rec["msg"], "凭证原文漏出来了"
    assert secret[:10] not in rec["msg"], f"截断切在半截凭证上：{rec['msg']}"
    assert len(rec["msg"]) <= 120


def test_last_send_times_keeps_spaces_in_msg():
    """报错原文里有空格（`errcode=40037 模板 ID 无效`），不许按空白切碎。"""
    today = datetime.now().strftime("%Y-%m-%d")
    (config.LOG_DIR / f"notify-{today}.log").write_text(
        "[09:12:00] channel=wxmp ok=False msg=errcode=40037 模板 ID 无效\n",
        encoding="utf-8",
    )
    rec = bridge._last_send_times()["wxmp"]
    assert "模板 ID 无效" in rec["msg"]


# --------------------------------------------------- W3.5 测试发送 / 关窗拦截


def test_last_send_times_marks_test_lines():
    """**W3.5 验收第 4 条：读出来，但标上是不是测试。**

    只读不判：`time` 要用（刚点完测试，列表显示刚刚是对的），
    `ok` 不能用（测试用的是表单里没保存的值）。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    (config.LOG_DIR / f"notify-{today}.log").write_text(
        "[09:12:00] test-send type=wxmp ok=False msg=errcode=40037\n"
        "[09:13:00] test channel=bark ok=True msg=HTTP 200\n"
        "[09:14:00] channel=webhook ok=True msg=HTTP 200\n",
        encoding="utf-8",
    )
    got = bridge._last_send_times()
    assert got["wxmp"]["test"] is True
    assert got["bark"]["test"] is True, "W3.1 之前的历史写法也要认"
    assert got["webhook"]["test"] is False
    # 时间照给，测试行也算
    assert got["wxmp"]["time"] == "09:12"


def test_chain_sentence_ignores_test_failure(isolated_state):
    """**W3.5 验收第 4 条：只有测试失败 → 侧栏仍是 ok。**

    真实失败才降级。两条断言必须挨着写，否则你分不清是「排掉了测试」
    还是「什么都没看见」。
    """
    _ready_two_remote()
    today = datetime.now().strftime("%Y-%m-%d")
    log = config.LOG_DIR / f"notify-{today}.log"

    log.write_text("[09:12:00] test-send type=wxmp ok=False msg=errcode=40037\n",
                   encoding="utf-8")
    after_test = bridge.Bridge().chain_sentence()["data"]
    assert after_test["level"] == "ok", "一次故意的试探不许把侧栏打成红色"
    assert "送到你手机" in after_test["text"]

    log.write_text("[09:12:00] test-send type=wxmp ok=False msg=errcode=40037\n"
                   "[09:13:00] channel=wxmp ok=False msg=errcode=40037\n",
                   encoding="utf-8")
    after_real = bridge.Bridge().chain_sentence()["data"]
    assert after_real["level"] == "danger"
    assert "最近一次没送到" in after_real["text"]


def test_set_dirty_only_stores_a_flag():
    """后端只存标志，不做判断。「什么算改动」只有前端一份。"""
    b = bridge.Bridge()
    assert b._unsaved is False
    assert b.set_dirty(True)["data"] == {"dirty": True}
    assert b._unsaved is True
    assert b.set_dirty(False)["data"] == {"dirty": False}
    assert b._unsaved is False


def test_set_dirty_accepts_truthy_junk():
    """JS 传过来的可能是 "true" / 1 / null，不许因此抛异常把窗口卡住。"""
    b = bridge.Bridge()
    b.set_dirty("true")
    assert b._unsaved is True
    b.set_dirty(None)
    assert b._unsaved is False


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


# ---------------------------------------------------------- 通道页：结构 / 状态


def test_groups_cover_every_channel_exactly_once(isolated_state):
    """分组必须把 10 个通道一个不漏地分掉，且不重复。

    漏掉的通道在界面上永远点不到——而 `CHANNEL_META` 里还有它，
    写配置的人会以为是自己填错了。
    """
    data = bridge.Bridge().channels_meta()["data"]
    flat = [t for g in data["groups"] for t in g["types"]]
    assert sorted(flat) == sorted(data["meta"])
    assert len(flat) == len(set(flat)) == 10


def test_meta_fields_are_objects_not_tuples(isolated_state):
    """元组要转成对象：JSON 里没有元组，前端也不能按下标取。"""
    data = bridge.Bridge().channels_meta()["data"]
    for ctype, m in data["meta"].items():
        assert m["label"] and m["short"], ctype
        for f in m["fields"]:
            assert set(f) == {"key", "label", "type", "default", "hint"}, ctype
            assert f["type"] in ("text", "password", "bool", "int", "textarea")


def test_meta_is_generated_from_channel_meta(isolated_state, monkeypatch):
    """**验收第 2 条的机器版**：给 bark 加一个假字段，meta 里必须自动多一条。

    这条证明表单是 schema 驱动的，不是把 10 套字段抄了 10 遍。
    """
    from wbn import channels as ch

    extra = ("fake_field", "假字段", "text", "x", "验收用的临时字段")
    monkeypatch.setitem(ch.CHANNEL_META["bark"], "fields",
                        list(ch.CHANNEL_META["bark"]["fields"]) + [extra])

    fields = bridge.Bridge().channels_meta()["data"]["meta"]["bark"]["fields"]
    assert [f["key"] for f in fields][-1] == "fake_field"
    assert fields[-1]["label"] == "假字段"


def test_required_comes_from_channels(isolated_state):
    from wbn import channels as ch

    data = bridge.Bridge().channels_meta()["data"]
    assert data["required"] == {k: list(v) for k, v in ch.REQUIRED.items()}


def test_timeouts_are_dispatched_with_the_meta(isolated_state):
    """前端兜底超时必须由后端下发，不许自己写死。

    后端一次发送最坏要串两次请求（换 token + 发消息），前端比后端短就会
    「后端成功了、界面报超时」，然后真实结果被 `_push()` 丢弃。
    """
    from wbn import channels as ch

    t = bridge.Bridge().channels_meta()["data"]["timeouts"]
    assert t == {"token": ch.TOKEN_TIMEOUT, "send": 8}


def test_timeouts_follow_advanced_http_timeout(isolated_state):
    """`advanced.http_timeout` 改了要立刻反映到下发的值上。

    三个取值对应前端 20s / 20s（下限）/ 32s，见 W3.1 验收第 3 条。
    """
    for want in (3, 8, 20):
        cfg = config.load()
        cfg.setdefault("advanced", {})["http_timeout"] = want
        config.save(cfg)
        t = bridge.Bridge().channels_meta()["data"]["timeouts"]
        assert t["send"] == want

        # 前端那段算式的 Python 版：下限 20000，余量 2000
        ms = max(20000, (t["token"] + t["send"]) * 1000 + 2000)
        assert ms == {3: 20000, 8: 20000, 20: 32000}[want]


def test_timeouts_fall_back_when_config_is_junk(isolated_state):
    """config 里写了个字符串或 null，不许让整页崩掉。"""
    cfg = config.load()
    cfg.setdefault("advanced", {})["http_timeout"] = "不是数字"
    config.save(cfg)
    assert bridge.Bridge().channels_meta()["data"]["timeouts"]["send"] == 8


def test_state_values_cover_exactly_the_schema_fields(isolated_state):
    """values 的键必须和 schema 的字段一一对应。

    多一个（比如混进 enabled）就会在前端被当成输入框渲染出来；
    少一个则该字段永远是空的，用户填了保存也会丢。
    """
    meta = bridge.Bridge().channels_meta()["data"]["meta"]
    for row in bridge.Bridge().channels_state()["data"]:
        keys = [f["key"] for f in meta[row["type"]]["fields"]]
        assert sorted(row["values"]) == sorted(keys), row["type"]


def test_state_keeps_bool_and_int_shapes(isolated_state):
    """bool 必须是真布尔，int 必须是字符串——两个都有具体后果。

    `_send_smtp` 写的是 `if c.get("ssl", True)`：字符串 "false" 是真值，
    传字符串会静默变成「永远开 SSL」。
    而 port 在现有 config.json 里就是 "465"，跟着现状走，
    免得存回去时 `git diff config.json` 冒出一堆无意义的引号增删。
    """
    rows = {r["type"]: r for r in bridge.Bridge().channels_state()["data"]}
    assert rows["smtp"]["values"]["ssl"] is True
    assert rows["smtp"]["values"]["port"] == "465"
    assert rows["wxmp"]["values"]["wrap"] is True
    assert rows["ntfy"]["values"]["priority"] == "3"


def test_state_subtitle_matches_insight(isolated_state):
    """副标题只许有一份判定逻辑，否则两版界面对同一份配置说两句话。"""
    from wbn import insight

    cfg = config.load()
    for row in bridge.Bridge().channels_state()["data"]:
        want = insight.channel_state(row["type"],
                                     config.get_channel(cfg, row["type"]))
        assert (row["subtitle"], row["level"], row["on"]) == want


# ---------------------------------------------------------- 通道页：异步测试


class FakeWindow:
    """只记 evaluate_js 的脚本，不真跑。"""

    def __init__(self) -> None:
        self.scripts: list[str] = []
        self.pushed = threading.Event()

    def evaluate_js(self, script: str) -> None:
        self.scripts.append(script)
        self.pushed.set()


def _payload(script: str) -> dict:
    """从 `window.__wbnCb({...})` 里把那个 JSON 抠出来解析。

    这一句本身就是断言：如果 payload 没转义好、把脚本拼断了，
    `json.loads` 会直接抛——比「肉眼看着没问题」硬得多。
    """
    prefix = "window.__wbnCb("
    assert script.startswith(prefix), script[:80]
    assert script.endswith(")"), script[-40:]
    return json.loads(script[len(prefix):-1])


def test_channel_test_returns_before_network_finishes(isolated_state, monkeypatch):
    """**8 秒超时期间主线程不能阻塞**——这条把它变成机器判据。

    channel_test 必须在 HTTP 还没回来时就返回，否则整个窗口冻住，
    Windows 会把标题栏改成「未响应」。
    """
    from wbn import insight

    started = threading.Event()
    release = threading.Event()

    def slow(cfg, ctype):
        started.set()
        release.wait(5)
        return True, "已发送"

    monkeypatch.setattr(insight, "test_channel", slow)

    win = FakeWindow()
    t0 = time.monotonic()
    res = bridge.Bridge(window=win).channel_test("bark", {}, 1)
    elapsed = time.monotonic() - t0

    assert res["ok"] is True and res["data"]["started"] is True
    assert elapsed < 0.5, f"channel_test 阻塞了 {elapsed:.2f}s"
    assert started.wait(2), "线程没起来"
    assert win.scripts == [], "结果不该在 HTTP 回来之前就推"

    release.set()
    assert win.pushed.wait(3), "结果没推回来"


def test_channel_test_pushes_ok_payload(isolated_state, monkeypatch):
    from wbn import insight

    monkeypatch.setattr(insight, "test_channel", lambda cfg, ctype: (True, "已发送"))
    win = FakeWindow()
    bridge.Bridge(window=win).channel_test("bark", {}, 42)

    assert win.pushed.wait(3)
    payload = _payload(win.scripts[0])
    assert payload == {"id": 42, "ok": True, "data": {"msg": "已发送"}}


def test_channel_test_escapes_hostile_remote_body(isolated_state, monkeypatch):
    """**evaluate_js 的注入闸门。**

    msg 是远端服务返回的 body，属于不可信文本。直接拼进 JS 字符串的话，
    一个引号就能把脚本拼断——而 window.pywebview.api 就在同一个上下文里，
    拼断之后能调 config_get() 读走全部通道密钥。
    """
    from wbn import insight

    evil = 'pwn"});alert(1);//'
    monkeypatch.setattr(insight, "test_channel", lambda cfg, ctype: (False, evil))

    win = FakeWindow()
    bridge.Bridge(window=win).channel_test("bark", {}, 9)
    assert win.pushed.wait(3)

    script = win.scripts[0]
    payload = _payload(script)          # 拼断了这里就会抛
    assert payload["data"]["msg"] == evil
    assert script.endswith("})") or script.endswith(")"), script[-20:]


def test_channel_test_escapes_line_separators(isolated_state, monkeypatch):
    """U+2028 / U+2029 在 JSON 里允许裸写，但老 JS 引擎当行分隔符。"""
    from wbn import insight

    evil = "前\u2028中\u2029后"
    monkeypatch.setattr(insight, "test_channel", lambda cfg, ctype: (False, evil))

    win = FakeWindow()
    bridge.Bridge(window=win).channel_test("bark", {}, 3)
    assert win.pushed.wait(3)

    script = win.scripts[0]
    assert "\u2028" not in script and "\u2029" not in script
    assert _payload(script)["data"]["msg"] == evil


def test_channel_test_uses_form_values_not_disk(isolated_state, monkeypatch):
    """测的是表单里当前的值，不是磁盘上那份。

    用户改完还没保存就点测试，要试的是他眼前这一份。
    """
    from wbn import insight

    seen = {}

    def spy(cfg, ctype):
        seen["key"] = config.get_channel(cfg, ctype).get("key")
        return True, "ok"

    monkeypatch.setattr(insight, "test_channel", spy)
    bridge.Bridge(window=FakeWindow()).channel_test("bark", {"key": "改过的"}, 1)

    for _ in range(50):
        if seen:
            break
        time.sleep(0.05)
    assert seen["key"] == "改过的"
    assert config.get_channel(config.load(), "bark").get("key") != "改过的", \
        "测试不许写盘"


def test_channel_test_ignores_fields_outside_schema(isolated_state, monkeypatch):
    """只认 schema 里有的字段，别的一概不写进配置。"""
    from wbn import insight

    seen = {}

    def spy(cfg, ctype):
        seen.update(config.get_channel(cfg, ctype))
        return True, "ok"

    monkeypatch.setattr(insight, "test_channel", spy)
    bridge.Bridge(window=FakeWindow()).channel_test(
        "bark", {"key": "k", "enabled": True, "随便什么": 1}, 1)

    for _ in range(50):
        if seen:
            break
        time.sleep(0.05)
    assert seen["key"] == "k"
    assert "随便什么" not in seen
    # 表单传 enabled=True，磁盘上 bark 默认是 False —— 必须还是 False。
    # 开关归「每通道独立开关」管，表单字段不许绕过去。
    assert seen["enabled"] is False, "表单里的 enabled 不许绕过开关去改配置"


def test_channel_test_rejects_unknown_channel(isolated_state):
    res = bridge.Bridge(window=FakeWindow()).channel_test("nope", {}, 1)
    assert res["ok"] is False
    assert "未知通道" in res["error"]


def test_channel_test_survives_exception_in_thread(isolated_state, monkeypatch):
    """线程里的异常没人接，必须自己兜住再推回去，否则前端永远转圈。"""
    from wbn import insight

    def boom(cfg, ctype):
        raise RuntimeError("网络炸了")

    monkeypatch.setattr(insight, "test_channel", boom)
    win = FakeWindow()
    bridge.Bridge(window=win).channel_test("bark", {}, 5)

    assert win.pushed.wait(3)
    payload = _payload(win.scripts[0])
    assert payload["ok"] is False
    assert "网络炸了" in payload["data"]["msg"]


def test_push_without_window_does_not_raise(isolated_state, monkeypatch):
    """窗口已经关了（或者 pytest 里根本没窗口）时，推送静默失败就行。"""
    from wbn import insight

    monkeypatch.setattr(insight, "test_channel", lambda cfg, ctype: (True, "ok"))
    res = bridge.Bridge().channel_test("bark", {}, 1)
    assert res["ok"] is True
    time.sleep(0.3)          # 让线程跑完，确认没有未捕获异常


def test_fetch_openid_pushes_result(isolated_state, monkeypatch):
    from wbn import insight

    monkeypatch.setattr(insight, "fetch_openid", lambda f: (["o1", "o2"], ""))
    win = FakeWindow()
    bridge.Bridge(window=win).channel_fetch_openid(
        {"appid": "a", "secret": "s", "template_id": "t"}, 11)

    assert win.pushed.wait(3)
    payload = _payload(win.scripts[0])
    assert payload["id"] == 11
    assert payload["ok"] is True
    assert payload["data"]["ids"] == ["o1", "o2"]


def test_fetch_openid_only_passes_appid_and_secret(isolated_state, monkeypatch):
    from wbn import insight

    seen = {}

    def spy(fields):
        seen.update(fields)
        return [], "先填 appID 和 appsecret"

    monkeypatch.setattr(insight, "fetch_openid", spy)
    bridge.Bridge(window=FakeWindow()).channel_fetch_openid(
        {"appid": "a", "secret": "s", "openid": "不该传"}, 12)

    for _ in range(50):
        if seen:
            break
        time.sleep(0.05)
    assert set(seen) == {"appid", "secret"}


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
        # W3 通道页：结构 / 状态 / 两个异步动作
        "channels_meta", "channels_state",
        "channel_test", "channel_fetch_openid",
        # W3.5 关窗拦截：前端只写不读，读的那一侧是私有的 `_unsaved`
        "set_dirty",
    }


def test_bridge_has_no_public_data_attributes():
    """js_api 上**只许有方法**，不许有公开的数据属性。

    现场（2026-10-28，W3）：`self.window = win` 挂上去之后，pywebview 的
    `get_functions()` 会 `dir()` 一遍再对每个公开非可调用属性递归展开，
    于是开始遍历 `window.native.AccessibilityObject.Bounds.Empty.Empty...`，
    无限递归 → API 注入失败 → 前端永远拿不到 `window.pywebview.api` →
    页面停在骨架屏 → 存活探针判死降级 tkinter。Python 侧一句报错都没有。
    """
    b = bridge.Bridge(window=FakeWindow())
    bad = [n for n in dir(b)
           if not n.startswith("_") and not callable(getattr(b, n))]
    assert bad == [], f"js_api 上不许有公开数据属性：{bad}"


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


# --------------------------------------------------- W3.6 跨天边界（D18）


def _day_file(days_ago: int, lines: list[str]) -> None:
    d = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    (config.LOG_DIR / f"notify-{d}.log").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def test_last_send_times_reads_yesterday():
    """**零点之后不能失忆。** 只读今天的话，昨天连挂 15 次的失败在 00:00
    被彻底遗忘，侧栏回到绿点「送到你手机」——而用户早上打开看到的正是这个。
    """
    _day_file(1, ["[23:50:00] channel=wxmp ok=False msg=errcode=40037"])
    rec = bridge._last_send_times()["wxmp"]
    assert rec["ok"] is False
    assert rec["day"] == "yesterday"
    assert rec["time"] == "23:50"


def test_last_send_times_today_wins_over_yesterday():
    """昨天先读、今天后读，同一条通道今天的记录覆盖昨天的。"""
    _day_file(1, ["[23:50:00] channel=wxmp ok=False msg=errcode=40037"])
    _day_file(0, ["[08:00:00] channel=wxmp ok=True msg=ok"])
    rec = bridge._last_send_times()["wxmp"]
    assert rec["day"] == "today"
    assert rec["ok"] is True, "今天的成功要盖掉昨天的失败"


def test_last_send_times_keeps_the_latest_line_within_a_day():
    """同一份文件里，后面的行覆盖前面的行（日志是追加写的）。"""
    _day_file(0, ["[08:00:00] channel=wxmp ok=False msg=errcode=40037",
                  "[09:00:00] channel=wxmp ok=True msg=ok"])
    assert bridge._last_send_times()["wxmp"]["ok"] is True


def test_last_label_three_forms():
    """**验收第 6 条：`last` 的三种文本。**"""
    assert bridge._last_label(None) is None
    assert bridge._last_label({"time": "22:39", "day": "today"}) == "22:39"
    assert bridge._last_label(
        {"time": "23:50", "day": "yesterday"}) == "昨天 23:50"
    # 老记录没有 day（W3.6 之前写进 state 的），不许崩
    assert bridge._last_label({"time": "22:39"}) == "22:39"


def test_chain_sentence_still_danger_after_midnight(isolated_state):
    """**R16 的复发点：昨天失败、今天没发过。**

    这是验收第 3 条。和第 4 条必须成对——只测这条，你不知道自己是不是
    把「昨天的成功」也一起判成了失败（那就是把「失忆」换成「记仇」）。
    """
    _ready_two_remote()
    _day_file(1, ["[23:50:00] channel=wxmp ok=False msg=errcode=40037"])
    got = bridge.Bridge().chain_sentence()["data"]
    assert got["level"] == "danger", got
    assert "最近一次没送到" in got["text"]


def test_chain_sentence_does_not_punish_yesterday_success(isolated_state):
    """验收第 4 条：昨天成功、今天没发过 → 仍是 ok。

    不许因为「不是今天」就降级——那是把「失忆」换成「记仇」。
    """
    _ready_two_remote()
    _day_file(1, ["[23:50:00] channel=wxmp ok=True msg=ok"])
    got = bridge.Bridge().chain_sentence()["data"]
    assert got["level"] == "ok", got
    assert "送到你手机" in got["text"]


def test_channels_list_shows_yesterday_in_last(isolated_state):
    """通道列表的时间要标出「昨天」，否则用户以为 23:50 是刚才。"""
    _ready_two_remote()
    _day_file(1, ["[23:50:00] channel=wxmp ok=False msg=errcode=40037"])
    by_type = {r["type"]: r for r in bridge.Bridge().channels_list()["data"]}
    assert by_type["wxmp"]["last"] == "昨天 23:50"
    assert by_type["wxmp"]["level"] == "danger"
    assert by_type["wxmp"]["state"].startswith("最近一次失败")



# --------------------------------------------------- W3.7 两个槽（D19）


def test_last_send_times_only_test_gives_ok_none():
    """**一个槽装不下两种事实。** 只点过测试时，`ok` 必须是 `None`。

    `None` 的含义是「真实投递一次都没发生过」——不是成功也不是失败。
    时间仍然是那条测试的（用户刚点完，列表显示刚刚是对的）。
    """
    _day_file(0, ["[08:00:00] test-send type=wxmp ok=False msg=errcode=40037"])
    rec = bridge._last_send_times()["wxmp"]
    assert rec["time"] == "08:00", "时间认测试"
    assert rec["test"] is True
    assert rec["ok"] is None, f"只点过测试时 ok 必须是 None，实际 {rec['ok']!r}"
    assert rec["ok_day"] is None
    assert rec["msg"] == "", "没有真实记录就没有真实报错原文"


def test_last_send_times_test_does_not_shadow_yesterday_failure():
    """今天点一次测试，不许把昨天的真实失败**遮蔽**掉。

    旧结构里今天那条覆盖昨天那条，判定时又被当测试排掉 → `none` → 侧栏回绿，
    R25 修好的「零点不失忆」被「早上点一次测试」打回原形。
    """
    _day_file(1, ["[23:50:00] channel=wxmp ok=False msg=errcode=40037"])
    _day_file(0, ["[08:00:00] test-send type=wxmp ok=True msg=ok"])
    rec = bridge._last_send_times()["wxmp"]
    assert rec["time"] == "08:00" and rec["day"] == "today" and rec["test"] is True
    assert rec["ok"] is False, "ok 来自昨天那条真实失败"
    assert rec["ok_day"] == "yesterday"
    assert "errcode" in rec["msg"]


def test_last_send_times_test_failure_does_not_override_yesterday_success():
    """反向：昨天真实成功 + 今天测试失败 → `ok` 仍是 True。

    只测「昨天失败 + 今天测试」那一面，你不知道自己是不是把测试彻底忽略了；
    两面一起看才说明两个槽真的分开。
    """
    _day_file(1, ["[23:50:00] channel=wxmp ok=True msg=ok"])
    _day_file(0, ["[08:00:00] test-send type=wxmp ok=False msg=TimeoutError"])
    rec = bridge._last_send_times()["wxmp"]
    assert rec["test"] is True and rec["time"] == "08:00"
    assert rec["ok"] is True, "昨天的成功仍是最近一次真实投递"
    assert rec["ok_day"] == "yesterday"


def test_last_send_times_today_real_wins_over_yesterday_real():
    """两槽各自独立推进：今天真实成功 → `ok=True` + `ok_day='today'`。"""
    _day_file(1, ["[23:50:00] channel=wxmp ok=False msg=errcode=40037"])
    _day_file(0, ["[09:00:00] channel=wxmp ok=True msg=ok"])
    rec = bridge._last_send_times()["wxmp"]
    assert rec["ok"] is True
    assert rec["ok_day"] == "today"
    assert rec["test"] is False


def test_last_send_times_only_test_does_not_break_channel_list(isolated_state):
    """只点过测试 → 列表绿「已启用」，和侧栏一致（不再一红一绿）。"""
    _ready_two_remote()
    _day_file(0, ["[08:00:00] test-send type=wxmp ok=False msg=errcode=40037"])
    by_type = {r["type"]: r for r in bridge.Bridge().channels_list()["data"]}
    assert by_type["wxmp"]["level"] == "ok", by_type["wxmp"]
    assert by_type["wxmp"]["state"] == "已启用"
    assert by_type["wxmp"]["last"] == "08:00", "时间仍显示测试那条"
    assert bridge.Bridge().chain_sentence()["data"]["level"] == "ok"

