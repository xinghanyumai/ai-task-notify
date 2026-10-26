# -*- coding: utf-8 -*-
"""触发条件判定：静默时段 / 项目黑白名单 / 关键词 / 时长 / 去重 / 频率闸门。

判定顺序见 filters.check 的文档。这里的重点是**边界值**：
静默时段跨零点、限流刚好达到上限、去重窗口刚好过期——这些差一分钟就
判断反了，人眼看 selftest 的输出根本发现不了。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from wbn import config, filters, store


def _cfg(**filters_override) -> dict:
    """一份最小可用配置：总开关开、事件开、无项目/关键词限制。"""
    cfg = config.load()
    cfg["enabled"] = True
    cfg["triggers"] = {k: True for k in cfg["triggers"]}
    cfg["filters"].update(
        {
            "quiet_hours": "",
            "projects_include": [],
            "projects_exclude": [],
            "keywords_include": [],
            "min_duration_seconds": 0,
            "dedup_seconds": 0,
            "max_per_minute": 0,
            "max_per_hour": 0,
        }
    )
    cfg["filters"].update(filters_override)
    return cfg


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 26, hour, minute)


# ---------------------------------------------------------------- 静默时段


@pytest.mark.parametrize(
    "hhmm,expected",
    [
        ("23:30", True),    # 跨零点段内（前一天晚上）
        ("00:30", True),    # 跨零点段内（第二天凌晨）
        ("07:59", True),
        ("08:00", True),    # 端点含在区间内
        ("08:01", False),   # 刚过端点
        ("12:00", False),
        ("22:59", False),
    ],
)
def test_quiet_hours_spanning_midnight(hhmm, expected):
    """23:00-08:00 必须真的跨过零点，否则半夜照样弹通知。"""
    hour, minute = (int(x) for x in hhmm.split(":"))
    assert filters.in_quiet_hours("23:00-08:00", _at(hour, minute)) is expected


@pytest.mark.parametrize(
    "hhmm,expected",
    [("11:59", False), ("12:00", True), ("13:30", True), ("13:31", False)],
)
def test_quiet_hours_within_same_day(hhmm, expected):
    hour, minute = (int(x) for x in hhmm.split(":"))
    assert filters.in_quiet_hours("12:00-13:30", _at(hour, minute)) is expected


def test_quiet_hours_supports_multiple_segments():
    spec = "23:00-08:00,12:00-13:30"
    assert filters.in_quiet_hours(spec, _at(0, 10)) is True
    assert filters.in_quiet_hours(spec, _at(13, 0)) is True
    assert filters.in_quiet_hours(spec, _at(15, 0)) is False


def test_quiet_hours_accepts_fullwidth_colon():
    assert filters.in_quiet_hours("23：00-08：00", _at(23, 30)) is True


@pytest.mark.parametrize("spec", ["", "   ", "abc", "23:00", "25:00-26:00", "23:00-"])
def test_invalid_quiet_spec_never_silences(spec):
    """配置写坏了不能变成「永远静默」——那等于通知全丢。"""
    assert filters.in_quiet_hours(spec, _at(12, 0)) is False


# ---------------------------------------------------------------- check()


def test_master_switch_off_blocks_everything():
    cfg = _cfg()
    cfg["enabled"] = False
    ok, why = filters.check("task_done", {"cwd": "D:/x"}, cfg)
    assert ok is False
    assert "总开关" in why


def test_disabled_trigger_blocks_that_kind_only():
    cfg = _cfg()
    cfg["triggers"]["task_done"] = False
    assert filters.check("task_done", {"cwd": "D:/x"}, cfg)[0] is False
    assert filters.check("need_input", {"cwd": "D:/x"}, cfg)[0] is True


def test_quiet_hours_blocks_send():
    cfg = _cfg(quiet_hours="00:00-23:59")
    ok, why = filters.check("task_done", {"cwd": "D:/x"}, cfg)
    assert ok is False
    assert "静默" in why


def test_project_whitelist_requires_match():
    cfg = _cfg(projects_include=["Work buddy"])
    assert filters.check("task_done", {"cwd": "D:/Code/Work buddy 通知"}, cfg)[0] is True
    ok, why = filters.check("task_done", {"cwd": "D:/Code/other"}, cfg)
    assert ok is False
    assert "白名单" in why


def test_project_blacklist_blocks_match():
    cfg = _cfg(projects_exclude=["vendor"])
    ok, why = filters.check("task_done", {"cwd": "D:/Code/vendor/lib"}, cfg)
    assert ok is False
    assert "黑名单" in why
    assert filters.check("task_done", {"cwd": "D:/Code/mine"}, cfg)[0] is True


def test_project_match_supports_wildcard_and_backslashes():
    cfg = _cfg(projects_exclude=["*\\tmp\\*"])
    ok, _ = filters.check("task_done", {"cwd": "D:\\tmp\\scratch"}, cfg)
    assert ok is False


def test_keywords_must_appear_in_summary_or_prompt():
    cfg = _cfg(keywords_include=["部署"])
    assert filters.check("task_done", {"cwd": "D:/x", "summary": "开始部署到测试机"}, cfg)[0] is True
    ok, why = filters.check("task_done", {"cwd": "D:/x", "summary": "改了个错别字"}, cfg)
    assert ok is False
    assert "关键词" in why


def test_min_duration_only_applies_to_done_and_failed():
    """秒回的寒暄不值得弹窗，但「等你确认」哪怕很快也要提醒。"""
    cfg = _cfg(min_duration_seconds=20)
    assert filters.check("task_done", {"cwd": "D:/x", "duration": 5}, cfg)[0] is False
    assert filters.check("task_done", {"cwd": "D:/x", "duration": 30}, cfg)[0] is True
    assert filters.check("need_input", {"cwd": "D:/x", "duration": 5}, cfg)[0] is True


def test_ignore_rate_limit_flag_bypasses_the_gate():
    cfg = _cfg(max_per_minute=1)
    for _ in range(5):
        store.record_send()
    assert filters.check("task_done", {"cwd": "D:/x"}, cfg)[0] is False
    assert filters.check("task_done", {"cwd": "D:/x"}, cfg, ignore_rate_limit=True)[0] is True


# ---------------------------------------------------------------- 频率闸门


def test_rate_limit_per_minute_triggers_exactly_at_the_cap():
    cfg_max = 2
    for _ in range(cfg_max - 1):
        store.record_send()
    assert store.rate_limited(cfg_max, 0) is None
    store.record_send()                      # 第 2 条，刚好达到上限
    assert store.rate_limited(cfg_max, 0) is not None


def test_rate_limit_per_hour():
    for _ in range(3):
        store.record_send()
    assert store.rate_limited(0, 3) is not None
    assert store.rate_limited(0, 4) is None


def test_rate_limit_zero_means_unlimited():
    for _ in range(50):
        store.record_send()
    assert store.rate_limited(0, 0) is None


def test_rate_limit_state_stays_inside_tmp(isolated_state):
    """闸门状态必须落在隔离目录里，绝不能污染真实 state/。"""
    store.record_send()
    assert (isolated_state / "state" / "runtime.json").exists()


# ---------------------------------------------------------------- 去重窗口


def test_dedup_second_identical_call_is_a_hit():
    assert store.dedup_hit("k1", 180) is False
    assert store.dedup_hit("k1", 180) is True


def test_dedup_ignores_different_keys():
    assert store.dedup_hit("k1", 180) is False
    assert store.dedup_hit("k2", 180) is False


def test_dedup_disabled_when_window_is_zero():
    assert store.dedup_hit("k1", 0) is False
    assert store.dedup_hit("k1", 0) is False


def test_dedup_window_expiry(monkeypatch):
    """窗口 1 秒：把时钟推后 2 秒，同一条内容必须重新放行。"""
    import time as _time

    assert store.dedup_hit("k1", 1) is False
    real = _time.time
    monkeypatch.setattr(store.time, "time", lambda: real() + 2)
    assert store.dedup_hit("k1", 1) is False
