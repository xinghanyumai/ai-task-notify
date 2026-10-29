"""日志解析的性能守卫（W6.3 §5）。

## 为什么是守卫，不是修

W6 复核报过「1.25MB 日志 → `stats()` 202.6ms」，评级 P1。PM 复核后降 P3、
**不修**，理由是那个 1.25MB 是造出来的：`stats()` 只读 `days=2`
（`wbn/insight.py:175`），`notify-*.log` 有 14 天清理，而**真实单日日志是
23.7KB**。要碰到 50ms 得每天 350KB（15 倍），这个工具一天发不出 6000 条通知。

改缓存 + 异步刷新是行为改动（首次进页面先显示旧值），收益配不上风险。
所以改成**持续守卫**：以后谁把解析改成 O(n²)、或者在 `read()` 里加一次
同步 IO，这条会红。一次性测量没有这个作用。

## 为什么是 2 天 × 400KB

- `days=2` 是生产口径的上限（昨天 + 今天），造 14 天没有意义
- 400KB/天 ≈ 真实量的 **17 倍**，已经越过「重度」档（PM 实测 378KB 两天 → 28ms）
- 断言 150ms 是**退化报警线**（见下面 `BUDGET_MS` 的说明），不是性能目标

## 两条断言缺一不可

`assert elapsed < 0.1` 单独存在是**可以永远绿的**——日志造空了，
`read()` 什么都不解析，0.3ms 就返回。所以先断言真读到了事件，
再断言耗时。删掉前一条，这个文件就退化成「测空气」。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from wbn import config, insight, logs

# 每天写多少字节。400KB × 2 天 = 800KB，约 8000 行。
BYTES_PER_DAY = 400 * 1024

# 断言门槛。**这是退化报警线，不是性能目标。**
#
# 真实场景（单日 23.7KB、`days=2`）只有 4.5ms，离这条线 33 倍。它红了先查
# 解析有没有变成 O(n²)、或者 `read()` 里被塞了一次同步 IO，**别第一反应就调
# 这个数**。真要调，先把「为什么慢」写进提交信息。
#
# 为什么从 100 提到 150（W6.4）：100 是拿**字节数**当度量定错的。日志解析的
# 真实成本由**事件行数**决定，不由字节数决定——同样的 800KB，行短就是行多。
# 现在这份造数按真实行长（79–119 B/行）造，实测 83.6ms（7 次 77.3–87.3），
# 对 100 只有 1.2 倍余量，CI 上慢一点的机器会假红。150 给出 1.8 倍余量，
# 同时仍然能咬住「退化一个数量级」（真退到 200ms 照样红）。
BUDGET_MS = 150.0

# 三种行，形状和 `pipeline.notify()` / `channels.send_all()` 写的一模一样。
# **行长得对**：真实日志平均 79–119 B/行（`logs/notify-2026-10-2*.log` 实测）。
# 第一版把投递行写成 `msg=1.2s`，平均只有 63 B/行——同样 800KB 里多塞了
# 三成行，测出来 89ms，离 100ms 只剩一口气。那不是日志慢，是造的日志不像。
_EVENT = (
    "[{time}] event=Stop kind=task_done sent=True remote_ok=True "
    "elapsed=1.17s title=✅ WorkBuddy 任务完成"
)
_CHANNEL_FAIL = "[{time}] channel=wxmp ok=False msg=errcode=40037 模板 ID 无效"
_CHANNEL_LOG = "[{time}] channel=log ok=True msg=已记录"
_SKIP = (
    "[{time}] skip event=SessionStart kind=session_start "
    "reason=事件类型 session_start 未开启通知"
)


def _fill(path, target_bytes: int) -> int:
    """把一天写成 `target_bytes` 字节，返回写进去的事件行数。"""
    lines: list[str] = []
    size = 0
    i = 0
    while size < target_bytes:
        t = f"{i // 3600 % 24:02d}:{i // 60 % 60:02d}:{i % 60:02d}"
        # 每条事件配两条投递行，每四条再插一条 skip——真实日志就是这个比例
        batch = [_EVENT.format(time=t), _CHANNEL_FAIL.format(time=t),
                 _CHANNEL_LOG.format(time=t)]
        if i % 4 == 0:
            batch.append(_SKIP.format(time=t))
        for line in batch:
            lines.append(line)
            size += len(line.encode("utf-8")) + 1
        i += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return i


def test_stats_under_budget_on_two_days_of_400kb(tmp_path):
    """两天 × 400KB 时 `stats()` 要在 `BUDGET_MS` 内。

    名字里不写死毫秒数：门槛是退化报警线，会随度量口径调整（100 → 150，
    见 `BUDGET_MS` 的说明），写进名字就得跟着改名。
    """
    today = datetime.now()
    counts = {}
    for offset, tag in ((1, "yesterday"), (0, "today")):
        day = today - timedelta(days=offset)
        counts[tag] = _fill(logs.log_path(day), BYTES_PER_DAY)

    total = sum(p.stat().st_size for p in
                (logs.log_path(today - timedelta(days=1)), logs.log_path(today)))
    cfg = config.load()

    # ---- 先证明探针读到了东西。这一条是上面那段 docstring 说的「别测空气」。
    facts = logs.read(days=2)
    assert len(facts.events) == counts["yesterday"] + counts["today"], (
        f"日志造了 {counts} 条事件，只解析出 {len(facts.events)} 条——"
        f"下面那个耗时数字没有意义")
    assert len(facts.deliveries) > len(facts.events), "投递行也应当被解析出来"

    # ---- 再量耗时。取 3 次中位数：单次测量会被 GC / 磁盘缓存带偏。
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        result = insight.stats(cfg)
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    elapsed = times[1]

    assert result["today"] == counts["today"], "今日通知数应当只数今天的事件"
    assert elapsed < BUDGET_MS, (
        f"两天 {total / 1024:.0f}KB 时 stats() 用了 {elapsed:.1f}ms"
        f"（3 次 {[round(t, 1) for t in times]}），超过 {BUDGET_MS}ms。"
        f"看看是不是解析变成 O(n²) 了，或者在 read() 里加了同步 IO")
