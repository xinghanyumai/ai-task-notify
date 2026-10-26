# -*- coding: utf-8 -*-
"""事件语义收敛：27+ 个宿主事件 -> 用户真正在意的五种。

关键规则都来自实测噪音：
- `idle_prompt` 每条回复后面都跟一条，和 Stop 报的「完成」是同一件事，必须过滤；
- `auth_success` 是后台刷凭证，用户不需要知道；
- Stop 之后如果助手最后一句是问句，说明它在等你回答，归 need_input 更符合直觉。
"""

from __future__ import annotations

import pytest

from wbn import config, events, pipeline


# ---------------------------------------------------------------- classify


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"hook_event_name": "Stop"}, "task_done"),
        ({"hook_event_name": "TaskCompleted"}, "task_done"),
        ({"hook_event_name": "StopFailure"}, "task_failed"),
        ({"hook_event_name": "SubagentStopFailure"}, "task_failed"),
        ({"hook_event_name": "PostToolUseFailure"}, "tool_failed"),
        ({"hook_event_name": "PermissionRequest"}, "need_input"),
        ({"hook_event_name": "SessionStart"}, "session_start"),
        ({"hook_event_name": "SessionEnd", "reason": "other"}, "session_end"),
    ],
)
def test_event_maps_to_expected_kind(payload, expected):
    assert events.classify(payload) == expected


@pytest.mark.parametrize(
    "payload",
    [
        {"hook_event_name": "Notification", "notification_type": "idle_prompt"},
        {"hook_event_name": "Notification", "notification_type": "auth_success"},
        {"hook_event_name": "Notification", "notification_type": "whatever",
         "message": "Claude is waiting for your input"},
        {"hook_event_name": "SessionEnd", "reason": "clear"},
        {"hook_event_name": "SessionEnd", "reason": "logout"},
        {"hook_event_name": "SessionEnd", "reason": "prompt_input_exit"},
        {"hook_event_name": "Stop", "stop_hook_active": True},
        {"hook_event_name": "SomethingBrandNew"},
        {},
    ],
)
def test_noisy_or_irrelevant_events_are_silent(payload):
    """返回 None = 不通知。这些每一条都是实测噪音，不能变成弹窗。"""
    assert events.classify(payload) is None


def test_permission_prompt_needs_input():
    payload = {"hook_event_name": "Notification", "notification_type": "permission_prompt"}
    assert events.classify(payload) == "need_input"


def test_unknown_notification_with_message_still_warns():
    """未知子类型但有 message：宁可提醒，也不能把真正的等待吞掉。"""
    payload = {"hook_event_name": "Notification",
               "notification_type": "elicitation_dialog",
               "message": "请选择继续方式"}
    assert events.classify(payload) == "need_input"


def test_legacy_event_key_is_supported():
    assert events.classify({"event": "Stop"}) == "task_done"


# ------------------------------------------------------- ends_with_question


@pytest.mark.parametrize(
    "text",
    [
        "已经改好了，你看这样可以吗？",
        "这样可以吗",
        "要我现在就删掉它吗。",
        "请确认是否继续",
        "麻烦你提供一下 appsecret",
    ],
)
def test_question_tail_is_detected(text):
    assert events.ends_with_question(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "改好了，测试通过。",
        "Done.",
        "",
        "文件里有问号吗？不，没有。",   # 问号不在结尾
        "第一步：读文件？ 第二步：写完。",
    ],
)
def test_statement_tail_is_not_a_question(text):
    assert events.ends_with_question(text) is False


def test_only_the_last_line_decides():
    assert events.ends_with_question("第一段是陈述。\n最后一句是问句吗") is True
    assert events.ends_with_question("第一段是问句吗？\n最后一句是陈述。") is False


# ------------------------------------------- Stop + 问句 -> need_input（链路）

def _log_only_cfg() -> dict:
    """只留 log 通道：整条链路跑通，但不联网、不发通知。"""
    cfg = config.load()
    cfg["channels"] = [{"type": "log", "enabled": True}]
    cfg["filters"]["min_duration_seconds"] = 0
    cfg["filters"]["dedup_seconds"] = 0
    cfg["triggers"]["task_done"] = True
    cfg["triggers"]["need_input"] = True
    return cfg


@pytest.mark.usefixtures("no_network")
def test_stop_with_question_becomes_need_input():
    payload = {
        "hook_event_name": "Stop",
        "session_id": "t-question",
        "cwd": "D:/Code/demo",
        "last_assistant_message": "已经改好了，你看这样可以吗？",
    }
    result = pipeline.process(payload, "WorkBuddy", _log_only_cfg(), ignore_limits=True)

    assert result["kind"] == "need_input"
    assert "等你确认" in result["title"]


@pytest.mark.usefixtures("no_network")
def test_stop_with_statement_stays_task_done():
    payload = {
        "hook_event_name": "Stop",
        "session_id": "t-statement",
        "cwd": "D:/Code/demo",
        "last_assistant_message": "改好了，测试全部通过。",
    }
    result = pipeline.process(payload, "WorkBuddy", _log_only_cfg(), ignore_limits=True)

    assert result["kind"] == "task_done"
    assert "任务完成" in result["title"]
