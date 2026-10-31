"""把 hook 的原始事件翻译成「人话语义」。

CodeBuddy / WorkBuddy 的 hooks 与 Claude Code 同构，共 27+ 个事件，
但用户真正关心的只有五种语义。这一层负责收敛：

    Stop                -> task_done  （若最后一句是问句则 -> need_input）
    StopFailure         -> task_failed
    PostToolUseFailure  -> tool_failed
    PermissionRequest   -> need_input
    Notification        -> need_input（permission_prompt / idle_prompt）
    SessionStart/End    -> session_start / session_end
    TaskCompleted       -> task_done
"""

from __future__ import annotations

import json
import re
from pathlib import Path

KIND_LABELS = {
    "task_done": "任务完成",
    "task_failed": "任务失败",
    "need_input": "等你确认",
    "tool_failed": "工具失败",
    "session_start": "会话开始",
    "session_end": "会话结束",
    "other": "事件通知",
    "test": "测试通知",
}

KIND_EMOJI = {
    "task_done": "✅",
    "task_failed": "❌",
    "need_input": "🙋",
    "tool_failed": "⚠️",
    "session_start": "▶️",
    "session_end": "⏹",
    "other": "🔔",
    "test": "🔔",
}

# 会话结束原因里，这些不值得打扰用户
_BORING_SESSION_END = {"clear", "logout", "prompt_input_exit"}

# 需要「等你处理」的 Notification 子类型
_NEED_INPUT_NOTIFY_TYPES = {
    "permission_prompt",
    "elicitation_dialog",
    "plan_approval",
}

# 这些 Notification 是噪音，一律不通知。
# idle_prompt = 助手答完在等你说话，跟 Stop 已经报过的「任务完成」是同一件事，
# 再推一条「等你确认」纯属重复打扰（实测每条回复都会跟着来一条）。
_IDLE_NOTIFY_TYPES = {"idle_prompt", "auth_success"}
_IDLE_MESSAGE_HINTS = (
    "waiting for your input",
    "waiting for input",
    "is waiting",
    "idle",
)

_QUESTION_TAIL = re.compile(
    r"(?:[?？]\s*$)|(?:吗|呢|么|吧)\s*[。.!！?？]?\s*$|"
    r"(?:请|麻烦)(?:你|您)?(?:确认|选择|提供|告知)",
)


def ends_with_question(text: str) -> bool:
    """粗略判断助手最后一句话是不是在等用户回答。"""
    if not text:
        return False
    tail = text.strip().splitlines()[-1].strip() if text.strip() else ""
    # 只看最后 120 字，避免长文里的问号误判
    tail = tail[-120:]
    return bool(_QUESTION_TAIL.search(tail))


def classify(payload: dict) -> str | None:
    """返回语义 kind；返回 None 表示这个事件不需要处理。"""
    ev = payload.get("hook_event_name") or payload.get("event") or ""

    if ev == "Stop":
        # stop_hook_active=True 表示本次 Stop 是 hook 触发继续后的再次停止，
        # 不是用户感知的「任务结束」，跳过以免重复通知。
        if payload.get("stop_hook_active"):
            return None
        return "task_done"

    if ev in ("StopFailure", "SubagentStopFailure"):
        return "task_failed"

    if ev == "PostToolUseFailure":
        return "tool_failed"

    if ev == "PermissionRequest":
        return "need_input"

    if ev == "Notification":
        ntype = str(payload.get("notification_type") or "")
        msg = str(payload.get("message") or "")
        # 空闲提醒：助手只是答完在等下一条消息，Stop 已经报过了，别再打扰一次
        if ntype in _IDLE_NOTIFY_TYPES:
            return None
        if any(h in msg.lower() for h in _IDLE_MESSAGE_HINTS):
            return None
        if ntype in _NEED_INPUT_NOTIFY_TYPES:
            return "need_input"
        # 未知子类型：有 message 就当需要关注
        return "need_input" if msg else None

    if ev == "SessionStart":
        return "session_start"

    if ev == "SessionEnd":
        if str(payload.get("reason") or "") in _BORING_SESSION_END:
            return None
        return "session_end"

    if ev == "TaskCompleted":
        return "task_done"

    return None


# ------------------------------------------------------------ 字段提取


def _texts_from_content(content) -> list[str]:
    """从 transcript 的 content 字段里抽出纯文本片段。"""
    out: list[str] = []
    if isinstance(content, str):
        out.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict):
                if part.get("type") in (None, "text", "output_text"):
                    t = part.get("text")
                    if isinstance(t, str):
                        out.append(t)
    return out


def read_last_assistant_text(transcript_path: str, max_bytes: int = 512 * 1024) -> str:
    """从会话 transcript(.jsonl) 尾部倒着找最后一条助手文本。

    不同版本 / 不同宿主的行结构不完全一致，这里做了多形状兼容：
        {"type":"assistant","message":{"content":[{"type":"text","text":".."}]}}
        {"role":"assistant","content":".."}
        {"type":"assistant","text":".."}
    """
    if not transcript_path:
        return ""
    p = Path(transcript_path)
    if not p.is_file():
        return ""
    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # 丢掉被截断的半行
            raw = f.read()
    except OSError:
        return ""

    lines = raw.split(b"\n")
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line.decode("utf-8", "replace"))
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue

        # 形状 1：嵌套 message
        msg = obj.get("message")
        if isinstance(msg, dict):
            role = msg.get("role") or obj.get("type")
            if role == "assistant":
                texts = _texts_from_content(msg.get("content"))
                joined = "\n".join(t for t in texts if t.strip())
                if joined.strip():
                    return joined.strip()

        # 形状 2：role + content 平铺
        if obj.get("role") == "assistant":
            texts = _texts_from_content(obj.get("content"))
            joined = "\n".join(t for t in texts if t.strip())
            if joined.strip():
                return joined.strip()

        # 形状 3：type=assistant + text
        if obj.get("type") == "assistant" and isinstance(obj.get("text"), str):
            if obj["text"].strip():
                return obj["text"].strip()

    return ""


def extract_last_message(payload: dict) -> str:
    """优先用 payload 自带的 last_assistant_message，其次读 transcript。"""
    for key in ("last_assistant_message", "last_message", "assistant_message"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # 权限请求 / 空闲提醒没有助手消息，用宿主给的提示语代替
    ev = payload.get("hook_event_name") or ""
    if ev in ("PermissionRequest", "Notification", "Elicitation"):
        v = payload.get("message")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return read_last_assistant_text(str(payload.get("transcript_path") or ""))


def extract_error(payload: dict) -> str:
    """抽取失败原因文本。"""
    for key in ("error", "error_message", "message", "reason", "stop_reason"):
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    resp = payload.get("tool_response")
    if isinstance(resp, dict):
        for key in ("error", "stderr", "message"):
            v = resp.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return "未提供详细原因"


def project_name(cwd: str) -> str:
    """从工作目录取项目名。"""
    if not cwd:
        return "未知项目"
    try:
        return Path(str(cwd).replace("\\", "/")).name or str(cwd)
    except Exception:
        return str(cwd)


_FENCE = "```"
_TABLE_CHARS = set("|-: ")


def _flatten_md_tables(text: str) -> str:
    """把 markdown 表格压平成 ` · ` 连接的纯文本行，顺手丢掉代码围栏标记。

    为什么非做不可：目标通道（微信 / ClawBot / Bark / ntfy / 短信）**都不渲染
    markdown**，`|---|---|---|` 对用户是纯噪声；更要命的是分隔行每行白吃十几个
    字符的摘要预算——三行表格就吃掉 200 字上限的近 1/3，正文还没开始就被截断。
    这是「摘要越来越没用」的直接原因。

    **只动表格行和围栏行，其余行原样保留**：`summarize()` 被所有模板共用，
    顺手「美化」别的东西等于一次改 11 个通道的正文。
    """
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        # ① 围栏标记行整行丢。围栏里的**内容**不丢，那不是本轮的事。
        if s.startswith(_FENCE):
            continue
        # ② 表格分隔行：整行只有 | - : 空格，且必须有 -。
        #    要带 `"-" in s`，否则 `|  |` 这种空表格行也会被吞掉。
        if s and "-" in s and not (set(s) - _TABLE_CHARS):
            continue
        # ③ 数据行 → 去首尾 |、按 | 切、空单元格丢掉、用 ` · ` 连接。
        #    要求至少两个单元格：`| grep foo` 这种 shell 片段不许被改成 `grep foo`。
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                out.append(" · ".join(cells))
                continue
        out.append(line)
    return "\n".join(out)


def summarize(text: str, max_chars: int = 200) -> str:
    """压缩成一行摘要，保留首句与尾部信息。"""
    if not text:
        return "（无摘要）"
    # 先压平表格，再走原来的空白折叠 / 修饰符清理 / 截断。
    # 顺序不能反：折叠会把多行表格并成一行，之后就分不清哪是分隔行了。
    flat = _flatten_md_tables(text)
    flat = re.sub(r"\s+", " ", flat).strip()
    # 去掉 markdown 修饰符，避免推送里一堆星号
    flat = re.sub(r"[*_`#>]+", "", flat)
    flat = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", flat)
    flat = flat.strip()
    if len(flat) <= max_chars:
        return flat
    return flat[: max_chars - 1].rstrip() + "…"


def human_duration(seconds) -> str:
    """秒 -> 中文可读时长。"""
    try:
        s = int(float(seconds))
    except (TypeError, ValueError):
        return "—"
    if s < 0:
        return "—"
    if s < 60:
        return f"{s} 秒"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m} 分 {sec} 秒" if sec else f"{m} 分"
    h, m = divmod(m, 60)
    return f"{h} 小时 {m} 分"
