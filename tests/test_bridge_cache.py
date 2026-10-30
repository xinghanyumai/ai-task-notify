"""一次页面加载只读一遍（W3.8c）。

状态页加载会连调三个 bridge 方法（`stats_today` / `chain_sentence` /
`channels_list`），每个都要「今天 + 昨天的投递记录」。改之前各读一次文件：

```
stats_today()    → insight.stats(cfg)      → logs.read(2)   ← 读
chain_sentence() → _last_send_times()      → logs.read(2)   ← 读
channels_list()  → _last_send_times()      → logs.read(2)   ← 读
```

文件只有几十 KB，性能不是问题。**问题是三次读之间如果有写入**（后台正好投递
了一条），三个出口看到的数据就不一样——同一屏上「今日通知 2 / 通道失败 3」，
和 W3.7 那个「侧栏绿、列表红」同类，只是触发条件更隐蔽：要恰好赶上后台投递。

所以这条测的不是性能，是**一致性**：一次加载 = 一份 `Facts`。

## 缓存为什么按 mtime 而不按时间

「缓存 5 秒」能过第 1 条，会在第 2 条红：用户点完「测试此通道」立刻刷新，
测试刚写了一行日志，mtime 变了，但 5 秒没到 —— 读到的还是旧数据，界面上
「测试成功」和「最近一次失败」同时出现。

**时间是猜的，mtime 是事实。**
"""

from __future__ import annotations

import itertools
import os
from datetime import datetime, timedelta

import pytest

from wbn import bridge, config, insight, logs

# `_bump_mtime` 的递增计数器，见那个函数的说明
_bump = itertools.count(1)

_OK_LINE = ("[09:00:00] event=Stop kind=task_done sent=True remote_ok=True"
            " elapsed=1.17s title=✅ WorkBuddy 任务完成")
_BAD_LINE = "[09:00:00] channel=wxmp ok=False msg=errcode=40037 模板 ID 无效"


@pytest.fixture
def counter(monkeypatch):
    """包一层 `logs.read` 数调用次数。

    数的是 `logs.read`——**不是** `insight.stats` 或 `_last_send_times`。
    那三个出口随便谁自己开文件都会让计数涨，这正是要抓的东西。
    """
    calls = {"n": 0}
    real = logs.read

    def counting(days: int = 2):
        calls["n"] += 1
        return real(days=days)

    monkeypatch.setattr(logs, "read", counting, raising=True)
    return calls


def _today_file(tmp_path):
    d = datetime.now()
    return tmp_path / "logs" / f"notify-{d:%Y-%m-%d}.log"


def _append(tmp_path, line: str) -> None:
    p = _today_file(tmp_path)
    with p.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    _bump_mtime(p)


def _bump_mtime(p) -> None:
    """把 mtime 显式往后推，**每次推得比上一次更远**。

    **为什么需要这一步**：`_log_stamp()`（`wbn/bridge.py:82`，取数在第 101 行）
    比的是 `st_mtime_ns`——已经是纳秒，不是浮点秒。但**纳秒精度不等于纳秒
    分辨率**：Windows 的文件时间取自系统时钟，而系统时钟约 15.6ms 才跳一格。
    连续几次写如果落在同一个 tick 里，`st_mtime_ns` 会**完全相同**，缓存
    理应命中——生产代码没错，是测试造数据太快。

    实测：GitHub 的 `windows-latest` 上这两条稳定红（`assert 1 == 2`），
    本机靠时序侥幸通过。所以这里不测「时钟粒度」，只测「stamp 变了会不会
    重读」——那是这两条用例真正要钉的东西。

    **必须用递增的偏移**（`next(_bump)`）而不是「当前值 + 1ms」：写入会把
    mtime 打回系统时钟那一格，两次「当前值 + 1ms」会算出同一个数，断言
    又会假红——正是这个坑。
    """
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + next(_bump) * 1_000_000))


def test_one_page_load_reads_logs_once(tmp_path, counter):
    """验收 1（这一步的核心）：三个出口共用一份 `Facts`，计数 = 1。

    计数是 3 就说明 `Facts` 只是个形式，消费者还在各自读文件。
    """
    _append(tmp_path, _OK_LINE)
    _append(tmp_path, "[09:00:00] channel=bark ok=True msg=ok")

    api = bridge.Bridge()
    api.stats_today()
    api.chain_sentence()
    api.channels_list()

    assert counter["n"] == 1


def test_channel_page_shares_the_same_facts(tmp_path, counter):
    """通道页的两个方法也走同一份——不是只有状态页特殊。"""
    _append(tmp_path, _BAD_LINE)

    api = bridge.Bridge()
    api.channels_list()
    api.channels_state()
    api.stats_today()

    assert counter["n"] == 1


def test_append_invalidates_cache(tmp_path, counter):
    """验收 2：往日志追加一行 → 缓存失效 → 新数据立刻可见。

    这条防的是「缓存永不失效」。用「缓存 N 秒」的写法在第 1 条能过，这里会红。
    """
    _append(tmp_path, _OK_LINE)
    _append(tmp_path, "[09:00:00] channel=bark ok=True msg=ok")

    api = bridge.Bridge()
    first = api.stats_today()["data"]
    assert (first["today"], first["ok"], first["fail"]) == (1, 1, 0)
    assert counter["n"] == 1

    # 用户点完「测试此通道」，或者后台刚投递了一条失败
    _append(tmp_path, _BAD_LINE)

    second = api.stats_today()["data"]
    assert counter["n"] == 2, "文件变了还读缓存——mtime 失效没生效"
    assert (second["today"], second["ok"], second["fail"]) == (1, 1, 1)


def test_touch_without_content_change_is_still_invalidated(tmp_path, counter):
    """mtime 变了就重读，即使内容没变。

    反过来（内容变了但 mtime 没变）在正常文件系统上不可能发生，所以宁可
    多重读一次，也不要漏掉一次更新。这里钉的是「按 mtime」而不是
    「按内容 hash」——后者要读文件才能算，缓存就没意义了。
    """
    _append(tmp_path, _OK_LINE)

    api = bridge.Bridge()
    api.stats_today()
    assert counter["n"] == 1

    p = _today_file(tmp_path)
    p.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    _bump_mtime(p)          # 内容没变，只让 mtime 变（见 `_bump_mtime` 的说明）

    api.stats_today()
    assert counter["n"] == 2


def test_day_rollover_invalidates_cache(tmp_path, counter, monkeypatch):
    """验收 3：跨零点缓存必须失效。

    stamp 里装的是「昨天 + 今天」两个文件的 mtime。日期一变，两个路径都变了，
    文件不存在就记 0——所以跨零点不用特殊处理，stamp 自然全变。

    这里只 patch `bridge.datetime`（stamp 的取数），不动 `logs.datetime`：
    要验的是「stamp 变了就重读」，不是「logs 会读哪一天」。
    """
    _append(tmp_path, _OK_LINE)

    api = bridge.Bridge()
    api.stats_today()
    assert counter["n"] == 1

    real_now = datetime.now()

    class _Tomorrow(datetime):
        @classmethod
        def now(cls, tz=None):
            return real_now + timedelta(days=1)

    monkeypatch.setattr(bridge, "datetime", _Tomorrow)

    api.stats_today()
    assert counter["n"] == 2, "跨零点没失效"


def test_last_send_times_does_not_use_cache(tmp_path, counter):
    """`_last_send_times()` 不走缓存，这是**有意**的。

    它是模块级函数、没有 `self`；要缓存就得挂全局变量，那是比「多读一次
    文件」坏得多的东西。而且 312 个测试直接调它，它们要的是「读一次真数据」，
    不是「读一次别人留下的缓存」。
    """
    _append(tmp_path, _OK_LINE)

    bridge._last_send_times()
    bridge._last_send_times()
    assert counter["n"] == 2


def test_cached_facts_are_the_same_object(tmp_path):
    """三个出口拿到的是**同一个** `Facts` 实例，不是三份内容相等的副本。

    内容相等不够——两份内容相等的 `Facts` 意味着读了两次，中间那次写入
    仍然会被吞掉。
    """
    api = bridge.Bridge()
    first = api._get_facts()
    second = api._get_facts()
    assert first is second


def test_stats_accepts_facts_and_skips_reading(tmp_path, counter):
    """`stats(cfg, facts)` 给了就不读文件——这是共用能成立的前提。"""
    _append(tmp_path, _OK_LINE)
    _append(tmp_path, "[09:00:00] channel=bark ok=True msg=ok")
    facts = logs.read(days=2)
    assert counter["n"] == 1

    s = insight.stats(config.load(), facts)
    assert counter["n"] == 1, "给了 facts 还去读文件"
    assert (s["today"], s["ok"], s["fail"]) == (1, 1, 0)

    # 不给就自己读——312 个旧测试和 ui.py 都按这个签名调
    insight.stats(config.load())
    assert counter["n"] == 2
