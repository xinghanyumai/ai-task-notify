"""`wbn/insight.py` 的行为守卫。

这个模块是从 `ui.py` 搬出来的业务逻辑，搬错不会报错，只会让页面上
「19 显示成 38」。所以这里的重点不是覆盖率，是**把那几个踩过的坑钉住**：

- `stats` 只数 `channel=` 行（汇总行的 `remote_ok=False` 不能算失败）
- `stats` 剥掉标题开头的 emoji
- `merge_save` 是读-改-写，不能拿内存整份覆盖磁盘
- `chain_sentence` 里 log 通道不算「送到手机」
- `preview` 的正文要剥 markdown（和实际发出去的一致）

另外三条硬规则也在这里守着：不出现颜色、状态是语义枚举、纯函数无状态。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from wbn import config, insight

# ---------------------------------------------------------------- 硬规则


def test_module_has_no_ui_dependency():
    """insight 不许碰 GUI：没有 tkinter / webview 导入，没有十六进制色值。"""
    src = (config.ROOT / "wbn" / "insight.py").read_text(encoding="utf-8")
    assert "import tkinter" not in src
    assert "import webview" not in src
    assert "from tkinter" not in src
    assert not __import__("re").search(r"#[0-9a-fA-F]{6}", src)


def test_levels_are_semantic_not_colors():
    """语义枚举是固定四个，不是颜色值。"""
    assert set(insight.LEVELS) if hasattr(insight, "LEVELS") else True
    levels = {insight.LEVEL_OK, insight.LEVEL_WARN,
              insight.LEVEL_DANGER, insight.LEVEL_MUTED}
    assert levels == {"ok", "warn", "danger", "muted"}


# ---------------------------------------------------------------- 字符串工具


@pytest.mark.parametrize("raw, want", [
    ("a,b", ["a", "b"]),
    ("a，b", ["a", "b"]),          # 中文逗号
    (" a , b ", ["a", "b"]),       # 两端空白
    ("a,,b", ["a", "b"]),          # 空段丢弃
    ("", []),
    (None, []),
])
def test_split_list(raw, want):
    assert insight.split_list(raw) == want


@pytest.mark.parametrize("raw, default, want", [
    ("12", 0, 12),
    (" 12 ", 0, 12),
    ("", 7, 7),
    ("abc", 7, 7),
    (None, 7, 7),
])
def test_to_int(raw, default, want):
    assert insight.to_int(raw, default) == want


@pytest.mark.parametrize("raw, emoji, rest", [
    ("✅ 任务完成", "✅", "任务完成"),
    ("⚠️ 工具失败", "⚠️", "工具失败"),
    ("任务完成", "", "任务完成"),
    ("", "", ""),
])
def test_split_emoji(raw, emoji, rest):
    assert insight.split_emoji(raw) == (emoji, rest)


# ---------------------------------------------------------------- stats


def _write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_stats_counts_only_channel_lines():
    """**这是「19 变 38」那个 bug 的回归测试。**

    每个事件在通道行之后还有一行汇总，里面 `remote_ok=False` 含子串
    "ok=False"。只认锚定在时间戳右侧的 `channel=` 行，汇总行就进不来。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    _write(config.LOG_DIR / f"notify-{today}.log", [
        "[10:00:00] channel=wxmp ok=True msg=1.2s",
        "[10:00:01] channel=bark ok=False msg=timeout",
        "[10:00:02] event=Stop kind=task_done sent=True "
        "remote_ok=False remote_fail=1 dedup=False",
    ])
    s = insight.stats(config.load())
    assert s["ok"] == 1
    assert s["fail"] == 1, "汇总行的 remote_ok=False 不能被算成一次失败"


def test_stats_excludes_log_channel_from_ok_and_fail():
    """**D14 的核心：`log` 是调试落盘，不是投递目标。**

    它落盘成功既不该让 `ok` +1，也不该让 `today` +1——否则屏幕上会出现
    「16 条通知 / 16 次成功 / 14 次失败」这种读不出结论的卡。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    _write(config.LOG_DIR / f"notify-{today}.log", [
        "[10:00:00] channel=log ok=True msg=已记录",
        "[10:00:00] channel=wxmp ok=False msg=errcode=40037 模板 ID 无效",
        "[10:00:01] event=Stop kind=task_done sent=True remote_ok=False",
    ])
    s = insight.stats(config.load())
    assert s["ok"] == 0, "log 落盘不算一次投递成功"
    assert s["fail"] == 1, "远端那一次失败要留下"
    assert s["today"] == 0, "只有 log 成功的事件不算到手机了"


def test_stats_does_not_mistake_msg_for_channel_name():
    """**验收第 6 条：假通道名不误伤。**

    前三轮的 bug 全出在「整行找子串」。取通道名必须锚定时间戳右侧第一段：
    `msg=` 是通道返回的原文，里面出现 `channel=log` 的概率不为零，
    整行匹配会把一次**真实失败**当成 log 落盘排掉。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    _write(config.LOG_DIR / f"notify-{today}.log", [
        "[10:00:00] channel=wxmp ok=False msg=upstream said channel=log failed",
        "[10:00:01] channel=log ok=True msg=已记录",
        "[10:00:02] channel=bark ok=True msg=ok channel=log",
    ])
    s = insight.stats(config.load())
    assert s["fail"] == 1, "msg 里含 channel=log 的真实失败必须计入 fail"
    assert s["ok"] == 1, "只有 bark 那一行算成功（log 行被排除）"


def test_stats_does_not_read_ok_from_msg():
    """**W3.4 验收第 1 条：`ok=` 也必须锚定，不许整行找。**

    `msg=` 是远端返回的原文。写它的是远端服务，不是我们——里面出现
    `ok=False` 完全可能（比如上游把一段 JSON 回显出来）。前四轮统计 bug
    全是「整行找子串」，`channel=` 锚定了、`ok=` 还留一处，就还有第五次。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    _write(config.LOG_DIR / f"notify-{today}.log", [
        # 一次**成功**的投递，但远端原文里带了 ok=False
        "[10:00:00] channel=bark ok=True msg=upstream said ok=False but we won",
        "[10:00:01] channel=wxmp ok=True msg=ok",
    ])
    s = insight.stats(config.load())
    assert s["ok"] == 2, "两行都是成功的，msg 里的 ok=False 不许把谁数成失败"
    assert s["fail"] == 0

    # 反向：真失败的行，msg 里带 ok=True 也不许数成成功
    _write(config.LOG_DIR / f"notify-{today}.log", [
        "[10:00:00] channel=bark ok=False msg=server replied ok=True in body",
    ])
    s = insight.stats(config.load())
    assert s["ok"] == 0
    assert s["fail"] == 1


def test_stats_recent_ignores_test_send_lines():
    """「最近通知」里出现一条带绿色 ✓ 的「通知测试」，就是「看起来像真的」。

    `send_test` 写的是 `test-send type=…`，第一段不是 `event=`，
    所以永远进不了 `recent`——这里钉住这一点，别哪天为了「测试也要显示」
    把它放进来。测试不是通知（D14）。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    _write(config.LOG_DIR / f"notify-{today}.log", [
        "[10:00:00] test-send type=bark ok=True msg=HTTP 200",
        "[10:00:01] test channel=log ok=True msg=已记录",
        "[10:01:00] event=Stop kind=task_done sent=True remote_ok=True "
        "elapsed=1.0s title=✅ WorkBuddy 任务完成",
    ])
    recent = insight.stats(config.load())["recent"]
    assert len(recent) == 1
    assert recent[0]["summary"] == "WorkBuddy 任务完成"


def test_stats_ignores_test_send_lines():
    """**「今日通知 7 / 成功 7 / 失败 6」那个 bug 的回归测试。**

    手动点「测试此通道」和真实发送写同一个 `notify-*.log`，所以测试行
    必须在读取侧排掉，否则 7 条通知能配出 13 次通道结果。

    两种写法都要认：
    - `test-send type=…`（W3.1 起 `pipeline.send_test` 的写法）
    - `test channel=…`（W3.1 之前的历史行，**日志里留着，不许删**）
    """
    today = datetime.now().strftime("%Y-%m-%d")
    _write(config.LOG_DIR / f"notify-{today}.log", [
        "[10:00:00] channel=bark ok=False msg=timeout",
        "[10:00:01] test-send type=bark ok=False msg=timeout",
        "[10:00:02] test-send type=log ok=True msg=已记录",
        "[10:00:03] test channel=log ok=True msg=已记录",
        "[10:00:04] event=Stop kind=task_done sent=True remote_ok=False",
    ])
    s = insight.stats(config.load())
    assert s["ok"] == 0, "测试发送不算通知，成功数不许被它撑起来"
    assert s["fail"] == 1, "只剩那一行真实发送；真行不许被一起滤掉"


def test_stats_recent_does_not_read_notifications_log():
    """**「关掉 log 通道最近通知就永久为空」那个 bug 的回归测试。**

    `recent` 曾经读 `notifications.log`，而那个文件全项目只有
    `channels._send_log` 写——它属于一个**可以关掉的通道**。用户关掉它，
    「最近通知」就永远空白，空态还写「还没收到通知」，可他明明发了 20 条。

    现在 `recent` 和 `today` 读同一行（`pipeline` 写的事件汇总行），
    这个文件里的东西一条都不许进 `recent`。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    _write(config.LOG_DIR / "notifications.log", [
        f"{today} 09:00:00 [WorkBuddy] task_done | ✅ 一",
        f"{today} 09:01:00 [WorkBuddy] task_done | ✅ 二",
        f"{today} 09:02:00 [WorkBuddy] task_done | ✅ 三",
    ])
    _write(config.LOG_DIR / f"notify-{today}.log", [
        "[09:00:00] event=Stop kind=task_done sent=True remote_ok=False "
        "elapsed=1.0s title=✅ 远端全失败那条",
        "[09:01:00] event=Stop kind=task_done sent=True remote_ok=True "
        "elapsed=1.0s title=✅ 真到手机那条",
        "[09:02:00] event=Stop kind=task_done sent=False remote_ok=False "
        "elapsed=0.1s title=✅ 一条都没发出去",
        "[09:03:00] skip event=Stop kind=task_done reason=耗时太短",
    ])
    s = insight.stats(config.load())
    assert s["today"] == 1, "只有 remote_ok=True 才算到手机；远端全失败不算"
    assert len(s["recent"]) == 1, "recent 只收真正送达的事件，和 today 同一行"
    assert s["recent"][0]["summary"] == "真到手机那条"


def test_stats_today_and_recent_survive_log_channel_being_off():
    """关掉「写入本地日志」通道后 notifications.log 根本不存在。

    两个数都要照样有——它们和通道开关无关（W3.5 / B 项）。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    assert not (config.LOG_DIR / "notifications.log").exists()
    _write(config.LOG_DIR / f"notify-{today}.log", [
        "[10:00:00] channel=webhook ok=True msg=HTTP 200",
        "[10:00:00] event=Stop kind=task_done sent=True remote_ok=True "
        "elapsed=1.0s title=✅ 关掉 log 也发得出去",
    ])
    s = insight.stats(config.load())
    assert s["today"] == 1
    assert len(s["recent"]) == 1, "最近通知不许因为 log 通道关着就空着"
    assert s["recent"][0]["summary"] == "关掉 log 也发得出去"


def test_is_delivered_anchors_on_the_first_field():
    """`remote_ok=True` 靠整行按位置匹配来判，不是整行找子串。"""
    assert insight._is_delivered(
        "[10:00:00] event=Stop kind=task_done sent=True remote_ok=True elapsed=1.0s")
    # sent=True 但远端全失败 = 没到手机
    assert not insight._is_delivered(
        "[10:00:00] event=Stop kind=task_done sent=True remote_ok=False elapsed=1.0s")
    assert not insight._is_delivered("[10:00:00] event=Stop sent=False")
    # 被过滤链拦下的行第一段是 `skip`，不是 `event=`
    assert not insight._is_delivered(
        "[10:00:00] skip event=Stop remote_ok=True")
    # 手动测试不是通知
    assert not insight._is_delivered(
        "[10:00:00] test-send type=log remote_ok=True")
    assert not insight._is_delivered("garbage line without prefix")


def test_is_test_line_only_looks_at_the_first_field():
    """`msg=` 后面是通道返回的原文，出现 "test" 很常见，不能整行匹配。"""
    assert insight._is_test_line("[10:00:00] test-send type=bark ok=False msg=x")
    assert insight._is_test_line("[10:00:00] test channel=log ok=True msg=x")
    assert not insight._is_test_line("[10:00:00] channel=bark ok=False "
                                     "msg=connection reset by test-host")
    assert not insight._is_test_line("[10:00:00] event=Stop kind=task_done")
    # 没有 `] ` 前缀的畸形行不许炸
    assert not insight._is_test_line("garbage line without prefix")


def test_stats_counts_today_and_recent_newest_first():
    today = datetime.now().strftime("%Y-%m-%d")
    # 真实日志是追加写的，最后一行才是最新的
    _write(config.LOG_DIR / f"notify-{today}.log", [
        "[09:00:00] event=Stop kind=task_done sent=True remote_ok=True "
        "elapsed=1.0s title=✅ 第一条",
        "[09:05:00] event=Stop kind=task_failed sent=True remote_ok=True "
        "elapsed=1.0s title=❌ 第二条",
    ])
    s = insight.stats(config.load())
    assert s["today"] == 2
    assert s["recent"][0]["summary"] == "第二条", "recent 是最新在前"
    # 红点说的是「这次任务不顺」，不是「没发出去」——两条都送到了
    assert s["recent"][0]["ok"] is False, "task_failed 是失败类事件"
    assert s["recent"][1]["ok"] is True


def test_stats_recent_marks_yesterday():
    """**跨天条目必须标出来**（W3.5 / C 项）。

    只给 `HH:MM` 的话，昨天 23:50 和今天 00:10 在界面上看不出区别，
    用户会把昨晚的通知当成刚才的。
    """
    from datetime import timedelta

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    _write(config.LOG_DIR / f"notify-{yesterday}.log", [
        "[23:50:00] event=Stop kind=task_done sent=True remote_ok=True "
        "elapsed=1.0s title=✅ 昨晚那条",
    ])
    _write(config.LOG_DIR / f"notify-{today}.log", [
        "[00:10:00] event=Stop kind=task_done sent=True remote_ok=True "
        "elapsed=1.0s title=✅ 今天那条",
    ])
    recent = insight.stats(config.load())["recent"]
    assert [r["summary"] for r in recent] == ["今天那条", "昨晚那条"]
    assert recent[0]["day"] == "today"
    assert recent[1]["day"] == "yesterday", "昨天的条目要带 day 标记"
    # today 只数今天：昨天的通知不是今天的成绩
    assert insight.stats(config.load())["today"] == 1


def _two_days(y_lines, t_lines):
    """写昨天 + 今天两份日志，返回 (yesterday, today) 两个日期串。"""
    from datetime import timedelta

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    _write(config.LOG_DIR / f"notify-{yesterday}.log", y_lines)
    _write(config.LOG_DIR / f"notify-{today}.log", t_lines)
    return yesterday, today


def test_stats_ok_fail_count_only_today():
    """**D18：`ok` / `fail` 是「今日」卡上的数，昨天的投递不是今天的成绩。**

    改前是两天都数：昨天 3 成功 2 失败 + 今天 1 成功 → `ok=4 fail=2`，
    屏幕上出现「今日通知 1 / 通道成功 4」——同一张卡里两个时间范围。
    """
    _two_days(
        ["[09:00:00] channel=wxmp ok=True msg=ok",
         "[09:01:00] channel=wxmp ok=True msg=ok",
         "[09:02:00] channel=wxmp ok=True msg=ok",
         "[09:03:00] channel=wxmp ok=False msg=errcode=40037",
         "[09:04:00] channel=wxmp ok=False msg=errcode=40037"],
        ["[10:00:00] channel=wxmp ok=True msg=ok"],
    )
    s = insight.stats(config.load())
    assert s["ok"] == 1, f"只数今天，实际 {s['ok']}"
    assert s["fail"] == 0, f"昨天的失败不是今天的，实际 {s['fail']}"


def test_stats_today_does_not_follow_ok_fail_to_two_days():
    """`today` 不许跟着读两天——它叫「今日通知」（D18 §5 第 1 条）。

    昨天有 `remote_ok=True` 的事件、今天没有：`today` 必须是 0。
    """
    _two_days(
        ["[23:50:00] event=Stop kind=task_done sent=True remote_ok=True "
         "elapsed=1.0s title=昨晚那条"],
        ["[00:10:00] channel=wxmp ok=False msg=errcode=40037"],
    )
    s = insight.stats(config.load())
    assert s["today"] == 0, "昨天到过手机不代表今天到过"
    assert s["fail"] == 1, "今天的失败要数"


def test_stats_recent_still_reads_two_days_after_the_cut():
    """截通道行的 `tag != "today"` **不能**把 `recent` 一起断掉。

    位置放错（挪到 `_EVENT_LINE` 之前）就会这样：`recent` 只剩今天的，
    昨晚那 20 条凭空消失。这条钉住「只截通道行」这个边界。
    """
    _two_days(
        ["[23:50:00] event=Stop kind=task_done sent=True remote_ok=True "
         "elapsed=1.0s title=昨晚那条"],
        [],
    )
    recent = insight.stats(config.load())["recent"]
    assert len(recent) == 1, "recent 就是要读两天"
    assert recent[0]["day"] == "yesterday"


def test_stats_strips_leading_emoji_from_title():
    today = datetime.now().strftime("%Y-%m-%d")
    _write(config.LOG_DIR / f"notify-{today}.log", [
        "[09:00:00] event=Stop kind=task_done sent=True remote_ok=True "
        "elapsed=1.0s title=✅ 重构完成",
    ])
    row = insight.stats(config.load())["recent"][0]
    assert row["summary"] == "重构完成"
    assert "✅" not in row["summary"]


def test_stats_recent_falls_back_when_title_is_missing():
    """老的事件行没有 `title=` 字段，不许渲染出一行空白。"""
    today = datetime.now().strftime("%Y-%m-%d")
    _write(config.LOG_DIR / f"notify-{today}.log", [
        "[09:00:00] event=Stop kind=task_done sent=True remote_ok=True elapsed=1.0s",
    ])
    row = insight.stats(config.load())["recent"][0]
    assert row["summary"] == "任务完成", "没有标题就用事件名顶上"


def test_stats_recent_caps_at_eight():
    today = datetime.now().strftime("%Y-%m-%d")
    _write(config.LOG_DIR / f"notify-{today}.log", [
        f"[09:00:{i:02d}] event=Stop kind=task_done sent=True remote_ok=True "
        f"elapsed=1.0s title=第 {i} 条"
        for i in range(20)
    ])
    recent = insight.stats(config.load())["recent"]
    assert len(recent) == 8
    assert recent[0]["summary"] == "第 19 条", "最新在前"


def test_stats_missing_logs_is_all_zero():
    s = insight.stats(config.load())
    assert (s["today"], s["ok"], s["fail"]) == (0, 0, 0)
    assert s["recent"] == []


# ---------------------------------------------------------------- chain_sentence


def _cfg(**kw) -> dict:
    cfg = config.load()
    cfg["enabled"] = True
    cfg["triggers"] = {k: False for k in cfg["triggers"]}
    cfg["triggers"]["task_done"] = True
    for ch in cfg["channels"]:
        ch["enabled"] = False
    cfg.update(kw)
    return cfg


def _enable(cfg, ctype):
    for ch in cfg["channels"]:
        if ch["type"] == ctype:
            ch["enabled"] = True


def test_chain_muted_when_master_off():
    cfg = _cfg(enabled=False)
    text, level = insight.chain_sentence(cfg)
    assert "已静音" in text
    assert level == insight.LEVEL_WARN


def test_chain_danger_when_no_channel():
    text, level = insight.chain_sentence(_cfg())
    assert "没有可用通道" in text
    assert level == insight.LEVEL_DANGER


def test_chain_warn_when_no_trigger():
    cfg = _cfg()
    _enable(cfg, "wxmp")
    cfg["triggers"] = {k: False for k in cfg["triggers"]}
    text, level = insight.chain_sentence(cfg)
    assert "没有勾选任何事件" in text
    assert level == insight.LEVEL_WARN


def test_chain_log_only_is_not_phone_delivery():
    """log 通道只写本地文件，不能算「送到手机」——混进去就是骗人。"""
    cfg = _cfg()
    _enable(cfg, "log")
    text, level = insight.chain_sentence(cfg)
    assert "只写入本地日志" in text
    assert "手机" in text
    assert level == insight.LEVEL_WARN


def test_chain_ok_names_channels():
    cfg = _cfg()
    _enable(cfg, "wxmp")
    text, level = insight.chain_sentence(cfg)
    assert "任务完成时" in text
    assert "送到你手机" in text
    assert level == insight.LEVEL_OK


# ------------------------------------------------ chain_sentence × 最近一次投递


def _rec(ok, msg=""):
    return {"time": "10:00", "ok": ok, "msg": msg}


def test_chain_keeps_ok_when_there_is_no_record():
    """**W3.4 验收第 4 条，必须和下面那条失败用例成对。**

    只测「失败会降级」，你不知道自己是不是把「没发过」也算成了失败。
    首次启动、日志被清空、今天还没发过——都该维持原样。
    """
    cfg = _cfg()
    _enable(cfg, "wxmp")
    for last in ({}, None):
        text, level = insight.chain_sentence(cfg, last)
        assert level == insight.LEVEL_OK, f"last={last!r} 不该降级"
        assert "送到你手机" in text
        assert "没送到" not in text


def test_chain_degrades_when_last_delivery_failed():
    """**W3.4 验收第 3 条：界面不许替失败背书。**

    配置全绿、通道「已启用」，但最近一次投递全失败——这时候还说
    「送到你手机」就是在承诺一件没发生的事。
    """
    cfg = _cfg()
    _enable(cfg, "wxmp")
    text, level = insight.chain_sentence(cfg, {"wxmp": _rec(False, "errcode=40037")})
    assert level == insight.LEVEL_DANGER
    assert "最近一次没送到" in text
    assert "送到你手机" not in text


def test_chain_failure_reason_never_leaks_into_sidebar():
    """侧栏 240px，`--fs-aux` 一行约 16 个汉字。报错原文只配去通道页。

    这里钉两件事：不出现 `errcode` / 异常名，且整句不超过 30 字。
    """
    cfg = _cfg()
    _enable(cfg, "wxmp")
    text, level = insight.chain_sentence(
        cfg, {"wxmp": _rec(False, "errcode=40037 模板 ID 无效，请到公众平台核对")})
    assert level == insight.LEVEL_DANGER
    assert "errcode" not in text
    assert "40037" not in text
    assert len(text) <= 30, f"侧栏放不下：{text}"


def test_chain_warns_when_partially_failed():
    """**W3.4 验收第 5 条：一成一败 → warn，不是 danger 也不是 ok。**"""
    cfg = _cfg()
    _enable(cfg, "bark")
    _enable(cfg, "wxmp")
    text, level = insight.chain_sentence(
        cfg, {"bark": _rec(True), "wxmp": _rec(False, "HTTP 500")})
    assert level == insight.LEVEL_WARN
    assert "部分没送到" in text


def test_chain_ignores_channels_that_were_never_tried():
    """今天没试过的通道没资格参与判定，不能算成失败拉低整句话。"""
    cfg = _cfg()
    _enable(cfg, "bark")
    _enable(cfg, "wxmp")
    text, level = insight.chain_sentence(cfg, {"bark": _rec(True)})
    assert level == insight.LEVEL_OK
    assert "送到你手机" in text


def test_delivery_health_levels():
    remote = ["bark", "wxmp"]
    assert insight.delivery_health(remote, None) == "none"
    assert insight.delivery_health(remote, {}) == "none"
    # 只有 log 有记录 → 远端一个都没试过 → none
    assert insight.delivery_health(remote, {"log": _rec(False)}) == "none"
    assert insight.delivery_health(remote, {"bark": _rec(True)}) == "ok"
    assert insight.delivery_health(
        remote, {"bark": _rec(True), "wxmp": _rec(True)}) == "ok"
    assert insight.delivery_health(
        remote, {"bark": _rec(True), "wxmp": _rec(False)}) == "partial"
    assert insight.delivery_health(
        remote, {"bark": _rec(False), "wxmp": _rec(False)}) == "fail"


def test_delivery_health_ignores_test_records():
    """**W3.5 验收第 4 条 / D19：一次「测试此通道」失败不许把侧栏永久打成红。**

    测试用的是**表单里还没保存的值**。用户想看看报错长什么样，故意填个错地址
    点一下测试，侧栏从此变红——而磁盘上的配置可能是对的。
    D14 也早就把「测试」排除在投递之外了。

    新结构里「只点过测试」表现为 **`ok=None`**（取数层把两个槽分开了，
    见 `bridge._last_send_times()`）。判定层只需认这个三态——
    它不看 `test`，也不看 `day`。
    """
    remote = ["wxmp"]
    only_test = {"wxmp": {"time": "10:00", "day": "today", "test": True,
                          "ok": None, "msg": "", "ok_day": None}}
    assert insight.delivery_health(remote, only_test) == "none"

    cfg = _cfg()
    _enable(cfg, "wxmp")
    text, level = insight.chain_sentence(cfg, only_test)
    assert level == insight.LEVEL_OK, "只有测试失败时侧栏必须维持 ok"
    assert "送到你手机" in text

    # 再来一条真实失败 → 立刻 danger
    real_fail = {"wxmp": {"time": "10:01", "day": "today", "test": False,
                          "ok": False, "msg": "errcode=40037",
                          "ok_day": "today"}}
    assert insight.delivery_health(remote, real_fail) == "fail"
    _text2, level2 = insight.chain_sentence(cfg, real_fail)
    assert level2 == insight.LEVEL_DANGER


def test_delivery_health_reads_ok_not_time():
    """两段事实分开：`time` 可以是测试的，`ok` 只来自真实记录。

    今天 08:00 点过一次测试（`time` / `test` 来自它），但真实投递最后一次是
    昨天 23:50 的失败（`ok` / `ok_day` 来自它）。判定必须用后者。

    旧结构（一个槽）在这里会说 `none`——今天的测试记录把昨天的真实失败
    **遮蔽**了，R25 修好的「零点不失忆」被「早上点一次测试」打回原形。
    """
    last = {"wxmp": {"time": "08:00", "day": "today", "test": True,
                     "ok": False, "msg": "errcode=40037 模板 ID 无效",
                     "ok_day": "yesterday"}}
    assert last["wxmp"]["time"] == "08:00", "时间用最近一条，含测试"
    assert insight.delivery_health(["wxmp"], last) == "fail", "成败用真实那条"

    cfg = _cfg()
    _enable(cfg, "wxmp")
    text, level = insight.chain_sentence(cfg, last)
    assert level == insight.LEVEL_DANGER
    assert "最近一次没送到" in text


# ---------------------------------------------------------------- channel_state


def test_channel_state_three_states():
    ready = {"type": "bark", "enabled": True, "key": "k"}
    subtitle, level, lit = insight.channel_state("bark", ready)
    assert (subtitle, level, lit) == ("已启用", insight.LEVEL_OK, True)

    off = {"type": "bark", "enabled": False, "key": "k"}
    subtitle, level, lit = insight.channel_state("bark", off)
    assert (subtitle, level, lit) == ("已配置，未启用", insight.LEVEL_WARN, False)

    empty = {"type": "bark", "enabled": False}
    subtitle, level, lit = insight.channel_state("bark", empty)
    assert subtitle.startswith("待配置：缺")
    assert level == insight.LEVEL_MUTED
    assert lit is False


def test_channel_state_counts_missing_fields():
    # wxmp 必填三项，缺 3 个才会出现「等 N 项」后缀
    subtitle, _, _ = insight.channel_state("wxmp", {"type": "wxmp"})
    assert "待配置：缺" in subtitle
    assert "等 3 项" in subtitle


def test_channel_state_reports_last_failure():
    """**W3.4 验收第 6 条：列表行要说出「最近一次失败」，不是一直「已启用」。**"""
    wx = {"type": "wxmp", "enabled": True, "appid": "a", "secret": "s",
          "template_id": "t", "openid": "o"}
    subtitle, level, lit = insight.channel_state(
        "wxmp", wx, _rec(False, "errcode=40037 模板 ID 无效，请到公众平台核对"))
    assert level == insight.LEVEL_DANGER
    assert lit is True, "已启用就该点亮，失败不是「关掉」"
    assert subtitle.startswith("最近一次失败：")
    # 7 字前缀 + 20 字报错原文，再多列表行放不下
    assert len(subtitle) <= 27, f"截断失效：{subtitle}"


def test_channel_state_failure_without_msg():
    wx = {"type": "wxmp", "enabled": True, "appid": "a", "secret": "s",
          "template_id": "t", "openid": "o"}
    subtitle, level, _ = insight.channel_state("wxmp", wx, _rec(False, "   "))
    assert subtitle == "最近一次失败", "没有原文时不许留一个空冒号"
    assert level == insight.LEVEL_DANGER


def test_channel_state_ok_after_successful_send():
    wx = {"type": "wxmp", "enabled": True, "appid": "a", "secret": "s",
          "template_id": "t", "openid": "o"}
    subtitle, level, lit = insight.channel_state("wxmp", wx, _rec(True, "ok"))
    assert (subtitle, level, lit) == ("已启用", insight.LEVEL_OK, True)


def test_channel_state_treats_none_as_no_conclusion():
    """**`ok=None` 是「真实投递一次都没发生过」，不是失败**（D19）。

    只点过测试时，取数层给出 `ok=None` + `test=True`。判定必须落到「已启用」
    绿点——否则侧栏说送到了、列表说最近一次失败，同一个界面上下矛盾。

    判据必须是 `is False`：`not None` 是 True，用 `not` 写就会把这里判成红。
    """
    wx = {"type": "wxmp", "enabled": True, "appid": "a", "secret": "s",
          "template_id": "t", "openid": "o"}
    only_test = {"time": "08:00", "day": "today", "test": True,
                 "ok": None, "msg": "", "ok_day": None}
    subtitle, level, lit = insight.channel_state("wxmp", wx, only_test)
    assert (subtitle, level, lit) == ("已启用", insight.LEVEL_OK, True)


def test_channel_state_missing_fields_wins_over_runtime_failure():
    """缺字段连试都试不了，说「最近一次失败」只会让人去查网络。

    `enabled` 可能是上一版配置留下的 True，也可能被手改 config.json 改成
    True——那时字段照样不全。配置问题必须先说。
    """
    broken = {"type": "wxmp", "enabled": True, "appid": "a"}
    subtitle, level, lit = insight.channel_state("wxmp", broken, _rec(False, "errcode=1"))
    assert level == insight.LEVEL_MUTED
    assert lit is False
    assert subtitle.startswith("待配置：缺")


# ---------------------------------------------------------------- pick_channel


def test_pick_channel_prefers_enabled_and_ready():
    cfg = _cfg()
    _enable(cfg, "log")          # log 不需要字段，直接 ready
    assert insight.pick_channel(cfg) == "log"


def test_pick_channel_falls_back_to_wxmp():
    cfg = _cfg()
    for ch in cfg["channels"]:
        ch.clear()
        ch["type"] = "webhook"   # 缺 url，谁都不 ready
    assert insight.pick_channel(cfg) == "wxmp"


# ---------------------------------------------------------------- normalize_filters


def test_normalize_filters_types():
    out = insight.normalize_filters({
        "projects_include": "a, b，c",
        "quiet_hours": " 23:00-08:00 ",
        "min_duration_seconds": "30",
        "max_per_minute": "abc",
        "question_as_need_input": 1,
    })
    assert out["projects_include"] == ["a", "b", "c"]
    assert out["quiet_hours"] == "23:00-08:00"
    assert out["min_duration_seconds"] == 30
    # 非数字回落 DEFAULT_CONFIG 里的默认值，不写死 0
    assert out["max_per_minute"] == config.DEFAULT_CONFIG["filters"]["max_per_minute"]
    assert out["question_as_need_input"] is True


def test_normalize_filters_accepts_list_input():
    out = insight.normalize_filters({"projects_include": [" a ", "", "b"]})
    assert out["projects_include"] == ["a", "b"]


# ---------------------------------------------------------------- merge_save


def test_merge_save_does_not_drop_concurrent_changes():
    """**外来方案第 5 条修正的落点。**

    UI 打开期间 hook 进程可能改了 config（这里模拟：磁盘上已有 bark.key），
    UI 只提交了 triggers 的一个字段。整份覆盖会把 bark.key 抹掉。
    """
    cfg = config.load()
    cfg["triggers"]["session_start"] = False
    config.save(cfg)
    # 模拟「另一个进程」往磁盘写了一个 UI 不知道的字段
    disk = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    for ch in disk["channels"]:
        if ch["type"] == "bark":
            ch["key"] = "hook-写进来的-key"
    config.CONFIG_PATH.write_text(json.dumps(disk, ensure_ascii=False),
                                  encoding="utf-8")

    res = insight.merge_save({"triggers": {"session_start": True}})
    assert res["ok"] is True
    assert res["cfg"]["triggers"]["session_start"] is True
    saved = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    bark = next(c for c in saved["channels"] if c["type"] == "bark")
    assert bark["key"] == "hook-写进来的-key", "并发写入的字段被覆盖了"


def test_merge_save_merges_channels_by_type():
    """patch 里只带一个通道的一个字段，其他通道的配置不能丢。"""
    cfg = config.load()
    config.save(cfg)
    before = len(json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))["channels"])

    res = insight.merge_save({"channels": [{"type": "bark", "group": "改过"}]})
    assert res["ok"] is True
    saved = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    assert len(saved["channels"]) == before
    bark = next(c for c in saved["channels"] if c["type"] == "bark")
    assert bark["group"] == "改过"
    assert "server" in bark, "同通道其他字段不能被抹掉"


def test_merge_save_runs_auto_enable():
    """填齐凭证后自动启用——现有行为，不许丢。"""
    res = insight.merge_save({
        "channels": [{"type": "bark", "key": "abc123"}],
    })
    assert res["ok"] is True
    bark = next(c for c in res["cfg"]["channels"] if c["type"] == "bark")
    assert bark["enabled"] is True
    assert any("Bark" in x for x in res["turned_on"]), \
        "turned_on 里存的是给用户看的短名"


# ---------------------------------------------------------------- reset_channel


def test_reset_channel_reads_from_disk_not_memory():
    cfg = config.load()
    config.save(cfg)
    edited = cfg["channels"][0].copy()
    edited["__dirty__"] = "用户改了一半"
    got = insight.reset_channel({"channels": [edited]}, edited["type"])
    assert "__dirty__" not in got, "重置要回到磁盘那份，不是内存副本"


# ---------------------------------------------------------------- preview


def test_preview_strips_markdown_and_renders_placeholders():
    cfg = config.load()
    out = insight.preview(
        cfg, "task_done",
        "✅ {app} 任务完成",
        "**项目**：{project}\n**耗时**：{duration}\n\n{summary}",
    )
    assert "**" not in out["body"], "微信是纯文本，预览要剥 markdown"
    assert "{project}" not in out["body"], "占位符要被替换"
    assert "demo-project" in out["body"]
    assert out["vars_used"], "用到的占位符要能列出来"


def test_preview_reports_unknown_placeholder():
    cfg = config.load()
    out = insight.preview(cfg, "task_done", "标题", "正文 {nope} 和 {project}")
    assert out["vars_missing"] == ["{nope}"], \
        "写了但不存在的变量要报出来，否则发出去是空白"
    assert "{project}" not in out["vars_missing"]


def test_preview_empty_template_does_not_raise():
    out = insight.preview(config.load(), "task_done", "", "")
    assert isinstance(out["title"], str)
    assert isinstance(out["body"], str)


# ---------------------------------------------------------------- 纯函数


def test_stats_does_not_mutate_cfg():
    cfg = config.load()
    snapshot = json.dumps(cfg, ensure_ascii=False, sort_keys=True)
    insight.stats(cfg)
    insight.chain_sentence(cfg)
    insight.pick_channel(cfg)
    assert json.dumps(cfg, ensure_ascii=False, sort_keys=True) == snapshot


def test_open_path_reports_failure_without_raising():
    ok, err = insight.open_path("Z:/definitely/not/here")
    assert ok is False
    assert err
