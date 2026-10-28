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
from datetime import datetime, timedelta
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

# `notify-*.log` 里的**事件汇总行**，`pipeline.notify()` 每条事件写一行：
#   [10:24:23] event=Stop kind=task_done sent=True remote_ok=True \
#              elapsed=1.17s title=✅ WorkBuddy 任务完成
#
# 这一行同时供 `today` 和 `recent`（W3.5）。**一个正则，两个用途**——
# 以前 `recent` 读 `notifications.log`（那是 `log` 通道的副产物，通道一关
# 就永久为空），于是同一个界面上的两个数来自两个不同的宇宙。
#
# `title=` 是最后一个字段，可能含空格（`✅ WorkBuddy 任务完成`），所以用 `(.*)`
# 收尾；老行没有 `title=`，那一段可选。字段全部锚定位置，不整行找子串。
_EVENT_LINE = re.compile(
    r"^\[(?P<time>\d{2}:\d{2}:\d{2})\] event=(?P<event>\S+) kind=(?P<kind>\S+)"
    r" sent=(?P<sent>True|False) remote_ok=(?P<remote_ok>True|False)"
    r" elapsed=\S+(?: title=(?P<title>.*))?$")

# `kind` 里属于「这次任务不顺」的那几种，最近通知用红点标出来。
# 注意这跟 `remote_ok` 是两件事：一条「任务失败」的通知**送到了**也是红点，
# 因为红点说的是「这件事本身是坏消息」，不是「没发出去」。
_BAD_KINDS = ("task_failed", "tool_failed")

# `notify-*.log` 里的通道投递行：`[10:40:04] channel=wxmp ok=False msg=…`。
# **连 `ok=` 一起锚定**，不留给 `"ok=True" in line` 这种子串判断：
# `msg=` 是通道返回的原文（不可信），里面出现 `ok=False` 完全可能，
# 整行找子串会把一次成功数成失败。四轮统计 bug 全部出在「整行找子串」，
# 留一处就还有下一次。
# 写入格式固定（`channels.py` 的 `send_all`），`ok=` 后面永远是 True/False。
_CHANNEL_LINE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2})\] channel=(\S+) ok=(True|False)")

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


def _is_delivered(line: str) -> bool:
    """`pipeline.notify()` 写的「这条真的到手机了」汇总行。

    形如 `[10:24:23] event=Stop kind=task_done sent=True remote_ok=True …`。

    判据是**整行按位置匹配 `_EVENT_LINE`**，再要求 `remote_ok=True`：

    - `skip event=…`（被过滤链拦下）第一段是 `skip`，匹配不上；
    - `test-send type=…` 第一段是 `test-send`，手动测试不算通知；
    - `sent=False` / `remote_ok=False` 都不算。

    **为什么看 `remote_ok` 而不是 `sent`**（D14）：`sent` 是 `any_ok`，
    把 `log` 通道也算作成功——于是「只写进了本地文件」也会被数成一条通知。
    `remote_ok` 是 `pipeline.py` 里 `t not in ("log",)` 那一步算出来的，
    语义就是「至少一个远端通道成功」。`log` 是调试落盘，不是投递目标。

    判据只此一份：`stats()` 里数 `today` 用的是同一个 `_EVENT_LINE` 的匹配结果，
    不是另写一遍 `"remote_ok=True" in line`。子串判据是前四轮 bug 的共同形状。
    """
    m = _EVENT_LINE.match(line)
    return bool(m) and m.group("remote_ok") == "True"


def _recent_row(m: re.Match, day: str) -> dict:
    """一条事件汇总行 → 「最近通知」里的一行。"""
    kind = m.group("kind")
    raw = m.group("title") or ""
    _emoji, plain = split_emoji(raw)
    return {
        "time": m.group("time"),
        # `day` 让前端能区分「今天」和「昨天」。没有它，昨天 23:50 和今天
        # 00:10 在界面上都是 `23:50` / `00:10`，用户会以为都是今天的。
        "day": day,
        "channel": "",
        # 标题空（老行没有 `title=` 字段）就退回事件名，不显示空白行
        "summary": (plain or events.KIND_LABELS.get(kind) or kind)[:52],
        # 红点说的是「这次任务本身不顺」，不是「没发出去」——后者由 `today`
        # 的计数体现。两者混用会让一条送达了的「任务失败」通知变成绿点。
        "ok": kind not in _BAD_KINDS,
    }


def stats(cfg: dict) -> dict:
    """今日统计 + 最近 8 条通知。

    返回 `{today, ok, fail, channels, recent: [{time, day, channel, summary, ok}]}`。

    **三个数只算远端通道**（D14）。`log` 通道是调试落盘，不是投递目标：
    它既不该让 `today` +1，也不该让 `ok` +1，否则屏幕上会出现
    「16 条通知 / 16 次成功 / 14 次失败」——三个数不在同一个宇宙里。

    **一个数据源**（W3.5）：`notify-*.log`。它由 `pipeline.notify()` 和
    `channels.send_all()` 写，**和通道开关无关**——通道全关也照写。

    - `today` / `recent` 读同一种行：`pipeline` 写的事件汇总行
      `event=… remote_ok=… title=…`。`recent` 曾经读 `notifications.log`，
      而那个文件全项目只有 `channels._send_log` 写——用户关掉「写入本地日志」
      通道，「最近通知」就永久为空，空态还写「还没收到通知」。同一个界面上
      两个数来自两个宇宙，这是 W3.2 修 `today` 时漏掉的另一半。
    - `ok` / `fail` 读通道投递行 `channel=… ok=…`。
    - `notifications.log` **不再喂任何数字**，只作为正文存档给人翻。

    四处判据，都是踩过的坑：

    - **时间范围分两种，别混**（D18）：`today` / `ok` / `fail` **只数今天**——
      它们在同一张「今日」卡上，混进昨天的投递就会读出「今日通知 2 / 通道失败 66」
      这种没有结论的数字。`recent` 读今天 + 昨天，跨零点不能空。
      「最近一次」是另一件事，见 `bridge._last_send_times()`。
    - `today` 看 `event=… remote_ok=True`（见 `_is_delivered`）。
    - `ok` / `fail` **一个子串判断都不用**：`_CHANNEL_LINE` 把通道名和
      `ok=(True|False)` 一起锚定在时间戳右侧，取 `m.group(2)` / `m.group(3)`。
      `msg=` 是通道返回的原文，里面出现 `channel=log` 或 `ok=False` 都可能，
      整行找子串会把一次真实结果读反（前四轮 bug 全是这个形状）。
      匹配不上的行一律跳过——每个事件后面那行
      `event=… remote_ok=False` 就不会被数成一次失败（否则失败数翻倍，
      19 显示成 38）。
    - 以 `test ` / `test-send ` 开头的行是手动点「测试此通道」产生的，
      不算投递，跳过；历史行**不删**（复盘要看）。
    - `recent` 只收 `remote_ok=True` 的事件：「通知」在这个项目里已经被 D14
      定义成「到手机了」，列表里塞一条从没到过手机的条目，就是 W3.3 修掉的
      同一个病。没送到的事件在「通道失败 N」和通道页里能看到原因。
      标题开头的 ✅/⚠️ 要剥掉——成败已经由 `ok` 字段表达，emoji 是重复信号，
      而且 Canvas 字体没有 emoji 字形。
    """
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    delivered, ok, fail = 0, 0, 0
    rows: list[dict] = []

    # 今天 + 昨天。`recent` 是「最近通知」，本来就该跨天；只读今天的话，
    # 刚过零点打开程序会看到一片空白，而昨晚明明发过 20 条。
    for day, tag in ((yesterday, "yesterday"), (today, "today")):
        lp = config.LOG_DIR / f"notify-{day}.log"
        if not lp.exists():
            continue
        try:
            text = lp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            em = _EVENT_LINE.match(line)
            if em:
                if em.group("remote_ok") == "True":
                    if tag == "today":
                        delivered += 1
                    rows.append(_recent_row(em, tag))
                continue
            if _is_test_line(line):
                continue
            m = _CHANNEL_LINE.match(line)
            if not m or m.group(2) == "log":
                continue
            # 通道成功/失败是「今日」卡上的数，昨天的投递不是今天的成绩（D18）。
            # **位置不能提前**：放到 `_EVENT_LINE` 判断之前会把 `recent` 也断掉，
            # 而 `recent` 就是要读两天。只截通道行，不动事件行。
            if tag != "today":
                continue
            if m.group(3) == "True":
                ok += 1
            else:
                fail += 1

    return {
        "today": delivered, "ok": ok, "fail": fail,
        "channels": len(config.enabled_channels(cfg)),
        # 追加写的日志，最后一行最新；先截 8 条再倒序
        "recent": list(reversed(rows[-8:])),
    }


def delivery_health(remote: list[str], last: dict | None) -> str:
    """最近一次远端投递的整体结果，`"none" | "ok" | "partial" | "fail"`。

    `last` 形如 `{ctype: {"time": "HH:MM", "ok": bool, "msg": str, "test": bool}}`
    （`bridge._last_send_times()` 的返回值）。

    **「没有记录」必须是 `none`，不能算成失败。** 首次启动、日志被清空、
    今天还没发过——这些情况都该维持现状（`ok`）。把它们判成 `warn`/`danger`
    就是拿一个不存在的失败去吓用户，比不显示更糟。

    只统计**有记录**的远端通道：某个通道今天一次都没试过，就没资格参与
    这次判定，不该把它算成「失败」拉低整句话。

    **测试发送不算投递**（W3.5）。理由不是洁癖，是测试用的是**表单里还没
    保存的值**：用户想看看报错长什么样，故意填个错地址点一下「测试此通道」，
    侧栏从此永久变红——而磁盘上的配置可能是对的，要等下一次真实发送成功
    才恢复。D14 也已经把「测试」排除在投递之外，这里跟着同一条口径。

    **和 `time` 故意不对称**：通道列表的「最后发送时间」仍然认测试行
    （用户刚点完测试，列表显示刚刚是对的）。同一条记录，一个字段用来回答
    「什么时候试过」，另一个用来回答「有没有送到」——问的不是一件事，
    所以判定必须分开。这是全项目第三处刻意的不对称，改之前先读这段。
    """
    recs = [last[t] for t in remote
            if last and t in last and not last[t].get("test")]
    if not recs:
        return "none"
    good = sum(1 for r in recs if r.get("ok"))
    if good == len(recs):
        return "ok"
    return "fail" if good == 0 else "partial"


def chain_sentence(cfg: dict, last: dict | None = None) -> tuple[str, str]:
    """把配置翻译成一句人话 + 语义等级。

    这句话是给用户看的「现在这条链路通不通」，所以顺序是：
    总开关 → 有没有通道 → 有没有勾事件 → 通道能不能到手机 → 最近一次到没到。

    最后一环是 W3.4 加的：只读配置的话，「送到你手机」是**承诺**不是**状态**，
    微信连续失败十几次它照样说这句、照样亮绿点，界面等于在替失败背书。
    `last` 传进来才判，**不传就维持旧行为**（tkinter 版和测试都还这么调）。
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

    # 后缀只允许 4–6 个字：侧栏 240px，`--fs-aux` 一行约 16 个汉字。
    # 失败原因（errcode / 异常名）**不许**塞进来，那些去通道页看。
    health = delivery_health(remote, last)
    if health == "fail":
        return f"{when}，经{names}，但最近一次没送到", LEVEL_DANGER
    if health == "partial":
        return f"{when}，经{names}，最近一次部分没送到", LEVEL_WARN
    return f"{when}，经{names}，送到你手机", LEVEL_OK


def channel_state(
    ctype: str, ch: dict, last: dict | None = None
) -> tuple[str, str, bool]:
    """通道列表行的 `(副标题, 语义等级, 是否点亮)`。

    等级的含义：
    - `ok`    已启用，且最近一次没出问题（或今天还没发过）
    - `warn`  配好了但没启用（不点亮，提醒「你可以打开它」）
    - `danger` 已启用，但最近一次投递失败（点亮，红色）
    - `muted` 还缺字段（不点亮）

    `last` 是这条通道今天的最后一条投递记录
    `{"time": "HH:MM", "ok": bool, "msg": str}`，没有记录就传 `None`。

    **配置问题优先于运行时问题**：字段没填完连试都试不了，说「最近一次失败」
    只会让人去查网络。所以顺序是「缺字段 → 运行时失败 → 正常」。

    `msg` 是远端返回的原文，进这里之前必须已经 `redact` 过（bridge 负责）；
    这里只做截断——列表行放不下 120 个字，也不该让一条报错撑破布局。
    """
    ch = ch or {}
    # 缺字段排第一：填都没填完，谈「最近一次失败」是误导。`enabled` 可能是
    # 上一版配置留下的 True，也可能被手改 config.json 改成 True——那时字段
    # 照样不全，照样发不出去，先让用户把字段补上。
    miss = channels.missing_keys(ctype, ch)
    if miss:
        label = channels.FIELD_LABELS.get(ctype, {}).get(miss[0], miss[0])
        more = f" 等 {len(miss)} 项" if len(miss) > 1 else ""
        return f"待配置：缺 {label}{more}", LEVEL_MUTED, False
    if ch.get("enabled"):
        if last and not last.get("ok"):
            why = (last.get("msg") or "").strip()
            if why:
                return f"最近一次失败：{why[:20]}", LEVEL_DANGER, True
            return "最近一次失败", LEVEL_DANGER, True
        return "已启用", LEVEL_OK, True
    return "已配置，未启用", LEVEL_WARN, False


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
