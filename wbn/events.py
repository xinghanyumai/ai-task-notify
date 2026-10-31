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
import unicodedata
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
    # 去掉 markdown 修饰符，避免推送里一堆星号。
    #
    # **`_` 不能无脑删。** 原来这里是 `[*_`#>]+`，把下划线也当 markdown 清了，
    # 于是 `is_our_hook` 变成 `isourhook`、`_our_hook_exe_paths()` 变成
    # `ourhookexepaths()`——任何涉及文件名 / 函数名的通知在手机上都成了乱码级
    # 可读性（W16 §0 第 1 条，P0）。
    #
    # 但成对的下划线是斜体，`_斜体_` 还是得剥。所以先单独处理成对的，
    # 再清 `*` / `` ` `` / `#` / `>`。**顺序不能反**：先清别的字符不影响配对，
    # 反过来先把 `*` 清掉倒也不影响 `_` 配对，但成对 `_` 必须先于「裸 `_` 保留」
    # 这个决策执行，否则剥斜体的信息就没了。
    #
    # 配对要求两侧都不是「词字符」（`\w`：字母、数字、下划线，含中文）。
    # 用 `\w` 而不是 `[A-Za-z0-9]` 有两个原因：`__init__` 这种 dunder 的
    # 外层 `_` 本身是 `\w`，不该被当成斜体边界；`这是_斜体_文字` 这种没空格的
    # 中文粘连也不是 markdown 斜体，不该动。
    flat = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", flat)
    flat = re.sub(r"[*`#>]+", "", flat)
    # 注意：这里**不做**连续空格折叠。剥掉 `#` / `>` 会留下两个空格，看着丑，
    # 但那是既有行为，`test_plain_text_is_untouched` 用逐字节断言钉着
    # （「顺手美化它就是在改 11 个通道的正文」）。W16 只解决下划线，不动这条。
    flat = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", flat)
    flat = flat.strip()
    if len(flat) <= max_chars:
        return flat
    return flat[: max_chars - 1].rstrip() + "…"


# ---------------------------------------------------------------- 卡片摘要（W16）
#
# `summarize()` 是**给 11 个通道共用的**，也是关键词过滤器的匹配源
# （`pipeline.py` 的 `ctx["summary"]`）。它压短了 `filters.check` 会漏匹配——
# 用户配了关键词 `CI`，摘要里没有就把通知过滤掉了。所以 `summarize()` 的
# 签名、默认值、调用点一个字都不动。
#
# 手机卡片是另一回事：微信模板消息一个变量只占一行，4 个详情变量装不下
# 200 字（实测 186 字摘要只有 97 字能显示，89 字白算），而且 `flat[:199]`
# 从头硬截，**真正的结论永远在最后一句，一个字都显示不出来**。
#
# 所以这里是一个独立的、只在微信卡片切段之前调用的抽取式摘要：
# 选句子，不改写。不调模型——hook 只有 1.5 秒预算，调一次模型 1–3 秒必然超；
# 而且通知内容不能有幻觉，手机上一句编造的「已完成」比没有通知更糟。

# 句末标点。切句时保留，不丢。
_CARD_SENT_END = "。！？；!?;"

# 客套 / 过程开头。整句丢掉——这些句子信息量为 0，占的却是最贵的头几行。
_CARD_POLITE = (
    "好的", "好，", "我来", "我先", "已经为你", "已为你", "具体来说", "具体做了",
    "首先", "接下来", "下面", "让我们", "我这就", "正在", "稍等",
    "完成了以下", "做了以下几件事",
)

# 结论词。含这些词的句子有信息量，排序时优先，也算「步骤枚举」的豁免条件。
_CARD_CONCLUSION = (
    "通过", "失败", "完成", "绿", "红", "错误", "报错", "成功",
    # W17 补两个：`5. 重新打包并验证` 原来 0 分，但「验证」在这类回复里
    # 就是结论性动作；「已推送」同理。
    "验证", "推送",
)

# 拉丁标识符：`install.py` / `is_our_hook` / `onedir` / `_our_hook_exe_paths`。
# 首位必须是字母（下划线开头的 `_our_hook_exe_paths` 会从 `o` 开始匹配，
# 长度照样够），后面允许字母数字和 `. _ - /`。
_CARD_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_.\-/]{1,}")

# 不算标识符的英文短词。`ok` / `error` / `true` / `false` / `None` 这类
# 在技术回复里是**状态词**不是标识符，给它们加分等于给噪声加分。
_CARD_IDENT_STOP = frozenset({
    "ok", "okay", "error", "true", "false", "none", "null", "nil",
    "and", "the", "for", "with", "this", "that", "from", "not", "yes", "no",
    "is", "in", "or", "to", "of", "on", "at", "by", "as", "if", "it", "be",
    "was", "are", "has", "have", "had", "but", "so", "we", "you", "all",
})

# 中文数量短语：`4 条` `3 个` `2 处`。**降权到 +1，但不删**——纯中文回复里
# 它还是唯一能区分「有没有具体信息」的信号（§2.3 第 1 条）。
_CARD_COUNT_PHRASE = re.compile(r"\d+\s*(?:条|个|处|项|次|行|块)")

# 步骤枚举的开头：`1.` `2.` `-` `•`。全角顿号 / 括号也认。
_CARD_STEP_PREFIX = re.compile(r"^\s*(?:\d+\s*[.、)）]|[-•·*])\s*")

# 句子**内部**的步骤标记边界。
#
# 为什么需要它：进到卡片这一层时文本已经被 `summarize()` 压成一行了
# （`re.sub(r"\s+", " ")` 把换行全折成空格），于是
#   `具体来说，我做了以下几件事：` + `1. 把 …` + … + `现在所有 675 个用例都通过了`
# 粘成**一整句**。这一句以「具体来说」开头，会被当成客套话整句丢掉——
# 实测四行只剩一句「好的，我已经完成了…」，结论照样看不见。
# 所以在步骤标记前面补一刀，把句子边界找回来。
_CARD_STEP_BREAK = re.compile(r"(?<=\s)(?=(?:\d+\s*[.、)）]|[-•·])\s)")

# 代码残留：这些符号在句子里出现 3 次以上，说明是代码不是人话。
_CARD_CODE_TOKENS = ("()", "{}", "=>", ";")

# 短于这个字数的碎片并到前一句（「好的。」这种断句碎片没有独立价值）。
_CARD_MIN_FRAGMENT = 6


def _card_width(text: str) -> int:
    """一段文本占几个半角位。

    **口径必须与 `channels._width` 一致**：汉字 / 全角算 2，ASCII 算 1。
    不能从 channels 引进来——channels 依赖 events，反过来引就成环了。
    两边是否同口径由 `tests/test_summarize.py` 的对拍用例钉住。
    """
    return sum(
        2 if unicodedata.east_asian_width(c) in ("W", "F", "A") else 1 for c in text
    )


def _split_card_sentences(text: str) -> list[str]:
    """按句末标点和换行切句，保留句末标点；太短的碎片并到前一句。"""
    raw: list[str] = []
    buf = ""
    for ch in text:
        if ch == "\n":
            if buf.strip():
                raw.append(buf.strip())
            buf = ""
            continue
        buf += ch
        if ch in _CARD_SENT_END:
            if buf.strip():
                raw.append(buf.strip())
            buf = ""
    if buf.strip():
        raw.append(buf.strip())

    # 压平过的文本里，步骤标记只以空格分隔。再切一刀把句子边界找回来。
    pieces: list[str] = []
    for s in raw:
        for part in _CARD_STEP_BREAK.split(s):
            if part.strip():
                pieces.append(part.strip())

    merged: list[str] = []
    for s in pieces:
        if merged and len(s) < _CARD_MIN_FRAGMENT:
            merged[-1] += s
        else:
            merged.append(s)
    return merged


def _card_noise(s: str) -> str | None:
    """这句属于哪类噪声。`None` = 干净句子。"""
    body = s.lstrip()
    if body.startswith(_CARD_POLITE):
        return "polite"
    if sum(body.count(t) for t in _CARD_CODE_TOKENS) >= 3:
        return "code"
    if _CARD_STEP_PREFIX.match(body) and not any(w in body for w in _CARD_CONCLUSION):
        return "step"
    return None


def _card_is_ident(token: str) -> bool:
    """这个拉丁词算不算「实质改动标识符」。"""
    t = token.strip(".,;:()[]{}\"'")
    if len(t) < 3:
        return False
    return t.lower() not in _CARD_IDENT_STOP


def _card_density(s: str) -> int:
    """信息密度：**标识符 > 结论词 > 数量短语 > 其余数字**。

    打分前先剥掉步骤标记——`4. 补了 4 条单元测试` 里标记的 `4.` 不算数。

    W17 换掉了 W16 那套「有数字就 +2」。原因是中文技术回复里的数字绝大多数
    出现在**计数**里：`4 条测试`、`3 个文件`、`2 处`——这些说明不了改了什么。
    真正说明「改了什么」的是拉丁标识符：`install.py`、`is_our_hook`、`onedir`。

    W16 实测的病根就在这：那段输入里六句话，三条实质改动（`is_our_hook` /
    `_our_hook_exe_paths` / `or "wbn" in cmd`）全是 0 分，唯一有数字的
    「补了 4 条单元测试」拿 2 分排第一，于是手机上第一行是「补了 4 条单元测试」，
    最要紧的「把 is_our_hook 改成支持 onedir 路径」一个字都没进卡片。
    """
    body = _CARD_STEP_PREFIX.sub("", s)
    score = 0
    if any(_card_is_ident(m) for m in _CARD_IDENT.findall(body)):
        score += 3
    if any(w in body for w in _CARD_CONCLUSION):
        score += 2
    if _CARD_COUNT_PHRASE.search(body):
        score += 1
    # 其余数字不加分：结论句里已经有结论词了，单独的 `675` 不额外说明什么。
    return score


def _clip_card_width(text: str, budget: int) -> str:
    """按半角宽度截断并补省略号。**只在「一句话就超预算」时用**。

    正常路径是整句取舍（宁可少一句，不要半句话）；但如果结论句自己就装不下，
    丢掉它等于没通知，只能截。
    """
    if _card_width(text) <= budget:
        return text
    out = ""
    used = 0
    for ch in text:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1
        if used + cw > budget - 2:  # 留 2 格给省略号
            break
        out += ch
        used += cw
    return out.rstrip() + "…"


def summarize_for_card(text: str, budget: int = 68) -> str:
    """把长回复压成一张微信卡片装得下的摘要：结论优先，整句取舍。

    budget 是**半角宽度**（一个汉字算 2），默认 68 ≈ 4 行 × 34 的一半。
    """
    if not text or not text.strip():
        return ""
    stripped = text.strip()
    # 短文本原样返回：本来装得下，加工只会引入噪声。
    if _card_width(stripped) <= budget:
        return stripped

    sents = _split_card_sentences(stripped)
    if not sents:
        return ""

    # 第一步已经做完（切句）。第二步：分类。
    clean: list[int] = []
    steps: list[int] = []
    for i, s in enumerate(sents):
        kind = _card_noise(s)
        if kind is None:
            clean.append(i)
        elif kind == "step":
            # **步骤枚举不整句丢，只降到最低优先级。**
            # 任务书 §3.2 把「纯步骤枚举」也列为整句丢掉，但那样一来 §0 那段
            # 输入只剩末句 24 字，§5 第 4 条（四行总字数 ≥ 40）必然不成立——
            # 两条要求直接冲突。§5 是唯一硬判据，所以这里让步：步骤行是真内容，
            # 只是不含结论，排到最后补位；客套话和代码残留仍然整句丢。
            steps.append(i)

    if not clean and not steps:
        # 全是客套话，兜底取首句——不许空手（§5 边界用例 3）。
        return _clip_card_width(sents[0], budget)

    pool: dict[int, str] = {i: sents[i] for i in clean}
    pool.update({i: sents[i] for i in steps})

    # 第三步：按优先级填预算。末句先占位——结论几乎总在最后一句。
    last_i = (clean or steps)[-1]
    first_i = clean[0] if clean and clean[0] != last_i else None

    chosen: list[int] = []
    used = 0

    def take(i: int) -> bool:
        nonlocal used
        extra = _card_width(pool[i]) + (1 if chosen else 0)
        if used + extra > budget:
            return False
        chosen.append(i)
        used += extra
        return True

    if not take(last_i):
        # 结论句自己就超预算：截它，别丢它。
        return _clip_card_width(pool[last_i], budget)
    if first_i is not None:
        take(first_i)

    middles = [i for i in clean if i not in (last_i, first_i)]
    middles.sort(key=lambda i: (-_card_density(pool[i]), i))
    for i in middles:
        take(i)

    # 步骤行也按信息密度排：含数字（`4 条`）的比「重新打包并验证」这种有内容。
    # 放不下就跳过，接着看更短的——预算要用满，卡片不能只留一句「已完成」。
    steps.sort(key=lambda i: (-_card_density(pool[i]), i))
    for i in steps:
        take(i)

    # 第四步：按预算裁剪已经体现在 `take()` 里（放不下就整句跳过，不硬切）。
    # 输出按**原文顺序**，读起来才是连贯的，而不是按优先级乱序。
    return " ".join(pool[i] for i in sorted(chosen))


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
