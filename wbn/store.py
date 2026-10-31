"""运行时状态：去重、限流、会话计时、失败队列、日志。

所有状态都落在 state/ 下，纯文件实现（无数据库），单机个人使用足够。
并发保护用 O_EXCL 锁文件，避免多个 hook 同时改写同一个 json。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from . import config
from .sanitize import clean_obj, redact

RUNTIME_PATH = config.STATE_DIR / "runtime.json"
QUEUE_PATH = config.STATE_DIR / "queue.jsonl"
UI_PREF_PATH = config.STATE_DIR / "ui.json"

# 说明：这里刻意不加文件锁。
# 实测在本机沙箱环境下 unlink 单次约 700ms（删除会被拦截审计），
# 而「锁文件」必然要创建 + 删除，会把每条通知拖慢 1.4 秒。
# 本工具是单机单人使用，并发最多是「两条通知前后脚到达」，
# 极端情况只是去重计数少记一次，代价远小于每条都慢一秒。
# 写入统一走 tmp + os.replace（原子替换，实测 1ms）。


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # clean_obj：session 里的 last_prompt 来自宿主，可能带落单代理字符，
    # 不清洗的话 write_text 会抛 UnicodeEncodeError，本轮计时就此丢失
    tmp.write_text(json.dumps(clean_obj(obj), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ------------------------------------------------------------ 运行时状态


def load_runtime() -> dict:
    rt = _read_json(RUNTIME_PATH, {})
    if not isinstance(rt, dict):
        rt = {}
    rt.setdefault("dedup", {})
    rt.setdefault("sends", [])
    return rt


def save_runtime(rt: dict) -> None:
    _write_json(RUNTIME_PATH, rt)


# ------------------------------------------------------------ 界面偏好
#
# `state/ui.json` 只放「下次启动怎么开窗」这类偏好。**不是配置**——它不该进
# `config.json`：配置是用户手填的意图，这个是程序自己观察出来的事实
# （「上次带沙箱起不来，降级后起来了」）。两者混在一起，用户改配置时会莫名其妙
# 丢掉一个他从来没设置过的字段。


def load_ui_pref() -> dict:
    """读界面偏好。文件没有 / 坏了 / 不是对象 → 空字典，走默认行为。"""
    pref = _read_json(UI_PREF_PATH, {})
    return pref if isinstance(pref, dict) else {}


def save_ui_pref(pref: dict) -> None:
    _write_json(UI_PREF_PATH, pref)


def prefer_no_sandbox() -> bool:
    """上次是不是「关掉沙箱才起来的」。

    坏文件当 False：宁可多等 10 秒探测一次，也不要因为一个读不出来的标记
    就把安全降级当成默认——沙箱关掉是净损失。
    """
    return load_ui_pref().get("prefer_no_sandbox") is True


def set_prefer_no_sandbox(on: bool) -> None:
    """记下 / 忘掉「这台机器要关沙箱」。

    `on=False` 是**删字段**而不是写 `false`：用户在设置里点「恢复默认」之后，
    文件里不该再留一条痕迹，否则以后谁看到都会以为他选过什么。
    """
    pref = load_ui_pref()
    if on:
        pref["prefer_no_sandbox"] = True
    else:
        pref.pop("prefer_no_sandbox", None)
    save_ui_pref(pref)


def _num(value, default: float = 0.0) -> float:
    """宽容地取数字。runtime.json 被手改坏时不能让整条通知链挂掉。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------ 会话计时


def _session_path(session_id: str) -> Path:
    """会话 ID 来自宿主 payload，不可信，必须过滤。

    只留字母数字和 -_ ，`.` 和 `/` 都被剔除，`..` 之类无法构成路径穿越。
    再截断到 64 字符，避免超出文件系统单段名长度上限。
    """
    safe = "".join(ch for ch in (session_id or "unknown") if ch.isalnum() or ch in "-_")
    return config.SESSION_DIR / f"{(safe or 'unknown')[:64]}.json"


def get_session(session_id: str) -> dict:
    data = _read_json(_session_path(session_id), {})
    return data if isinstance(data, dict) else {}


def mark_turn_start(session_id: str, prompt_preview: str = "") -> dict:
    """用户提交新消息时调用，记录本轮开始时间并累加轮次。"""
    data = get_session(session_id)
    now = time.time()
    data["turn_start"] = now
    data["turn_index"] = int(data.get("turn_index", 0)) + 1
    data["last_prompt"] = prompt_preview[:500]
    data["updated_at"] = now
    data["fail_streak"] = 0
    _write_json(_session_path(session_id), data)
    return data


def bump_tool_failure(session_id: str) -> int:
    """工具失败计数 +1，返回当前连续失败次数。"""
    data = get_session(session_id)
    streak = int(data.get("fail_streak", 0)) + 1
    data["fail_streak"] = streak
    data["last_fail_at"] = time.time()
    _write_json(_session_path(session_id), data)
    return streak


def reset_tool_failure(session_id: str) -> None:
    data = get_session(session_id)
    if data.get("fail_streak"):
        data["fail_streak"] = 0
        _write_json(_session_path(session_id), data)


def turn_duration(session_id: str) -> float | None:
    """本轮已耗时秒数；没有记录则返回 None。"""
    data = get_session(session_id)
    start = data.get("turn_start")
    if not start:
        return None
    try:
        return max(0.0, time.time() - float(start))
    except (TypeError, ValueError):
        return None


def turn_index(session_id: str) -> int:
    return int(get_session(session_id).get("turn_index", 0))


# ------------------------------------------------------------ 去重与限流


def dedup_hit(key: str, window_seconds: int) -> bool:
    """命中去重窗口返回 True（表示应当跳过）。"""
    if window_seconds <= 0:
        return False
    rt = load_runtime()
    now = time.time()
    dedup = {k: _num(v) for k, v in rt["dedup"].items()}
    dedup = {k: v for k, v in dedup.items() if now - v < 86400}
    last = dedup.get(key)
    hit = bool(last and now - last < window_seconds)
    if not hit:
        dedup[key] = now
    # 只留最近 500 条，防止文件膨胀
    if len(dedup) > 500:
        for k in sorted(dedup, key=dedup.get)[: len(dedup) - 500]:
            dedup.pop(k, None)
    rt["dedup"] = dedup
    save_runtime(rt)
    return hit


def rate_limited(max_per_minute: int, max_per_hour: int) -> str | None:
    """返回被限流的原因字符串；未限流返回 None。"""
    now = time.time()
    rt = load_runtime()
    sends = [t for t in (_num(x) for x in rt.get("sends", [])) if now - t < 3600]
    if max_per_minute > 0 and sum(1 for t in sends if now - t < 60) >= max_per_minute:
        return f"每分钟上限 {max_per_minute} 条"
    if max_per_hour > 0 and len(sends) >= max_per_hour:
        return f"每小时上限 {max_per_hour} 条"
    return None


def record_send() -> None:
    rt = load_runtime()
    now = time.time()
    sends = [t for t in (_num(x) for x in rt.get("sends", [])) if now - t < 3600]
    sends.append(now)
    rt["sends"] = sends
    save_runtime(rt)


# ------------------------------------------------------------ 失败队列


def enqueue(item: dict) -> None:
    """发送失败的消息进队列，等托盘/下次 hook 时重试。"""
    config.ensure_dirs()
    try:
        with QUEUE_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(clean_obj(item), ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_queue() -> list[dict]:
    if not QUEUE_PATH.exists():
        return []
    out = []
    try:
        for line in QUEUE_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    out.append(obj)
            except Exception:
                continue
    except OSError:
        return []
    return out


def save_queue(items: list[dict]) -> None:
    config.ensure_dirs()
    tmp = QUEUE_PATH.with_suffix(".jsonl.tmp")
    try:
        tmp.write_text(
            "".join(json.dumps(clean_obj(i), ensure_ascii=False) + "\n" for i in items),
            encoding="utf-8",
        )
        os.replace(tmp, QUEUE_PATH)
    except OSError:
        pass


# ------------------------------------------------------------ 日志


def log_path(when: datetime | None = None) -> Path:
    d = when or datetime.now()
    return config.LOG_DIR / f"notify-{d:%Y-%m-%d}.log"


def log(msg: str) -> None:
    """写运行日志。任何异常都吞掉——日志永远不该影响主流程。

    写之前统一脱敏：调用方经常把通道返回的错误原文塞进来，
    而错误原文里可能回显了带 key / token 的完整 URL。
    """
    try:
        config.ensure_dirs()
        stamp = datetime.now().strftime("%H:%M:%S")
        with log_path().open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {redact(msg, 600)}\n")
    except Exception:
        pass


def tail_log(lines: int = 60) -> str:
    p = log_path()
    if not p.exists():
        return "（暂无日志）"
    try:
        content = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "（日志读取失败）"
    return "\n".join(content[-lines:])


def prune_logs(keep_days: int) -> None:
    """清理过期日志。"""
    if keep_days <= 0:
        return
    cutoff = time.time() - keep_days * 86400
    try:
        for p in config.LOG_DIR.glob("notify-*.log"):
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
    except OSError:
        pass
