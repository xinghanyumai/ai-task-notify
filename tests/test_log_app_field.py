"""事件行的 `app=` 字段（W7.7 / R63）。

**这一轮唯一的真风险是向后兼容**：`logs/` 里上千行 W7.7 之前的行没有 `app=`。
正则一旦把 `app` 写成必需段，历史行全部解析失败——而症状是「状态页今日通知数
变成 0」，界面上没有任何报错。所以第一条测试就是拿一行**手写的老格式**喂进去。

另一个容易踩的：`app` 必须排在 `title` **之前**。`title` 是自由文本，用户完全
可能在自己的任务标题里写「加 app= 字段」——那串字会跑到 `title=` 后面。位置
写反了，`\\S+` 会把它当成 app 吃掉，后面的 title 跟着错位。

    pytest tests/test_log_app_field.py -q
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import install  # noqa: E402  （ROOT 进 sys.path 之后才能 import）
from wbn import config, logs, pipeline  # noqa: E402

# 两行都是**手写**的，不是从文件里抄的：抄的那份会随着日志轮转消失，
# 而这几行要长期当「老格式/新格式长什么样」的标本。
OLD_LINE = (
    "[19:11:25] event=Stop kind=task_done sent=False remote_ok=False "
    "elapsed=6.02s title=✅ WorkBuddy 任务完成"
)
NEW_LINE = (
    "[19:11:25] event=Stop kind=task_done sent=True remote_ok=True "
    "elapsed=6.02s app=WorkBuddy title=✅ WorkBuddy 任务完成"
)


def _log_only_cfg() -> dict:
    cfg = config.load()
    cfg["channels"] = [{"type": "log", "enabled": True}]
    cfg["filters"]["min_duration_seconds"] = 0
    cfg["filters"]["dedup_seconds"] = 0
    cfg["triggers"]["task_done"] = True
    return cfg


# ---------------------------------------------------------------- 向后兼容


def test_old_format_line_still_parses():
    """老格式（无 `app=`）必须照旧解析成功，`app` 是空串。

    这条红了就说明「状态页今日通知数」在生产上会归零——而它是 0 的时候，
    用户只会以为「今天没通知」，不会想到是解析器坏了。
    """
    ev = logs.parse_event(OLD_LINE)
    assert ev is not None, "老格式行解析失败了——正则把 app 写成必需段了？"
    assert ev.app == "", f"老行没有 app=，应当是空串，实际 {ev.app!r}"
    assert ev.remote_ok is False
    assert ev.sent is False
    assert ev.title == "WorkBuddy 任务完成"      # emoji 已剥掉


def test_old_format_without_title_still_parses():
    """最早的行连 `title=` 都没有（W7 之前）。两段都可选，不能互相牵连。"""
    ev = logs.parse_event(
        "[10:24:23] event=Stop kind=task_done sent=True remote_ok=True elapsed=1.17s")
    assert ev is not None
    assert ev.app == ""
    assert ev.title == ""


def test_new_format_line_carries_app():
    ev = logs.parse_event(NEW_LINE)
    assert ev is not None
    assert ev.app == "WorkBuddy"
    assert ev.title == "WorkBuddy 任务完成"


def test_app_sits_before_title_so_title_may_contain_app():
    """`title` 里出现 `app=` 这串字时，不能被当成字段。

    用户完全可能把任务标题写成「日志里加 app= 字段」——这正是本轮任务本身。
    所以 app 必须锚在 title 之前，靠 `(.*)` 把后面整段吞掉。
    """
    line = ("[19:11:25] event=Stop kind=task_done sent=True remote_ok=True "
            "elapsed=6.02s app=CodeBuddy title=在日志里加 app= 字段，别截断")
    ev = logs.parse_event(line)
    assert ev is not None
    assert ev.app == "CodeBuddy"
    assert "app=" in ev.title and "别截断" in ev.title


def test_event_has_default_app():
    """`Event` 的 `app` 必须有默认值：否则所有既有构造点（含测试）全部报错。"""
    ev = logs.Event(time="08:00", day="today", event="Stop", kind="task_done",
                    sent=True, remote_ok=True, title="x")
    assert ev.app == ""


def test_delivery_regex_is_not_touched():
    """`_DELIVERY_LINE` 是通道投递行的正则，这轮**只碰事件行**。

    往投递行里加 `app=` 会让 `msg=` 后面的原文把整行搞错位，而投递行是
    「最后发送时间 / 侧栏红点」的唯一来源。
    """
    assert "app" not in logs._DELIVERY_LINE.pattern


# ---------------------------------------------------------------- 写入侧


@pytest.mark.usefixtures("no_network")
def test_pipeline_writes_app_before_title(monkeypatch):
    """写出来的行要能被自己的正则读回去，且 `app` 在 `title` 之前。

    只断言字符串包含 `app=WorkBuddy` 不够：位置错了（放到 title 后面）
    字符串照样包含。所以这里做**往返**——写一行、再解析回来。
    """
    lines: list[str] = []
    monkeypatch.setattr(pipeline.store, "log", lambda m: lines.append(m))

    payload = {
        "hook_event_name": "Stop",
        "session_id": "w77-app",
        "cwd": "D:/Code/demo",
        "last_assistant_message": "改好了，测试全部通过。",
    }
    pipeline.process(payload, "WorkBuddy", _log_only_cfg(), ignore_limits=True)

    written = [ln for ln in lines if ln.startswith("event=")]
    assert written, f"没写出事件行，实际写了：{lines}"
    line = written[-1]

    assert line.index("app=WorkBuddy") < line.index("title="), (
        f"app 必须排在 title 之前，实际：{line}")

    ev = logs.parse_event(f"[00:00:00] {line}")
    assert ev is not None, f"自己写的行自己解析不了：{line}"
    assert ev.app == "WorkBuddy"
    assert ev.title                     # title 没被 app 吃掉


@pytest.mark.usefixtures("no_network")
def test_skip_line_carries_app(monkeypatch):
    """被过滤链拦下的行也要带 `app=`——排查时最需要它的正是这种行。"""
    lines: list[str] = []
    monkeypatch.setattr(pipeline.store, "log", lambda m: lines.append(m))

    cfg = _log_only_cfg()
    cfg["triggers"]["task_done"] = False          # 一定被触发规则拦下
    payload = {
        "hook_event_name": "Stop",
        "session_id": "w77-skip",
        "cwd": "D:/Code/demo",
        "last_assistant_message": "改好了。",
    }
    pipeline.process(payload, "CodeBuddy", cfg, ignore_limits=True)

    skip = [ln for ln in lines if ln.startswith("skip ")]
    assert skip, f"没写出 skip 行：{lines}"
    assert "app=CodeBuddy" in skip[-1], f"skip 行没有 app=：{skip[-1]}"


# ---------------------------------------------------------------- app 值约束


def test_install_app_names_have_no_space():
    """写进 hook command 的 app 名不许含空格。

    日志正则用 `\\S+` 抓 app：带空格的 app 名会让那一行在空格处截断，
    `title` 跟着错位（`WorkBuddy Beta` 会变成 app=WorkBuddy + 脏 title）。
    现在的三个名字都没有空格，这条守着「以后也别加」。
    """
    names = [app for app, _path in install.TARGETS] + [install.LEGACY_TARGET[0]]
    bad = [n for n in names if " " in n or not n]
    assert not bad, f"这些 app 名含空格或为空，会把日志行截断：{bad}"


def test_hook_and_dispatch_default_app_to_non_empty():
    """`\\S+` 成立的前提：两条入口都把 app 兜底成非空。

    这是源码级守卫——删掉 hook.py 的默认值或 dispatch.py 的 `or "WorkBuddy"`，
    `--app ""` 就会写出 `app= title=…`，那一行**整行匹配失败**（不是 app 为空），
    事件静默消失。
    """
    hook = (ROOT / "hook.py").read_text(encoding="utf-8")
    dispatch = (ROOT / "dispatch.py").read_text(encoding="utf-8")
    assert re.search(r'app\s*=\s*"WorkBuddy"', hook), "hook.py 里 app 的默认值没了"
    assert re.search(r'app\s*=\s*str\(.*or\s*"WorkBuddy"\)', dispatch), \
        "dispatch.py 里 app 的空值兜底没了"
