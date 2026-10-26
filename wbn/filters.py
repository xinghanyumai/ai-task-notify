# -*- coding: utf-8 -*-
"""触发条件判定：决定一条事件到底要不要发到手机上。

判定顺序（任一不通过即静默丢弃，并记录原因到日志）：
    enabled -> 事件开关 -> 静默时段 -> 项目过滤 -> 关键词 -> 时长门槛 -> 频率闸门
去重放在渲染之后（需要内容指纹），由 dispatch 负责。
"""

from __future__ import annotations

import fnmatch
import re
from datetime import datetime

from . import store


def _parse_hhmm(text: str) -> int | None:
    m = re.fullmatch(r"\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*", text or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return h * 60 + mi


def in_quiet_hours(spec: str, now: datetime | None = None) -> bool:
    """判断当前是否处于静默时段。支持跨零点，如 "23:00-08:00"。"""
    spec = (spec or "").strip()
    if not spec:
        return False
    now = now or datetime.now()
    cur = now.hour * 60 + now.minute
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" not in part:
            continue
        a, b = part.split("-", 1)
        start, end = _parse_hhmm(a), _parse_hhmm(b)
        if start is None or end is None:
            continue
        if start <= end:
            if start <= cur <= end:
                return True
        else:  # 跨零点
            if cur >= start or cur <= end:
                return True
    return False


def _match_any(value: str, patterns: list[str]) -> bool:
    """子串或通配符匹配。"""
    v = (value or "").replace("\\", "/")
    for pat in patterns:
        if not pat:
            continue
        p = str(pat).replace("\\", "/")
        if p in v:
            return True
        if fnmatch.fnmatch(v, p):
            return True
        if fnmatch.fnmatch(v.lower(), p.lower()):
            return True
    return False


def check(kind: str, ctx: dict, cfg: dict, ignore_rate_limit: bool = False) -> tuple[bool, str]:
    """返回 (是否放行, 说明)。说明用于日志。

    ignore_rate_limit 给「显式自检」用：连跑两遍 selftest 会撞上
    每分钟 4 条的闸门，看起来像功能坏了，其实只是限流在正常工作。
    """
    if not cfg.get("enabled", True):
        return False, "总开关已关闭"

    triggers = cfg.get("triggers", {})
    if not triggers.get(kind, False):
        return False, f"事件类型 {kind} 未开启通知"

    flt = cfg.get("filters", {})

    if in_quiet_hours(flt.get("quiet_hours", "")):
        return False, "处于静默时段"

    cwd = str(ctx.get("cwd") or "")
    include = flt.get("projects_include") or []
    exclude = flt.get("projects_exclude") or []
    if include and not _match_any(cwd, include):
        return False, f"项目不在白名单内：{cwd}"
    if exclude and _match_any(cwd, exclude):
        return False, f"项目命中黑名单：{cwd}"

    keywords = [k for k in (flt.get("keywords_include") or []) if k]
    if keywords:
        haystack = f"{ctx.get('summary','')} {ctx.get('prompt','')} {cwd}"
        if not any(str(k).lower() in haystack.lower() for k in keywords):
            return False, "未命中关键词"

    # 时长门槛只作用于「完成 / 失败」——秒回的寒暄不值得弹通知，
    # 但「等你确认」哪怕很快也要提醒。
    if kind in ("task_done", "task_failed"):
        try:
            min_dur = float(flt.get("min_duration_seconds") or 0)
        except (TypeError, ValueError):
            min_dur = 0.0
        dur = ctx.get("duration")
        if min_dur > 0 and dur is not None and dur < min_dur:
            return False, f"耗时 {dur:.0f}s 低于门槛 {min_dur:.0f}s"

    if ignore_rate_limit:
        return True, "ok"

    reason = store.rate_limited(
        int(flt.get("max_per_minute") or 0), int(flt.get("max_per_hour") or 0)
    )
    if reason:
        return False, f"触发限流（{reason}）"

    return True, "ok"
