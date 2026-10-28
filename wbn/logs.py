"""日志读取层：全项目唯一读 `notify-*.log` 的地方。

职责边界：**只做文本 → 结构**。什么算成功、要不要降级、显示什么文案，
全部属于 `insight`，这里一概不管。

为什么单独一层：W3 到 W3.7 七轮修复全部落在这个边界上，因为解析逻辑
分散在 `insight.stats()` 和 `bridge._last_send_times()` 两处、用三份正则、
各自决定读几天。每加一个消费者就暴露一次不一致——W3.6 的
「`ok`/`fail` 跨天累加」就是两份实现各有一套时间口径的直接后果。

**这一层不知道「log 通道不算投递」**，也**不知道测试该不该算**。`deliveries_of`
只接受 `exclude` / `include_tests` 两个**纯参数**，口径由调用方决定。

对外契约
    read()        ← 生产代码只许用这个
    Facts.*       ← 生产代码只许用这些
    parse_* / is_test_line / split_emoji
                  ← 只给 logs 内部和旧测试用。生产代码直接用它们
                    等于绕过 Facts 自己解析，那就是这一层要消灭的东西。

**本模块不许 import 任何业务模块**（R30）：通道注册表属于业务层，依赖它就把
方向反了（底层文本层指向业务层），而且它将来只要用到日志就成环。排除谁由
调用方传 `frozenset` 进来。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import config, sanitize

# ---------------------------------------------------------------- 正则

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
# `title=` 是最后一个字段，可能含空格（`✅ WorkBuddy 任务完成`），所以用 `(.*)`
# 收尾；老行没有 `title=`，那一段可选。字段全部锚定位置，不整行找子串。
_EVENT_LINE = re.compile(
    r"^\[(?P<time>\d{2}:\d{2}:\d{2})\] event=(?P<event>\S+) kind=(?P<kind>\S+)"
    r" sent=(?P<sent>True|False) remote_ok=(?P<remote_ok>True|False)"
    r" elapsed=\S+(?: title=(?P<title>.*))?$")

# `notify-*.log` 里的通道投递行：
#   [10:40:04] channel=wxmp ok=False msg=errcode=40037 模板 ID 无效
#   [01:26:32] test-send type=webhook ok=False msg=TimeoutError: timed out
#   [01:26:32] test channel=log ok=True msg=已记录        ← W3.1 之前的历史写法
#
# **连 `ok=` 一起锚定**，不留给 `"ok=True" in line` 这种子串判断：
# `msg=` 是通道返回的原文（不可信），里面出现 `ok=False` 或 `channel=log`
# 完全可能，整行找子串会把一次成功数成失败、把一次真实失败当成 log 排掉。
# 四轮统计 bug 全部出在「整行找子串」，留一处就还有下一次。
#
# 两种字段名都认：写入侧 W3.1 起用 `type=`（`test-send type=` 不含 `channel=`
# 子串），历史行是 `channel=`。**只认一种会让「最后发送时间」漏掉另一种。**
_DELIVERY_LINE = re.compile(
    r"^\[(?P<time>\d{2}:\d{2}:\d{2})\] (?P<test>test-send |test )?"
    r"(?:channel|type)=(?P<channel>\S+) ok=(?P<ok>True|False)"
    r"(?: msg=(?P<msg>.*))?$")


# ---------------------------------------------------------------- 结构


@dataclass(frozen=True)
class Delivery:
    """一次投递尝试（含手动「测试此通道」）。"""

    time: str        # "08:00"
    day: str         # "today" | "yesterday"
    channel: str
    ok: bool
    msg: str         # 已 redact，已截断 120 字
    test: bool


@dataclass(frozen=True)
class Event:
    """一条事件汇总行（`pipeline.notify()` 写的）。"""

    time: str
    day: str
    event: str
    kind: str
    sent: bool
    remote_ok: bool
    title: str       # 已剥掉开头 emoji，可能为空（老行没有 title=）


@dataclass(frozen=True)
class ChannelFacts:
    """一条通道的**两件事实**（D19）。

    `time` / `day` / `test` 来自最近一条记录（**含**测试）；
    `ok` / `msg` / `ok_day` 来自最近一条**非测试**记录。
    """

    time: str
    day: str
    test: bool
    ok: bool | None            # None = 真实投递一次都没发生过
    msg: str
    ok_day: str | None


@dataclass(frozen=True)
class Facts:
    """两天的日志，解析完的形态。**只由文件内容决定**，不含任何业务判断。"""

    events: tuple[Event, ...]         # 时间升序
    deliveries: tuple[Delivery, ...]  # 时间升序

    # ---- 四个方法就是全部口径的定义处，每个口径只写一次 ----

    def events_of(self, day: str) -> tuple[Event, ...]:
        """某一天的全部事件（`day` 取 `"today"` / `"yesterday"`）。

        「`today` 只数今天」这条口径由调用方传 `"today"` 表达，这里不做默认。
        """
        return tuple(e for e in self.events if e.day == day)

    def deliveries_of(
        self, day: str, *, exclude: frozenset[str] = frozenset(),
        include_tests: bool = True,
    ) -> tuple[Delivery, ...]:
        """某一天的投递尝试。

        `exclude` / `include_tests` 都是**纯参数**——这一层不知道哪个通道是
        「本机落盘」，也不知道手动测试该不该算一次投递，那是调用方的口径。

        「统计口径下的一次投递」= `exclude=<通道注册表的 LOCAL_CHANNELS>` +
        `include_tests=False`，由 `insight.stats()` 传进来（D14）。
        这里只负责把 `channel=log` 如实读成一条投递——**排除是调用方的决定**。
        """
        out = [d for d in self.deliveries if d.day == day]
        if exclude:
            out = [d for d in out if d.channel not in exclude]
        if not include_tests:
            out = [d for d in out if not d.test]
        return tuple(out)

    def last_by_channel(self) -> dict[str, ChannelFacts]:
        """每条通道的两件事实（D19）。

        时间轴用**最近一条**记录（含测试）——用户刚点完测试，列表显示
        「刚刚」是对的；成败用最近一条**非测试**记录——一次故意的试探
        不该把侧栏永久打成红。

        只有测试记录时 `ok=None`（真实投递一次都没发生过），
        既不是成功也不是失败。
        """
        recent: dict[str, Delivery] = {}
        real: dict[str, Delivery] = {}
        for d in self.deliveries:
            recent[d.channel] = d
            if not d.test:
                real[d.channel] = d

        out: dict[str, ChannelFacts] = {}
        for ctype, r in recent.items():
            truth = real.get(ctype)
            out[ctype] = ChannelFacts(
                time=r.time, day=r.day, test=r.test,
                ok=truth.ok if truth else None,
                msg=truth.msg if truth else "",
                ok_day=truth.day if truth else None,
            )
        return out

    def recent(self, limit: int = 8) -> tuple[Event, ...]:
        """「最近通知」：**送到手机**的事件，最新在前，最多 `limit` 条。

        「通知」在这个项目里已经被 D14 定义成「到手机了」（`remote_ok`），
        列表里塞一条从没到过手机的条目，就是 W3.3 修掉的同一个病。
        读两天：跨零点后不能空，昨晚明明发过。
        """
        got = [e for e in self.events if e.remote_ok]
        return tuple(reversed(got[-limit:])) if limit > 0 else ()


# ---------------------------------------------------------------- 解析


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


def is_test_line(line: str) -> bool:
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


def parse_event(line: str, day: str = "") -> Event | None:
    """一行 → `Event`；不是事件汇总行就返回 `None`。

    `day` 由调用方给（这一层不知道读的是哪个文件），默认空串。
    """
    m = _EVENT_LINE.match(line)
    if not m:
        return None
    _emoji, plain = split_emoji(m.group("title") or "")
    return Event(
        time=m.group("time"),
        day=day,
        event=m.group("event"),
        kind=m.group("kind"),
        sent=m.group("sent") == "True",
        remote_ok=m.group("remote_ok") == "True",
        title=plain,
    )


def parse_delivery(line: str, day: str = "") -> Delivery | None:
    """一行 → `Delivery`；不是通道投递行就返回 `None`。

    `msg` 在这里就 `redact`：它是远端返回的原文，可能回显带 key / token 的
    完整 URL。截断留给展示层，顺序必须是**先脱敏再截断**——反过来会把
    敏感串的前半段留在界面上。
    """
    m = _DELIVERY_LINE.match(line)
    if not m:
        return None
    return Delivery(
        time=m.group("time")[:5],             # 只留 HH:MM，秒没用
        day=day,
        channel=m.group("channel"),
        ok=m.group("ok") == "True",
        msg=sanitize.redact(m.group("msg") or "", 120),
        test=bool(m.group("test")),
    )


# ---------------------------------------------------------------- 入口


def read(days: int = 2) -> Facts:
    """读最近 `days` 天的日志。文件不存在就当那天没有数据，不抛。

    `days=2` 读昨天 + 今天，顺序是**昨天先、今天后**——同一条通道今天的
    记录自然覆盖昨天的（`last_by_channel` 靠这个顺序取「最近一条」）。

    只支持 1 / 2 天：`day` 字段的取值域是 `"today" | "yesterday"`，
    更早的记录没有消费者。
    """
    if days not in (1, 2):
        raise ValueError(f"只支持 1 或 2 天，收到 {days!r}")

    now = datetime.now()
    events: list[Event] = []
    deliveries: list[Delivery] = []
    for offset in range(days - 1, -1, -1):
        tag = "today" if offset == 0 else "yesterday"
        d = now - timedelta(days=offset)
        path = config.LOG_DIR / f"notify-{d:%Y-%m-%d}.log"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            ev = parse_event(line, tag)
            if ev is not None:
                events.append(ev)
                continue
            dl = parse_delivery(line, tag)
            if dl is not None:
                deliveries.append(dl)
    return Facts(events=tuple(events), deliveries=tuple(deliveries))
