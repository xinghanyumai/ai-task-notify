"""config / 日志 → 前端可直接渲染的数据。不含任何 UI 依赖。

W0 的产物。它是 `ui.py` 和未来 `bridge.py` 的**公共下层**：
tkinter 版和 webview 版都调它，所以两边行为必然一致——这是 W6「新旧并排验收」
能成立的前提。

三条硬规则（违反即返工）：

1. 不出现颜色、字体、尺寸。不导入任何 GUI 库，不出现十六进制色值。
2. 状态用语义枚举 `"ok" | "warn" | "danger" | "muted"`，颜色由 UI 层映射。
3. 只做纯函数。入参是 `cfg`，出参是 dict / tuple / bool；没有 self、没有缓存、
   没有全局可变状态。

搬运来源与改动边界（只允许改这三处）：

- `self.cfg` → 入参 `cfg`
- 颜色 → 语义等级
- `self.toast.show(...)` → 删掉（播报是 UI 的事，不是业务的事）

**不许顺手优化。** 下面几个函数里各有一处「看起来可以简化、其实不能动」的过滤，
注释里都写明了原因。那些坑都是踩过的。
"""

from __future__ import annotations

import copy
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import channels, config, events, pipeline, render

# 语义等级。UI 层把它映射成具体颜色，本模块不知道任何颜色。
LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_DANGER = "danger"
LEVEL_MUTED = "muted"

# 表单里是逗号分隔的字符串，config 里是 list[str] 的三项
_LIST_KEYS = ("projects_include", "projects_exclude", "keywords_include")

# emoji 的码位区间。日志标题开头通常是 ✅ / ⚠️ 这类 emoji，
# 前端拿到的是纯文本，剥掉之后用「结果点」表达成败，不重复。
_EMOJI_RANGES = (
    (0x2190, 0x21FF), (0x2300, 0x23FF), (0x25A0, 0x27BF),
    (0x2B00, 0x2BFF), (0xFE00, 0xFE0F), (0x1F000, 0x1FAFF),
)

_LOG_LINE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) \[([^\]]+)\] (\S+) \| (.*)$")

# 模板预览用的样例 payload。放在这里而不是 UI 里：它是「数据」，
# 两套 UI 要用同一份，否则预览出来的东西不一样。
SAMPLE_PAYLOAD = {
    "hook_event_name": "Stop",
    "session_id": "preview",
    "cwd": "D:/Code/demo-project",
    "last_assistant_message": "重构完成：推送通道已拆成可插拔模块，新增微信测试号通道。",
    # 预览里把耗时、工具名这些也补上，否则模板里的占位符会渲染成「—」
    "_duration": 192,
    "tool_name": "Bash",
    "permission_mode": "acceptEdits",
}


# ---------------------------------------------------------------- 字符串工具


def split_emoji(text: str) -> tuple[str, str]:
    """把开头连续的 emoji 与后面的文字分开，返回 (emoji, 其余)。"""
    i = 0
    for ch in text or "":
        o = ord(ch)
        if any(a <= o <= b for a, b in _EMOJI_RANGES):
            i += 1
        elif ch == " " and i > 0:
            i += 1
        else:
            break
    return text[:i].strip(), text[i:].lstrip()


def to_int(text, default: int) -> int:
    """转 int，失败回落默认值。空字符串、None、非数字都走回落。"""
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return default


def split_list(text) -> list[str]:
    """逗号（中英文）分隔 → 去空白的列表。"""
    return [x.strip() for x in re.split(r"[,，]", text or "") if x.strip()]


# ---------------------------------------------------------------- 状态翻译


def _is_test_line(line: str) -> bool:
    """是不是手动点「测试此通道」写下的行。

    两种写法都要认：

    - `test-send type=bark ok=False …`（W3.1 起 `pipeline.send_test` 这么写）
    - `test channel=bark ok=False …`（W3.1 之前的历史行，日志里还留着）

    只判**时间戳之后的第一段**，不在整行里找 `test`：`msg=` 后面是通道返回的
    原文，属于不可信文本，里面出现 "test" 很常见（`connection reset by test-host`
    之类），整行匹配会误伤。
    """
    body = line.split("] ", 1)[-1]
    return body.startswith("test-send ") or body.startswith("test ")


def _is_sent_event(line: str) -> bool:
    """`pipeline.notify()` 写的「这条发出去了」汇总行。

    形如 `[10:24:23] event=Stop kind=task_done sent=True remote_ok=False …`。

    判据是**时间戳后第一段就是 `event=`**，加上行内有 `sent=True`：

    - `skip event=…`（被过滤链拦下）第一段是 `skip`，不算；
    - `test-send type=…` 第一段是 `test-send`，手动测试不算通知；
    - `sent=False` 表示所有通道都失败，那不算「发出去了」，语义与旧版一致。
    """
    body = line.split("] ", 1)[-1]
    return body.startswith("event=") and "sent=True" in body


def stats(cfg: dict) -> dict:
    """今日统计 + 最近 8 条通知。

    返回 `{today, ok, fail, channels, recent: [{time, channel, summary, ok}]}`。

    **两个数据源，用途别混：**

    - `today` 数 `notify-*.log` 里 `event=… sent=True` 的汇总行。
      它由 `pipeline.notify()` 写，**和通道开关无关**——通道全关也照写。
      以前数的是 `notifications.log`，而那个文件全项目只有
      `channels._send_log` 写；用户关掉「写入本地日志」通道，这个数就冻住，
      而 `ok`/`fail` 继续涨，于是又是一次「几个数对不上」。
    - `ok` / `fail` 数同一个文件里的通道行，判据是**含 `channel=` 子串**
      （`in`，**不是** `startswith`）。每个事件在通道行之后还有一行汇总
      `… remote_ok=False`，那一行会被 "ok=False" 命中，失败数直接翻倍
      （19 显示成 38）。这段 docstring 以前写「开头」，和代码对不上——
      正因如此，测试发送那个 bug 一直没被发现。
      以 `test ` / `test-send ` 开头的行是手动点「测试此通道」产生的，不算投递，
      跳过；历史行**不删**（复盘要看）。
    - `recent` 读 `notifications.log`，那是用户唯一能读的正文记录。
      标题开头的 ✅/⚠️ 要剥掉——成败已经由 `ok` 字段表达，emoji 是重复信号，
      而且 Canvas 字体没有 emoji 字形。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    sent, ok, fail = 0, 0, 0

    lp = config.LOG_DIR / f"notify-{today}.log"
    if lp.exists():
        try:
            for line in lp.read_text(encoding="utf-8",
                                     errors="replace").splitlines():
                if _is_sent_event(line):
                    sent += 1
                if _is_test_line(line):
                    continue
                if "channel=" not in line:
                    continue
                if "ok=True" in line:
                    ok += 1
                elif "ok=False" in line:
                    fail += 1
        except Exception:
            pass

    rows: list[dict] = []
    np = config.LOG_DIR / "notifications.log"
    if np.exists():
        try:
            for line in np.read_text(encoding="utf-8",
                                     errors="replace").splitlines():
                m = _LOG_LINE.match(line)
                if not m:
                    continue
                _emoji, plain = split_emoji(m.group(5))
                rows.append({
                    "time": m.group(2),
                    "channel": "",
                    "summary": (plain or m.group(5))[:52],
                    "ok": m.group(4) not in ("task_failed", "tool_failed"),
                })
        except Exception:
            pass

    return {
        "today": sent, "ok": ok, "fail": fail,
        "channels": len(config.enabled_channels(cfg)),
        "recent": list(reversed(rows[-8:])),
    }


def chain_sentence(cfg: dict) -> tuple[str, str]:
    """把配置翻译成一句人话 + 语义等级。

    这句话是给用户看的「现在这条链路通不通」，所以顺序是：
    总开关 → 有没有通道 → 有没有勾事件 → 通道能不能到手机。
    """
    if not cfg.get("enabled", True):
        return "已静音，任何事件都不会发出通知", LEVEL_WARN

    on = [c.get("type") for c in config.enabled_channels(cfg) if c.get("type")]
    if not on:
        return "没有可用通道，通知发不出去", LEVEL_DANGER

    kinds = [k for k, v in (cfg.get("triggers") or {}).items() if v]
    if not kinds:
        return "没有勾选任何事件，不会触发通知", LEVEL_WARN
    pair = set(kinds)
    if {"task_done", "task_failed"} <= pair:
        when = "任务完成或失败时"
    elif pair == {"task_done"}:
        when = "任务完成时"
    elif pair == {"task_failed"}:
        when = "任务失败时"
    else:
        when = "、".join(events.KIND_LABELS.get(k, k) for k in kinds[:2])
        if len(kinds) > 2:
            when += f"等 {len(kinds)} 类事件时"

    # log 通道只写本地文件，不能算「送到手机」——混进这句话就是骗人
    remote = [t for t in on if t != "log"]
    if not remote:
        return "只写入本地日志，手机上收不到", LEVEL_WARN

    names = "、".join(channels.short_label(t) for t in remote[:2])
    if len(remote) > 2:
        names += f"等 {len(remote)} 条通道"
    return f"{when}，经{names}，送到你手机", LEVEL_OK


def channel_state(ctype: str, ch: dict) -> tuple[str, str, bool]:
    """通道列表行的 `(副标题, 语义等级, 是否点亮)`。

    等级的含义：
    - `ok`    已启用（点亮）
    - `warn`  配好了但没启用（不点亮，提醒「你可以打开它」）
    - `muted` 还缺字段（不点亮）
    """
    ch = ch or {}
    if ch.get("enabled"):
        return "已启用", LEVEL_OK, True
    miss = channels.missing_keys(ctype, ch)
    if not miss:
        return "已配置，未启用", LEVEL_WARN, False
    label = channels.FIELD_LABELS.get(ctype, {}).get(miss[0], miss[0])
    more = f" 等 {len(miss)} 项" if len(miss) > 1 else ""
    return f"待配置：缺 {label}{more}", LEVEL_MUTED, False


def pick_channel(cfg: dict) -> str:
    """默认展示哪个通道：优先「已启用且配好」，其次「配好但没启用」，最后回落 wxmp。"""
    for ch in cfg.get("channels", []):
        t = ch.get("type")
        if ch.get("enabled") and channels.is_ready(t, ch):
            return t
    for ch in cfg.get("channels", []):
        if channels.is_ready(ch.get("type"), ch):
            return ch["type"]
    return "wxmp"


def hooks_installed() -> bool:
    """WorkBuddy / CodeBuddy 的 hook 装没装。

    直接读宿主的 settings.json，不调 `selftest.py`——后者要起进程，
    在界面首屏上不值。
    """
    try:
        import json

        for path in (Path.home() / ".workbuddy-ai" / "settings.json",
                     Path.home() / ".codebuddy" / "settings.json"):
            if not path.exists():
                continue
            d = json.loads(path.read_text(encoding="utf-8"))
            if "hook.py" in json.dumps(d.get("hooks", {}), ensure_ascii=False):
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------- 表单 → config


def normalize_filters(raw: dict) -> dict:
    """把表单传来的字符串规整成 config 要的类型。

    - 三个列表项：逗号分隔字符串 → `list[str]`；已经是 list 就逐项去空白
    - `quiet_hours`：原样字符串（格式如 `23:00-08:00`，解析在 filters.py）
    - 布尔项：`bool()`
    - 数值项：`to_int`，失败回落 `DEFAULT_CONFIG` 里的默认值

    默认值一律从 `config.DEFAULT_CONFIG` 取，不在这里写死第二份。
    """
    defaults = config.DEFAULT_CONFIG["filters"]
    out: dict = {}
    for key, val in (raw or {}).items():
        default = defaults.get(key)
        if key in _LIST_KEYS:
            if isinstance(val, str):
                out[key] = split_list(val)
            else:
                out[key] = [str(x).strip() for x in (val or []) if str(x).strip()]
        elif key == "quiet_hours":
            out[key] = str(val or "").strip()
        elif isinstance(default, bool):
            out[key] = bool(val)
        elif isinstance(default, int):
            out[key] = to_int(val, default)
        else:
            out[key] = val
    return out


def _merge_channels(base: list, patch: list) -> list:
    """按 `type` 合并通道列表。

    **不能整条替换**：UI 打开期间 hook 进程可能改过 channels（比如自动启用），
    整条替换会把那些改动抹掉。所以逐个 type 对齐、只覆盖 patch 里出现的字段。
    """
    out = copy.deepcopy(base or [])
    index = {c.get("type"): i for i, c in enumerate(out) if c.get("type")}
    for item in patch or []:
        t = item.get("type")
        if not t:
            continue
        if t in index:
            out[index[t]].update(copy.deepcopy(item))
        else:
            out.append(copy.deepcopy(item))
            index[t] = len(out) - 1
    return out


def merge_save(patch: dict) -> dict:
    """**读-改-写**：重新 load，只覆盖 patch 里出现的字段，再存。

    外来方案第 5 条修正的落点。原来的 `on_save` 是「拿内存里的整份 cfg 覆盖」，
    在 webview 版里会丢掉 hook 进程在 UI 打开期间写进去的改动。

    返回 `{"ok": True, "turned_on": [...], "turned_off": [...], "cfg": {...}}`
    或 `{"ok": False, "error": "..."}`。

    必须调 `channels.auto_enable` —— 用户填齐凭证后自动启用，这是现有行为。
    """
    try:
        cfg = config.load()
        merged = config.deep_merge(cfg, {
            k: v for k, v in (patch or {}).items() if k != "channels"
        })
        if "channels" in (patch or {}):
            merged["channels"] = _merge_channels(
                cfg.get("channels", []), patch["channels"])
        turned_on, turned_off = channels.auto_enable(merged)
        config.save(merged)
        return {
            "ok": True,
            "turned_on": list(turned_on),
            "turned_off": list(turned_off),
            "cfg": merged,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def reset_channel(cfg: dict, ctype: str) -> dict:
    """把一个通道恢复到磁盘上的那份（丢弃未保存编辑）。

    从磁盘重读而不是从入参 `cfg` 里取：入参可能是用户改了一半的内存副本，
    而「重置」的语义是「回到上次保存的样子」。磁盘读不到就回落入参里那份。
    """
    try:
        disk = config.get_channel(config.load(), ctype)
    except Exception:
        disk = None
    if disk is None:
        disk = config.get_channel(cfg or {}, ctype)
    return copy.deepcopy(disk or {})


# ---------------------------------------------------------------- 会阻塞的动作


def test_channel(cfg: dict, ctype: str) -> tuple[bool, str]:
    """真发一条测试消息，返回 `(成功, 说明)`。

    **阻塞，调用方负责开线程。**

    发送前临时把该通道 `enabled` 置真、发完恢复：用户点「测试此通道」的
    意图就是「现在就试一次」，不应该因为开关没拨而失败。
    """
    ch = config.get_channel(cfg, ctype)
    if not ch:
        return False, "通道不存在"
    was = ch.get("enabled")
    ch["enabled"] = True
    try:
        results = pipeline.send_test(cfg, [ctype])
    except Exception as e:
        return False, f"发送异常：{e}"
    finally:
        ch["enabled"] = was
    if not results:
        return False, "没有可用的目标通道"
    r = results[0]
    return bool(r.get("ok")), str(r.get("msg", "") or "")


def fetch_openid(fields: dict) -> tuple[list[str], str]:
    """微信测试号拿关注者 openid，返回 `(ids, 错误说明)`。

    **阻塞，调用方负责开线程。** 成功时错误说明为空串。
    """
    appid = str((fields or {}).get("appid") or "").strip()
    secret = str((fields or {}).get("secret") or "").strip()
    if not appid or not secret:
        return [], "先填 appID 和 appsecret"
    try:
        ids, err = channels.wx_followers(appid, secret)
    except Exception as e:
        return [], str(e)
    return list(ids or []), str(err or "")


def open_path(target: str) -> tuple[bool, str]:
    """用系统默认程序打开目录 / 文件。返回 `(成功, 错误说明)`。"""
    try:
        if sys.platform == "win32":
            os.startfile(target)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", target])
        return True, ""
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------- 模板预览


def preview(cfg: dict, tpl_kind: str, title: str, content: str,
            sample: dict | None = None) -> dict:
    """渲染模板预览。

    返回 `{"title", "body", "vars_used", "vars_missing"}`。

    - `body` 已经过 `channels.plain_text`：微信模板消息是纯文本，
      发出去前会去掉 markdown 记号，预览必须和实际发出去的一致。
    - `vars_used`：标题与正文里出现的占位符（按 `render.PLACEHOLDERS` 顺序）
    - `vars_missing`：渲染后仍残留的 `{xxx}`——说明模板写了不存在的变量，
      发出去会是空白，得让用户看见
    """
    snapshot = copy.deepcopy(cfg or {})
    snapshot.setdefault("titles", {})[tpl_kind] = title
    snapshot.setdefault("templates", {})[tpl_kind] = content
    app = snapshot.get("app_name", "WorkBuddy")
    fields: dict = {}
    try:
        rendered_title, rendered_body, fields = render.render_message(
            sample or SAMPLE_PAYLOAD, tpl_kind, snapshot, app)
    except Exception:
        rendered_title, rendered_body = "（模板有误）", ""
    body = channels.plain_text("", rendered_body)

    blob = f"{title or ''}\n{content or ''}"
    used = [tok for tok, _desc in render.PLACEHOLDERS if tok in blob]
    # 未知占位符在渲染阶段就被替换成空字符串了，所以要在**模板原文**里找，
    # 再和实际可用的字段表比对——差集就是「写了但不存在的变量」。
    declared = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", blob))
    missing = [f"{{{x}}}" for x in sorted(declared - set(fields))]
    return {
        "title": rendered_title,
        "body": body,
        "vars_used": used,
        "vars_missing": missing,
    }
