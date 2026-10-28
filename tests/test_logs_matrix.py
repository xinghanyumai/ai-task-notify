"""30 格矩阵：穷举，不是列举。

三个维度：**今天的数据形态（5）× 昨天的数据形态（3）× log 通道（2）**。

为什么用矩阵而不是继续加场景：W3.1–W3.7 七轮，每轮都是「用户报一个症状 →
补一条断言」。加清单只能覆盖**想到的**组合，而七个历史 bug 全部藏在
「两个维度交叉」的地方（测试 × 跨天、log × recent、测试 × 跨天）。
矩阵的完整性有一个客观检验：**七个历史 bug 在旧代码上必须全红**
（`tools/verify_matrix_against_old.py`）。某格不红 = 那一格没覆盖到。

期望值全部**手算写死**在 `EXPECTED` 里，不是跑一遍记下来的。用「跑一遍
看结果」生成期望值，矩阵就变成「记录当前行为」，永远绿，也就永远抓不到回归。

## 配置假设（写清楚，否则矩阵没有意义）

每格只启用**一条远端通道**（`focus`）——即这一格里出现数据的那条通道，
没有数据时用 `bark` 占位。`log` 按维度开关。

这不是为了凑表，是因为「多通道同时启用」时 `chain` 会走 `partial`，
那是另一个维度（4 条远端通道的组合），混进来会让 30 格变成 30×N 格，
而且和本矩阵要守的东西（log 不影响数字、跨天不失忆、测试不遮蔽真实）
没有关系。多通道的 `partial` 由 `test_multi_channel_health_is_partial`
单独钉住。

`last` 列断言的是**通道列表上显示的文本**（`bridge._last_label`）：
今天只显示 `HH:MM`，昨天显示 `昨天 HH:MM`，没记录是 `None`。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from wbn import bridge, channels, config, insight, logs

# ---------------------------------------------------------------- 数据形态

# 一条「真实成功」的事件 + 投递（bark）
_EVENT_OK = ("[09:00:00] event=Stop kind=task_done sent=True remote_ok=True"
             " elapsed=1.17s title=✅ WorkBuddy 任务完成")
_EVENT_BAD = ("[09:00:00] event=Stop kind=task_failed sent=False remote_ok=False"
              " elapsed=1.17s title=⚠️ 任务失败")
_BARK_OK = "[09:00:00] channel=bark ok=True msg=ok"
_WXMP_BAD = "[09:00:00] channel=wxmp ok=False msg=errcode=40037 模板 ID 无效"

# 昨天的两条（23:50），用来测跨天
_EVENT_BAD_Y = _EVENT_BAD.replace("[09:00:00]", "[23:50:00]")
_WXMP_BAD_Y = _WXMP_BAD.replace("[09:00:00]", "[23:50:00]")
_EVENT_OK_Y = _EVENT_OK.replace("[09:00:00]", "[23:50:00]")
_BARK_OK_Y = _BARK_OK.replace("[09:00:00]", "[23:50:00]")

# T1：只有手动测试，**没有真实投递**。
#
# 两行是故意的：第一行是 W3.1 起的写法（`test-send type=`），第二行是历史
# 写法（`test channel=`，真机日志里还留着）。两种都算测试，`logs` 两种都认。
# 少了第二行，W3.1 那格在旧代码上不会红——旧解析器只认 `channel=` 子串，
# 看不见 `test-send type=`，于是「测试进统计」这个 bug 复现不出来。
_TEST_LINES = [
    "[08:00:00] test-send type=wxmp ok=False msg=errcode=40037 模板 ID 无效",
    "[08:00:00] test channel=wxmp ok=False msg=errcode=40037 模板 ID 无效",
]


def _log_line(t: str) -> str:
    """`log` 通道在一次真实投递后写下的行（本机落盘）。"""
    return f"{t} channel=log ok=True msg=已记录"


def _today(kind: str, log_on: bool) -> list[str]:
    """今天的数据形态。真实日志的顺序是「投递行 → 事件汇总行」。"""
    if kind == "T0":                                    # 空
        return []
    if kind == "T1":                                    # 只有一次测试失败
        return list(_TEST_LINES)
    if kind == "T2":                                    # 一次真实成功（bark）
        return [_BARK_OK] + ([_log_line("[09:00:00]")] if log_on else []) + [_EVENT_OK]
    if kind == "T3":                                    # 一次真实失败（wxmp）
        return [_WXMP_BAD] + ([_log_line("[09:00:00]")] if log_on else []) + [_EVENT_BAD]
    if kind == "T4":                                    # 测试失败 + 真实失败
        return ([_TEST_LINES[0]]
                + [_WXMP_BAD.replace("[09:00:00]", "[08:00:00]")]
                + ([_log_line("[08:00:00]")] if log_on else [])
                + [_EVENT_BAD.replace("[09:00:00]", "[08:00:00]")])
    raise AssertionError(kind)


def _yday(kind: str, log_on: bool) -> list[str]:
    """昨天的数据形态（23:50 写的，跨零点后还在文件里）。"""
    if kind == "Y0":                                    # 空
        return []
    if kind == "Y1":                                    # 一次真实失败（wxmp）
        return [_WXMP_BAD_Y] + ([_log_line("[23:50:00]")] if log_on else []) + [_EVENT_BAD_Y]
    if kind == "Y2":                                    # 一次真实成功（bark）
        return [_BARK_OK_Y] + ([_log_line("[23:50:00]")] if log_on else []) + [_EVENT_OK_Y]
    raise AssertionError(kind)


# ---------------------------------------------------------------- 期望值（手算）

# 每格一列一项，**手算写死**。
#
# 算的时候只用四条口径：
#   today        = 今天的 remote_ok=True 事件数
#   ok / fail    = 今天的（排除 log、排除测试）投递数，按 ok 分
#   recent       = 两天的 remote_ok=True 事件数（上限 8，本矩阵最多 2）
#   chain/state  = focus 通道最近一条**非测试**记录的成败；没有就是「没记录」
#   last         = focus 通道最近一条记录（**含**测试）的时间
#
# `recent` 是 W3.5 那一格的守卫：旧实现读 `notifications.log`，log 通道一关
# 这个文件就不存在，「最近通知」永久为空，而空态还写着「还没收到通知」。
EXPECTED: dict[tuple[str, str], dict] = {
    # 今天空、昨天空：什么都没有，不能吓人
    ("T0", "Y0"): dict(today=0, ok=0, fail=0, recent=0,
                       chain="ok", state="ok", last=None),
    # 昨天失败：零点后不能失忆（R24 / W3.6）
    ("T0", "Y1"): dict(today=0, ok=0, fail=0, recent=0,
                       chain="danger", state="danger", last="昨天 23:50"),
    ("T0", "Y2"): dict(today=0, ok=0, fail=0, recent=1,
                       chain="ok", state="ok", last="昨天 23:50"),

    # 今天只点过测试：成败未知（ok=None），不能算失败也不能算成功
    ("T1", "Y0"): dict(today=0, ok=0, fail=0, recent=0,
                       chain="ok", state="ok", last="08:00"),
    # 昨天真失败 + 今天一次测试失败：测试不许遮蔽真实失败（R28 / W3.7）
    ("T1", "Y1"): dict(today=0, ok=0, fail=0, recent=0,
                       chain="danger", state="danger", last="08:00"),
    ("T1", "Y2"): dict(today=0, ok=0, fail=0, recent=1,
                       chain="ok", state="ok", last="08:00"),

    # 一次真实成功：三个数各加一
    ("T2", "Y0"): dict(today=1, ok=1, fail=0, recent=1,
                       chain="ok", state="ok", last="09:00"),
    # 今天成功盖住昨天的失败——状态问「上次怎么样」
    ("T2", "Y1"): dict(today=1, ok=1, fail=0, recent=1,
                       chain="ok", state="ok", last="09:00"),
    ("T2", "Y2"): dict(today=1, ok=1, fail=0, recent=2,
                       chain="ok", state="ok", last="09:00"),

    # 一次真实失败：today 不涨（remote_ok=False），fail 涨
    ("T3", "Y0"): dict(today=0, ok=0, fail=1, recent=0,
                       chain="danger", state="danger", last="09:00"),
    ("T3", "Y1"): dict(today=0, ok=0, fail=1, recent=0,
                       chain="danger", state="danger", last="09:00"),
    ("T3", "Y2"): dict(today=0, ok=0, fail=1, recent=1,
                       chain="danger", state="danger", last="09:00"),

    # 测试失败 + 真实失败：只有真实那条算数
    ("T4", "Y0"): dict(today=0, ok=0, fail=1, recent=0,
                       chain="danger", state="danger", last="08:00"),
    ("T4", "Y1"): dict(today=0, ok=0, fail=1, recent=0,
                       chain="danger", state="danger", last="08:00"),
    ("T4", "Y2"): dict(today=0, ok=0, fail=1, recent=1,
                       chain="danger", state="danger", last="08:00"),
}

# 这一格里「有数据的那条远端通道」——只启用它。
# 今天有数据就用今天的通道，今天没有就用昨天的，都没有用 bark 占位。
FOCUS: dict[tuple[str, str], str] = {}
for _t, _c in (("T1", "wxmp"), ("T2", "bark"), ("T3", "wxmp"), ("T4", "wxmp")):
    for _y in ("Y0", "Y1", "Y2"):
        FOCUS[(_t, _y)] = _c
FOCUS[("T0", "Y0")] = "bark"
FOCUS[("T0", "Y1")] = "wxmp"
FOCUS[("T0", "Y2")] = "bark"

CASES = [(t, y, log_on)
         for t in ("T0", "T1", "T2", "T3", "T4")
         for y in ("Y0", "Y1", "Y2")
         for log_on in (False, True)]


# ---------------------------------------------------------------- 装置


def _cfg(focus: str, log_on: bool) -> dict:
    """只启用 `focus`（+ 按维度开关 log），其余通道全部关掉。"""
    cfg = config.load()
    cfg["enabled"] = True
    cfg["triggers"] = {k: False for k in cfg["triggers"]}
    cfg["triggers"]["task_done"] = True
    cfg["triggers"]["task_failed"] = True

    for ch in cfg["channels"]:
        ch["enabled"] = False
        # 必填字段填满，否则 `channel_state` 会返回「待配置」而不是「已启用」
        if ch["type"] == "wxmp":
            ch.update(appid="wxtest", secret="s", template_id="tpl")
        elif ch["type"] == "bark":
            ch.update(key="barkkey")

    on = {focus, "log"} if log_on else {focus}
    for ch in cfg["channels"]:
        if ch["type"] in on:
            ch["enabled"] = True
    return cfg


def _write(tmp_path, days_ago: int, lines: list[str]) -> None:
    d = datetime.now() - timedelta(days=days_ago)
    p = tmp_path / "logs" / f"notify-{d:%Y-%m-%d}.log"
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _write_notifications(tmp_path, days_ago: int, kind: str) -> None:
    """`log` 通道写的正文存档。旧版 `stats()` 的 `today` / `recent` 读它。"""
    if kind not in ("T2", "T3", "T4", "Y1", "Y2"):
        return
    d = datetime.now() - timedelta(days=days_ago)
    t = "23:50:00" if days_ago else "09:00:00"
    ev = "task_done" if kind in ("T2", "Y2") else "task_failed"
    title = "✅ WorkBuddy 任务完成" if ev == "task_done" else "⚠️ 任务失败"
    p = tmp_path / "logs" / "notifications.log"
    with p.open("a", encoding="utf-8") as f:
        f.write(f"{d:%Y-%m-%d} {t} [WorkBuddy] {ev} | {title}\n")


def _setup(tmp_path, today_kind: str, yday_kind: str, log_on: bool) -> dict:
    _write(tmp_path, 1, _yday(yday_kind, log_on))
    _write(tmp_path, 0, _today(today_kind, log_on))
    if log_on:
        _write_notifications(tmp_path, 1, yday_kind)
        _write_notifications(tmp_path, 0, today_kind)
    return _cfg(FOCUS[(today_kind, yday_kind)], log_on)


def _check_invariants(facts: logs.Facts, s: dict) -> None:
    """六条不变量：**不看具体数字**，只检查四个出口之间自洽。

    这六条是矩阵的骨架——期望值表可能写错，不变量不会。期望值表守
    「数字该是多少」，不变量守「数字和明细对得上」。
    """
    # ① today = 今天 remote_ok=True 的事件数
    assert s["today"] == sum(1 for e in facts.events_of("today") if e.remote_ok)

    # ② ok + fail = 今天的（排除 log、排除测试）投递数
    assert s["ok"] + s["fail"] == len(facts.deliveries_of(
        "today", exclude=channels.LOCAL_CHANNELS, include_tests=False))

    # ③ recent 的每一行都能在事件里找到原件（不许凭空造行）
    for row in s["recent"]:
        assert any(e.time == row["time"] and e.day == row["day"]
                   for e in facts.events)

    # ④ ok 非 None ⇒ 一定有一条**非测试**记录撑着它（D19 的槽不许凭空有值）
    for ctype, cf in facts.last_by_channel().items():
        if cf.ok is not None:
            assert any(d.channel == ctype and not d.test
                       for d in facts.deliveries)

    # ⑤ 同一份文件读两次结果相同（解析无状态、无缓存漂移）
    assert logs.read(days=2) == facts

    # ⑥ time 一定来自某条记录（含测试）——和 ④ 成对：
    #    ④ 管「成败必有真实记录」，⑥ 管「时间必有记录」，两条合起来
    #    把 D19 的两个槽都钉住，谁把槽写反都会红。
    for ctype, cf in facts.last_by_channel().items():
        assert any(d.channel == ctype and d.time == cf.time
                   for d in facts.deliveries)


# ---------------------------------------------------------------- 30 格


@pytest.mark.parametrize("today_kind,yday_kind,log_on", CASES)
def test_matrix(tmp_path, today_kind, yday_kind, log_on):
    """一格 = 一组数据 + 四个出口的期望值 + 六条不变量。"""
    cfg = _setup(tmp_path, today_kind, yday_kind, log_on)

    facts = logs.read(days=2)
    s = insight.stats(cfg)
    last = bridge._last_send_times()
    chain = insight.chain_sentence(cfg, last)

    focus = FOCUS[(today_kind, yday_kind)]
    ch = next(c for c in cfg["channels"] if c["type"] == focus)
    state = insight.channel_state(focus, ch, last.get(focus))

    # ① 六条不变量（与期望值无关的自洽检查）
    _check_invariants(facts, s)

    # ② 期望值（手算的常量，不是算出来的）
    want = EXPECTED[(today_kind, yday_kind)]
    tag = f"{today_kind}×{yday_kind}×log={'开' if log_on else '关'}"

    assert (s["today"], s["ok"], s["fail"]) == \
        (want["today"], want["ok"], want["fail"]), tag
    assert len(s["recent"]) == want["recent"], f"{tag} recent={s['recent']}"
    assert chain[1] == want["chain"], f"{tag} chain={chain}"
    assert state[1] == want["state"], f"{tag} state={state}"
    assert bridge._last_label(last.get(focus)) == want["last"], tag

    # ③ log 开着的时候，log 通道自己必须出现在列表里——它只是不参与统计
    if log_on and (today_kind in ("T2", "T3", "T4") or yday_kind in ("Y1", "Y2")):
        assert "log" in last, f"{tag} 开了 log 通道却没有它的记录"
    if not log_on:
        assert "log" not in last, f"{tag} 关了 log 通道却读到了记录"


@pytest.mark.parametrize("today_kind,yday_kind", [
    (t, y) for t in ("T0", "T1", "T2", "T3", "T4") for y in ("Y0", "Y1", "Y2")
])
def test_log_switch_changes_nothing_but_its_own_row(tmp_path, today_kind, yday_kind):
    """第七个不变量：**log 开关不影响任何数字**。

    单独写一条而不是塞进 30 格：它的表达力在「同一样本跑两遍再相减」，
    放进格子里就变成两组互不相干的断言，看不出「相等」这件事。

    W3.2（`today` 数 `notifications.log`）和 W3.5（`recent` 读同一个文件）
    修的都是这条。30 格里 15 格在守它。
    """
    off = insight.stats(_setup(tmp_path, today_kind, yday_kind, False))
    on = insight.stats(_setup(tmp_path, today_kind, yday_kind, True))

    assert (off["today"], off["ok"], off["fail"]) == \
           (on["today"], on["ok"], on["fail"]), "log 通道影响了三个数"
    assert off["recent"] == on["recent"], "log 通道影响了最近通知"
    # `channels` 是「启用通道 N」，log 开了当然要变——它是配置，不是统计
    assert on["channels"] == off["channels"] + 1


@pytest.mark.parametrize("today_kind,yday_kind", [
    (t, y) for t in ("T0", "T1", "T2", "T3", "T4") for y in ("Y0", "Y1", "Y2")
])
def test_remote_channel_state_ignores_log_switch(tmp_path, today_kind, yday_kind):
    """远端通道的 state / last 不许被 log 开关影响。"""
    focus = FOCUS[(today_kind, yday_kind)]

    def _state(log_on):
        cfg = _setup(tmp_path, today_kind, yday_kind, log_on)
        last = bridge._last_send_times()
        ch = next(c for c in cfg["channels"] if c["type"] == focus)
        return insight.channel_state(focus, ch, last.get(focus))

    assert _state(False) == _state(True)


def test_multi_channel_health_is_partial(tmp_path):
    """多通道同时启用时，一条成功一条失败是 `partial`（warn），不是 `ok`。

    30 格每格只启用一条远端通道（见模块 docstring），所以覆盖不到这个组合。
    这条单独补上：`chain` 不许因为「有一条成功了」就说「送到你手机」。
    """
    _write(tmp_path, 0, _today("T2", False))
    _write(tmp_path, 1, _yday("Y1", False))
    cfg = _cfg("bark", False)
    for ch in cfg["channels"]:
        if ch["type"] == "wxmp":
            ch["enabled"] = True

    chain = insight.chain_sentence(cfg, bridge._last_send_times())
    assert chain[1] == "warn"
    assert "部分没送到" in chain[0]


# ---------------------------------------------------------------- 变异测试

# 脏数据。**不是**「解析器要宽容」，是「解析器不许被骗」：
# `msg=` 是远端返回的原文，里面出现 `ok=False` / `channel=log` 都很正常。
_LONG_TITLE = "长标题" * 20          # 60 字，用来测 52 字截断

_DIRTY = [
    # ① 成功的行，但 msg 里写着 ok=False —— 不许被数成失败
    "[10:00:00] channel=bark ok=True msg=upstream said ok=False but we won",
    # ② 失败的行，但 msg 里写着 ok=True —— 不许被数成成功
    "[10:00:01] channel=bark ok=False msg=server replied ok=True in body",
    # ③ 真实失败，但 msg 里提到 channel=log —— 不许被当成 log 落盘排掉
    "[10:00:02] channel=bark ok=False msg=upstream said channel=log failed",
    # ④ 汇总行的 remote_ok=False —— 不许被数成一次失败（否则失败数翻倍）
    "[10:00:03] event=Stop kind=task_done sent=True remote_ok=False",
    # ⑤ 缺通道名
    "[10:00:04] channel=",
    # ⑥ 完全不是日志
    "[10:00:05] 完全不是日志的一行",
    # ⑦ 超长 msg
    "[10:00:06] channel=bark ok=True msg=" + "x" * 200,
    # ⑧ 老行：没有 title=
    "[10:00:07] event=Stop kind=task_done sent=True remote_ok=False elapsed=1s",
    # ⑨ 双时间戳
    "[10:00:08] [10:00:08] channel=bark ok=True",
    # ⑩ 测试行，没有 msg
    "[10:00:09] test-send type=bark ok=True",
    # ⑪ 超长标题 + 真的送到手机（测 52 字截断；⑧ 的 remote_ok=False 进不了 recent）
    "[10:00:10] event=Stop kind=task_done sent=True remote_ok=True"
    " elapsed=1s title=✅ " + _LONG_TITLE,
]


def test_dirty_lines_do_not_crash_or_miscount(tmp_path):
    """塞 11 行脏数据：不抛异常、不误算、脏行静默跳过。

    期望值是**手算**的：

    - 成功：①（ok=True，msg 里的 ok=False 是诱饵）、⑦（超长 msg）
    - 失败：②（ok=False，msg 里的 ok=True 是诱饵）、③（msg 提到 channel=log，
      但通道名是 bark，**必须**算一次失败——旧代码会把它当 log 排掉）
    - ④ 是事件汇总行，不是投递行；⑧ 同理
    - ⑤ 缺通道名、⑥ 不是日志、⑨ 双时间戳：解析不出投递行，跳过
    - ⑩ 是测试，不算投递
    """
    _write(tmp_path, 0, _DIRTY)
    facts = logs.read(days=2)
    s = insight.stats(_cfg("bark", False))

    # 不崩、不误算：4 条真实投递（①②③⑦）+ 1 条测试（⑩）
    real = facts.deliveries_of("today", exclude=channels.LOCAL_CHANNELS,
                               include_tests=False)
    assert len(real) == 4, [(d.time, d.channel, d.ok, d.test) for d in facts.deliveries]
    assert (s["ok"], s["fail"]) == (2, 2)

    # 诱饵：msg 里的 ok=False / ok=True / channel=log 都不参与判定。
    # 按文件顺序取，不按时间——这 11 行全在 10:00 这一分钟里，`time` 只到分钟。
    assert [d.ok for d in real] == [True, False, False, True], \
        "①②③⑦ 的成败被 msg 里的诱饵读反了"
    assert {d.channel for d in real} == {"bark"}, \
        "③ 的 msg 里有 channel=log，被当成 log 落盘排掉了"

    # 超长 msg 先脱敏再截断
    assert len(real[3].msg) <= 121

    # ⑨ 双时间戳 / ⑤ 缺通道名 / ⑥ 非日志行：一条都不许解析出来
    assert len(facts.deliveries) == 5, "脏行被解析成了投递"
    assert [d for d in facts.deliveries if d.test] == [
        logs.Delivery(time="10:00", day="today", channel="bark",
                      ok=True, msg="", test=True)], "⑩ 无 msg 的测试行没读对"

    # 事件行：⑧（remote_ok=False）+ ⑪（remote_ok=True）
    assert len(facts.events_of("today")) == 2
    assert s["today"] == 1

    # 超长标题截到 52 字，emoji 剥掉
    assert len(s["recent"]) == 1
    assert len(s["recent"][0]["summary"]) == 52
    assert not s["recent"][0]["summary"].startswith("✅")
