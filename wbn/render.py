"""模板渲染。

占位符用 {name} 形式，正则替换而不是 str.format，
这样模板里出现裸 `{`（比如 JSON 片段）也不会炸。
未知占位符渲染成空字符串。
"""

from __future__ import annotations

import getpass
import os
import re
import socket
from datetime import datetime

from . import events, store

_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

# 界面上展示给用户的占位符清单
PLACEHOLDERS = [
    ("{app}", "宿主名称，如 WorkBuddy"),
    ("{kind}", "语义类型，如 task_done"),
    ("{kind_label}", "语义中文名，如 任务完成"),
    ("{emoji}", "对应 emoji"),
    ("{event}", "原始 hook 事件名，如 Stop"),
    ("{project}", "项目名（工作目录末级）"),
    ("{cwd}", "完整工作目录"),
    ("{session}", "会话 ID 前 8 位"),
    ("{duration}", "本轮耗时，如 3 分 12 秒"),
    ("{turn}", "本会话第几轮"),
    ("{time}", "时间 HH:MM"),
    ("{date}", "日期 YYYY-MM-DD"),
    ("{datetime}", "完整日期时间"),
    ("{host}", "电脑名"),
    ("{user}", "系统用户名"),
    ("{summary}", "助手最后一段回复摘要"),
    ("{last_message}", "助手最后一段回复原文（较长）"),
    ("{tool}", "工具名（工具失败时）"),
    ("{error}", "失败原因"),
    ("{reason}", "会话结束原因 / 停止原因"),
    ("{mode}", "权限模式"),
    ("{model}", "模型名（若 payload 提供）"),
]


def build_fields(payload: dict, kind: str, cfg: dict, app: str) -> dict:
    """把 payload 摊平成模板可用的字段表。"""
    adv = cfg.get("advanced", {})
    now = datetime.now()
    sid = str(payload.get("session_id") or "")

    last_message = events.extract_last_message(payload)
    if adv.get("include_summary", True):
        summary = events.summarize(
            last_message, int(adv.get("summary_max_chars") or 200)
        )
    else:
        summary = "（已按隐私设置隐藏）"

    duration = payload.get("_duration")
    if duration is None:
        duration = store.turn_duration(sid)

    return {
        "app": app,
        "kind": kind,
        "kind_label": events.KIND_LABELS.get(kind, kind),
        "emoji": events.KIND_EMOJI.get(kind, "🔔"),
        "event": str(payload.get("hook_event_name") or ""),
        "project": events.project_name(str(payload.get("cwd") or "")),
        "cwd": str(payload.get("cwd") or ""),
        "session": sid[:8] or "—",
        "duration": events.human_duration(duration),
        "turn": str(store.turn_index(sid)) if sid else "—",
        "time": now.strftime("%H:%M"),
        "date": now.strftime("%Y-%m-%d"),
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "host": _hostname(),
        "user": _username(),
        "summary": summary,
        "last_message": last_message or "（无）",
        "tool": str(payload.get("tool_name") or "—"),
        "error": events.extract_error(payload),
        "reason": str(payload.get("reason") or payload.get("stop_reason") or "—"),
        "mode": str(payload.get("permission_mode") or "—"),
        "model": str(payload.get("model") or "—"),
    }


def render(template: str, fields: dict) -> str:
    """按字段表替换占位符。"""
    if not template:
        return ""
    return _PLACEHOLDER.sub(lambda m: str(fields.get(m.group(1), "")), template)


def render_message(payload: dict, kind: str, cfg: dict, app: str) -> tuple[str, str, dict]:
    """返回 (标题, 正文, 字段表)。"""
    fields = build_fields(payload, kind, cfg, app)
    titles = cfg.get("titles", {})
    templates = cfg.get("templates", {})
    title = render(titles.get(kind) or titles.get("task_done", ""), fields).strip()
    content = render(templates.get(kind, ""), fields).strip()
    if not title:
        title = f"{fields['emoji']} {fields['app']} {fields['kind_label']}"
    return title, content, fields


def _hostname() -> str:
    try:
        return socket.gethostname() or "unknown"
    except Exception:
        return "unknown"


def _username() -> str:
    try:
        return os.environ.get("USERNAME") or getpass.getuser() or "unknown"
    except Exception:
        return "unknown"
