"""通知流水线：事件 -> 语义 -> 过滤 -> 渲染 -> 去重 -> 多通道发送。

hook.py 落盘后由 dispatch.py 调用 process()；
设置中心的「测试发送」也复用同一套渲染逻辑，保证测试即真实效果。
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime

from . import channels, config, events, filters, render, store

# 终态事件：一个 turn 的「结果」只值一条通知。
# 非终态（need_input / tool_failed / session_*）不在此列——一个 turn 里真的
# 可能问两次、真的可能连续失败三次，按 turn 去重会把真通知吃掉。
TERMINAL_KINDS = frozenset({"task_done", "task_failed"})


def _dedup_key(kind: str, session: str, title: str, content: str,
               turn: int = 0) -> str:
    """去重键。

    **终态事件按「事件身份」去重，不按「渲染结果」。**

    2026-10-30 线上现象：一个任务弹两三条一模一样的通知。日志里 14:54:30 /
    :33 / :36 三条 `TaskCompleted` 全部 `sent=True`。根因是旧键
    `kind|session|title|content` 把渲染结果当成了事件身份——而默认模板带
    `{duration}`，宿主对这个 turn 连发三次时耗时是 12s / 15s / 18s，
    **三条 content 三个样 → 三个键 → 三条全发**。去重本身没坏（同一个 payload
    连发三次照样只发一条），是键选错了字段。

    所以终态事件的键是 `kind|session|turn:<turn_index>`，content 不参与。
    `kind` 必须留着：同一个 turn 里「完成」和「失败」是两条不同的事。
    `session` 必须留着：不同会话的 turn 1 不是同一个 turn。

    turn <= 0（旧会话文件没有 turn_index、或事件早于 UserPromptSubmit）时
    退回 content 键——**宁可多发一条，不许丢**。
    """
    if kind in TERMINAL_KINDS and turn > 0:
        raw = f"{kind}|{session}|turn:{turn}"
    else:
        raw = f"{kind}|{session}|{title}|{content}"
    # errors="replace" 兜底：万一上游漏了清洗，也不能因为一个脏字符丢掉整条通知
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def process(payload: dict, app: str, cfg: dict | None = None,
            ignore_limits: bool = False) -> dict:
    """处理一条事件。返回结构化结果，便于日志与调试。

    ignore_limits 只给自检脚本用：它要能连续跑完 4 个场景验证链路，
    不该被「每分钟 4 条」的频率闸门挡住。
    """
    cfg = cfg or config.load()
    started = time.time()
    result = {
        "app": app,
        "event": payload.get("hook_event_name") or "",
        "kind": None,
        "sent": False,
        "skipped": None,
        "channels": [],
        "title": "",
        "dedup_key": "",
    }

    kind = events.classify(payload)
    if not kind:
        result["skipped"] = "事件无需通知"
        return result

    sid = str(payload.get("session_id") or "")

    # 「最后一句是问句」说明它在等你回答，归为 need_input 更符合直觉
    if kind == "task_done" and cfg.get("filters", {}).get("question_as_need_input", True):
        if events.ends_with_question(events.extract_last_message(payload)):
            kind = "need_input"

    result["kind"] = kind

    # 本轮耗时：由 UserPromptSubmit 打点，这里取差值
    duration = None
    if kind in ("task_done", "task_failed", "need_input"):
        duration = store.turn_duration(sid)
    payload["_duration"] = duration

    ctx = {
        "cwd": str(payload.get("cwd") or ""),
        "duration": duration,
        "summary": "",
        "prompt": str(store.get_session(sid).get("last_prompt") or ""),
    }
    # 摘要用于关键词过滤，先粗算一份
    ctx["summary"] = events.summarize(
        events.extract_last_message(payload),
        int(cfg.get("advanced", {}).get("summary_max_chars") or 200),
    )

    ok, why = filters.check(kind, ctx, cfg, ignore_rate_limit=ignore_limits)
    if not ok:
        result["skipped"] = why
        # `app=` 加在末尾：这一行不是 `_EVENT_LINE` 解析的对象（`skip` 开头），
        # 位置只求「一眼能看出是哪个宿主」。R63 的起因就是排查时靠时间戳猜宿主。
        store.log(f"skip event={result['event']} kind={kind} reason={why} app={app}")
        return result

    title, content, fields = render.render_message(payload, kind, cfg, app)
    result["title"] = title

    dedup_window = int(cfg.get("filters", {}).get("dedup_seconds") or 0)
    # 只有终态事件才去查 turn_index：非终态用 content 键，查了也不用。
    turn = store.turn_index(sid) if kind in TERMINAL_KINDS else 0
    key = _dedup_key(kind, sid, title, content, turn)
    result["dedup_key"] = key
    if store.dedup_hit(key, dedup_window):
        result["skipped"] = "turn 内重复" if turn > 0 else f"{dedup_window}s 内重复内容"
        store.log(f"skip dedup kind={kind} turn={turn} title={title[:40]} app={app}")
        return result

    results = channels.send_all(cfg, title, content, fields)
    result["channels"] = [{"type": t, "ok": o, "msg": m} for t, o, m in results]

    any_ok = any(o for _, o, _ in results)
    remote_ok = any(o for t, o, _ in results if t not in ("log",))

    if any_ok:
        store.record_send()
        result["sent"] = True
    else:
        result["skipped"] = "全部通道发送失败"
        if channels.has_remote_channel(cfg):
            store.enqueue(
                {
                    "app": app,
                    "kind": kind,
                    "title": title,
                    "content": content,
                    "ts": time.time(),
                    "tries": 0,
                }
            )

    store.log(
        f"event={result['event']} kind={kind} sent={result['sent']} "
        f"remote_ok={remote_ok} elapsed={time.time()-started:.2f}s "
        f"app={app} title={title[:50]}"
    )
    return result


def _safe_num(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def retry_queue(cfg: dict | None = None, limit: int = 5) -> int:
    """重试失败队列，返回成功条数。"""
    cfg = cfg or config.load()
    items = store.load_queue()
    if not items:
        return 0

    remaining: list[dict] = []
    sent = 0
    for idx, item in enumerate(items):
        if idx >= limit:
            remaining.append(item)
            continue
        tries = int(_safe_num(item.get("tries"), 0)) + 1
        if tries > 5:
            store.log(f"queue 丢弃（重试超限）: {item.get('title','')[:40]}")
            continue
        fields = {
            "app": item.get("app", "WorkBuddy"),
            "kind": item.get("kind", "other"),
            "kind_label": events.KIND_LABELS.get(item.get("kind", ""), ""),
            "datetime": datetime.fromtimestamp(
                _safe_num(item.get("ts"), time.time())
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "project": "—",
            "event": "queued",
            "time": datetime.now().strftime("%H:%M"),
            "host": render._hostname(),
            "user": render._username(),
            "summary": "",
            "last_message": "",
            "tool": "—",
            "error": "—",
            "reason": "—",
            "mode": "—",
            "model": "—",
            "duration": "—",
            "turn": "—",
            "session": "—",
            "cwd": "",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "emoji": events.KIND_EMOJI.get(item.get("kind", ""), "🔔"),
        }
        results = channels.send_all(cfg, item.get("title", ""), item.get("content", ""), fields)
        if any(o for _, o, _ in results):
            sent += 1
        else:
            item["tries"] = tries
            remaining.append(item)

    store.save_queue(remaining)
    return sent


def send_test(cfg: dict | None = None, channel_types: list[str] | None = None) -> list[dict]:
    """发送一条测试通知，走完整渲染链路。"""
    cfg = cfg or config.load()
    payload = {
        "hook_event_name": "Test",
        "session_id": "test-session",
        "cwd": str(config.ROOT),
        "last_assistant_message": "这是一条测试通知，收到即表示通道配置正确。",
    }
    title, content, fields = render.render_message(payload, "test", cfg, cfg.get("app_name", "WorkBuddy"))

    targets = config.enabled_channels(cfg)
    if channel_types:
        targets = [c for c in targets if c.get("type") in channel_types]

    timeout = int(cfg.get("advanced", {}).get("http_timeout") or 8)
    out = []
    for ch in targets:
        ok, msg = channels.send_one(ch, title, content, fields, timeout)
        out.append({"type": ch.get("type"), "ok": ok, "msg": msg})
        # 写成 `test-send type=` 而不是 `test channel=`：后者含 `channel=` 子串，
        # 会被 `insight.stats()` 当成一次真实发送数进去（「今日通知 7 / 成功 7 /
        # 失败 6」就是这么来的）。日志本身照留——排查「点了测试为什么没反应」
        # 要靠它，只是不许进统计。
        store.log(f"test-send type={ch.get('type')} ok={ok} msg={msg[:120]}")
    return out
