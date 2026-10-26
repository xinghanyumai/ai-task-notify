#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""界面响应性能基线。

为什么需要这个脚本：
「界面卡不卡」不能靠感觉。Tk 是单线程的，切页时主线程在同步建控件，
建多久界面就冻结多久 —— 400ms 的冻结用户能明确感觉到「顿了一下」。
没有数字就没法判断一次改版是变快还是变慢，也没法设验收线。

测量三件事：
  1. 启动建界面     App() 构造到首次布局完成
  2. 切页           每页从 _show_page() 到布局完成
  3. 整页重建       主题切换走的就是这条路（_rebuild）

用法：
    python tools/bench_ui.py                 # 人看的表格
    python tools/bench_ui.py --strict        # 超预算时退出码 1，给 CI / 验收用
    python tools/bench_ui.py --runs 5        # 多跑几轮取中位数

判据参考（Nielsen 响应时间阈值）：
  ≤100ms  瞬时，用户不觉得有等待
  ≤300ms  略有停顿，但注意力没断
  >300ms  明确卡顿，会开始怀疑是不是没点上

注意：本脚本会真的创建 Tk 窗口（withdraw 状态，不显示）。
无图形栈的环境会直接报错退出，这属于预期行为。
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 预算（毫秒）。超过就是用户能感觉到的卡顿。
BUDGET_STARTUP = 400
BUDGET_PAGE = 100
BUDGET_REBUILD = 300


def _median_ms(fn, runs: int) -> float:
    """跑 runs 次取中位数。首次调用含冷启动，单独跑一次丢掉。"""
    fn()
    samples = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t) * 1000)
    return statistics.median(samples)


def _switch_ms(app, root, keys: list[str], key: str, runs: int) -> float:
    """测「冷切到某一页」的耗时。

    不能对同一页连续调用 _show_page —— 已经在那一页时它是空操作，会测出 0ms。
    所以每次都先切到别的页，再切回来，确保这一下真的在重建控件。
    """
    samples = []
    for _ in range(runs):
        away = next(k for k in keys if k != key)
        app._show_page(away)
        root.update_idletasks()

        t = time.perf_counter()
        app._show_page(key)
        root.update_idletasks()
        samples.append((time.perf_counter() - t) * 1000)
    return statistics.median(samples)


# 启动耗时必须在**独立进程**里测。
# 同一个进程里 App() 只能建一次；而且在已建过界面的进程里再测，
# 字体会被缓存、画布会被复用，测出来是「热启动」，跟用户双击图标时的
# 体验不是一回事。实测同机冷启动 120ms~760ms 都有，不隔离就完全不可信。
_STARTUP_PROBE = """
import sys, time
sys.path.insert(0, {root!r})
import tkinter as tk
from wbn import config, theme
theme.enable_dpi_awareness()
config.save_default_if_missing()
root = tk.Tk()
root.withdraw()
import ui
t = time.perf_counter()
app = ui.App(root)
root.update_idletasks()
print((time.perf_counter() - t) * 1000)
root.destroy()
"""


def _startup_ms(runs: int) -> float:
    """每个样本一个全新进程，取中位数。"""
    import subprocess

    probe = _STARTUP_PROBE.format(root=str(ROOT))
    samples: list[float] = []
    for _ in range(runs):
        proc = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        line = (proc.stdout or "").strip().splitlines()
        if proc.returncode == 0 and line:
            try:
                samples.append(float(line[-1]))
            except ValueError:
                pass
    if not samples:
        return -1.0
    return statistics.median(samples)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="界面响应性能基线")
    ap.add_argument("--runs", type=int, default=3, help="每个测量点重复次数（取中位数）")
    ap.add_argument("--strict", action="store_true", help="超预算时返回非零退出码")
    args = ap.parse_args(argv)

    try:
        import tkinter as tk
    except Exception as exc:
        print(f"没有 tkinter，无法测量：{exc}")
        return 2

    from wbn import config, theme

    theme.enable_dpi_awareness()
    config.save_default_if_missing()

    try:
        root = tk.Tk()
    except Exception as exc:
        print(f"没有图形栈，无法测量：{exc}")
        return 2
    root.withdraw()

    # Tk 回调异常默认只写 stderr，测量时也必须接住，
    # 否则「某页画崩了」会被算成「这页很快」。
    swallowed: list[str] = []
    theme.install_tk_exception_hook(root, on_error=lambda t: swallowed.append(t))

    import ui

    over: list[str] = []
    rows: list[tuple[str, float, int]] = []

    startup = _startup_ms(args.runs)
    if startup < 0:
        print("启动测量失败（子进程没返回数字）")
        return 2
    rows.append(("启动建界面（冷）", startup, BUDGET_STARTUP))

    # 切页与重建在同一个进程里测：它们本来就是「已经开着界面」时才发生的操作，
    # 放进子进程反而是错的。
    app = ui.App(root)
    root.update_idletasks()

    keys = [p[0] for p in ui.PAGES]
    for key in keys:
        ms = _switch_ms(app, root, keys, key, args.runs)
        rows.append((f"切到 {key}", ms, BUDGET_PAGE))

    ms = _median_ms(lambda: (app._rebuild(), root.update_idletasks()), args.runs)
    rows.append(("整页重建（切主题）", ms, BUDGET_REBUILD))

    print(f"\n界面响应性能（中位数，每点 {args.runs} 次）")
    print("=" * 56)
    for label, ms, budget in rows:
        flag = " " if ms <= budget else "!"
        print(f"{flag} {label:22s} {ms:7.1f} ms   预算 {budget} ms")
        if ms > budget:
            over.append(f"{label} {ms:.0f}ms > {budget}ms")
    print("=" * 56)

    if swallowed:
        print(f"\n测量期间 Tk 回调抛了 {len(swallowed)} 次异常（这些页的数字不可信）：")
        for text in swallowed:
            print("  -", text.splitlines()[-1])
        over.append(f"{len(swallowed)} 次 Tk 回调异常")

    if over:
        print(f"\n超出预算 {len(over)} 项：")
        for item in over:
            print("  -", item)
        print("\n这是基线，不是判死刑：改版后重跑，数字变小就是真的变快了。")
        return 1 if args.strict else 0

    print("\n全部在预算内。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
