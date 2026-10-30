"""重复通知：终态事件按「事件身份」去重，非终态保持原样。

**这组测试是成对的，别只留一半。**

- M1 证「该合的合了」：一个 turn 里宿主连发 3 条 `TaskCompleted`，只该有一条通知
- M2 证「该分的还分」：两个**不同 turn** 各一条，必须发两条

只写 M1 的话，把去重键换成常量也能全绿——那等于把「用户连续做完两件事」
的第二条通知吃掉。M2 就是挡这件事的。

M3 / M4 是反方向的守卫：修法只能作用在终态事件上。
一个 turn 里真的可能问两次（两次不同的权限请求），真的可能「先成功后失败」，
这两类都按 turn 去重就会把真通知吃掉。

为什么键里不能有 `content`（2026-10-30 的线上现象）：默认模板带 `{duration}`，
同一个 turn 的三次 `TaskCompleted` 分别渲染成 12s / 15s / 18s，
**三条 content 三个样 → 三个键 → 三条全发**。去重本身没坏，是键选错了字段。
"""

from __future__ import annotations

import pytest

from wbn import config, pipeline, render, store

SESSION = "t-dedup-turn"


def _cfg(**filters_override) -> dict:
    """只留 log 通道：整条链路真跑，但不联网。去重窗口用默认的 180s。"""
    cfg = config.load()
    cfg["enabled"] = True
    cfg["channels"] = [{"type": "log", "enabled": True}]
    cfg["triggers"] = {k: True for k in cfg["triggers"]}
    cfg["filters"].update(
        {
            "quiet_hours": "",
            "projects_include": [],
            "projects_exclude": [],
            "keywords_include": [],
            "min_duration_seconds": 0,
            "dedup_seconds": 180,
            "max_per_minute": 0,
            "max_per_hour": 0,
            "question_as_need_input": False,
        }
    )
    cfg["filters"].update(filters_override)
    return cfg


def _completed() -> dict:
    return {
        "hook_event_name": "TaskCompleted",
        "session_id": SESSION,
        "cwd": "D:/Code/demo",
        "last_assistant_message": "改好了，测试全部通过。",
    }


@pytest.fixture
def ticking_duration(monkeypatch):
    """让 `store.turn_duration` 每次调用往后走 3 秒。

    线上那三条日志是 14:54:30 / :33 / :36，中间各隔 3 秒，渲染出来是
    12s / 15s / 18s。测试里三次调用是毫秒级的，真耗时几乎不动，
    不模拟就复现不出「三条 content 三个样」——那样测试是假绿。
    """
    seq = iter([12.0, 15.0, 18.0, 21.0, 24.0])
    monkeypatch.setattr(store, "turn_duration", lambda _sid: next(seq, 99.0))
    return seq


# ---------------------------------------------------------------- M1：该合的合了


@pytest.mark.usefixtures("ticking_duration")
def test_m1_same_turn_three_completions_send_once():
    """同一 turn 连发 3 条 TaskCompleted（耗时递增）→ 只发 1 条。"""
    cfg = _cfg()
    store.mark_turn_start(SESSION, "跑一下测试")   # turn_index = 1

    # 前提：三条 content 必须真的不一样。一样的话旧代码也能去重，
    # 这条测试就是假绿——它要证明的恰恰是「content 变了也照样去重」。
    contents = [
        render.render_message(
            {**_completed(), "_duration": d}, "task_done", cfg, "WorkBuddy"
        )[1]
        for d in (12.0, 15.0, 18.0)
    ]
    assert len(set(contents)) == 3, "前提不成立：三条 content 相同，测不出这个 bug"

    results = [pipeline.process(_completed(), "WorkBuddy", cfg) for _ in range(3)]

    assert [r["sent"] for r in results] == [True, False, False]
    # 不能只断言 sent is False：静音、频率闸门、通道全关都能让它变 False。
    # 必须钉住「是因为 turn 内重复才没发」。
    assert results[1]["skipped"] == "turn 内重复"
    assert results[2]["skipped"] == "turn 内重复"
    # 三条的键必须相同——这正是「不看 content」的直接证据
    assert len({r["dedup_key"] for r in results}) == 1


def test_m1_key_ignores_content_for_terminal_kind():
    """终态事件的键不含 title/content——这就是修法的本体。"""
    a = pipeline._dedup_key("task_done", SESSION, "✅ 完成", "耗时 12 秒", turn=3)
    b = pipeline._dedup_key("task_done", SESSION, "✅ 完成", "耗时 18 秒", turn=3)
    c = pipeline._dedup_key("task_done", SESSION, "✅ 完成", "耗时 12 秒", turn=4)

    assert a == b, "同一个 turn 的两次完成必须同键（content 不许参与）"
    assert a != c, "不同 turn 必须不同键（否则连续两件事会被合成一条）"


def test_terminal_kind_falls_back_to_content_key_without_turn():
    """turn_index 读不到（0）时退回 content 键：宁可多发一条，不许丢。"""
    a = pipeline._dedup_key("task_done", SESSION, "✅ 完成", "耗时 12 秒", turn=0)
    b = pipeline._dedup_key("task_done", SESSION, "✅ 完成", "耗时 18 秒", turn=0)

    assert a != b, "读不到 turn 时必须按 content 分开，不能把两条都吞掉"


# ---------------------------------------------------------------- M2：该分的还分


def test_m2_two_different_turns_send_twice():
    """两个不同 turn 各一条 TaskCompleted → 必须发 2 条。红了就是修过头。"""
    cfg = _cfg()

    store.mark_turn_start(SESSION, "第一件事")     # turn 1
    first = pipeline.process(_completed(), "WorkBuddy", cfg)

    store.mark_turn_start(SESSION, "第二件事")     # turn 2
    second = pipeline.process(_completed(), "WorkBuddy", cfg)

    assert first["sent"] is True
    assert second["sent"] is True, "第二个 turn 的通知被吃掉了——去重键把 turn 丢了"
    # 断言键不同，而不是只数条数：将来哪怕通道变成 2 条也说明不了去重对不对
    assert first["dedup_key"] != second["dedup_key"]


# ------------------------------------------------------- M3：非终态不按 turn 去重


def test_m3_two_need_input_in_one_turn_send_twice():
    """一个 turn 里问两次（两次不同的权限请求）→ 发 2 条。"""
    cfg = _cfg()
    store.mark_turn_start(SESSION, "改配置")       # turn 1

    def perm(what: str) -> dict:
        return {
            "hook_event_name": "PermissionRequest",
            "session_id": SESSION,
            "cwd": "D:/Code/demo",
            "message": what,
        }

    first = pipeline.process(perm("允许写入 config.json 吗？"), "WorkBuddy", cfg)
    second = pipeline.process(perm("允许执行 git push 吗？"), "WorkBuddy", cfg)

    assert first["kind"] == "need_input"
    assert first["sent"] is True
    assert second["sent"] is True, "同一 turn 的第二次询问被吞了——非终态不许按 turn 去重"
    assert first["dedup_key"] != second["dedup_key"]


# --------------------------------------------------- M4：kind 必须参与键


def test_m4_done_and_failed_in_one_turn_send_twice():
    """同一 turn 里「完成」和「失败」各一条 → 发 2 条。"""
    cfg = _cfg()
    store.mark_turn_start(SESSION, "跑测试")       # turn 1

    done = pipeline.process(
        {
            "hook_event_name": "Stop",
            "session_id": SESSION,
            "cwd": "D:/Code/demo",
            "last_assistant_message": "全部通过。",
            "_duration": 30.0,
        },
        "WorkBuddy",
        cfg,
    )
    failed = pipeline.process(
        {
            "hook_event_name": "StopFailure",
            "session_id": SESSION,
            "cwd": "D:/Code/demo",
            "last_assistant_message": "构建失败。",
            "_duration": 30.0,
        },
        "WorkBuddy",
        cfg,
    )

    assert done["kind"] == "task_done"
    assert failed["kind"] == "task_failed"
    assert done["sent"] is True
    assert failed["sent"] is True, "kind 没进键——同一 turn 的失败被当成重复吞了"
    assert done["dedup_key"] != failed["dedup_key"]


# ------------------------------------------------- 兜底：窗口关了也不能崩


def test_dedup_disabled_still_sends_everything():
    """用户把 dedup_seconds 设成 0（关掉去重）→ 三条都发，不能崩。"""
    cfg = _cfg(dedup_seconds=0)
    store.mark_turn_start(SESSION, "跑一下")

    results = [pipeline.process(_completed(), "WorkBuddy", cfg) for _ in range(3)]

    assert [r["sent"] for r in results] == [True, True, True]


@pytest.mark.parametrize("kind", ["task_done", "task_failed"])
def test_terminal_kinds_are_the_only_ones_with_turn_key(kind):
    """白名单只有这两个；别的 kind 一旦混进来就是修过头。"""
    assert kind in pipeline.TERMINAL_KINDS


def test_non_terminal_kinds_are_not_in_terminal_set():
    for kind in ("need_input", "tool_failed", "session_start", "session_end"):
        assert kind not in pipeline.TERMINAL_KINDS
