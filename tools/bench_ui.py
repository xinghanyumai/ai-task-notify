#!/usr/bin/env python
"""界面响应性能基线。

为什么需要这个脚本：
「界面卡不卡」不能靠感觉。Tk 是单线程的，切页时主线程在同步建控件，
建多久界面就冻结多久 —— 400ms 的冻结用户能明确感觉到「顿了一下」。
没有数字就没法判断一次改版是变快还是变慢，也没法设验收线。

测量四件事：
  1. 启动建界面     App() 构造到首次布局完成        ← 冷启动路径，跨进程 median
  2. 首次进入某页   这一页此前没建过，第一次点进去    ← 冷启动路径，跨进程 median
  3. 切页           页面已在内存里，pack_forget+pack  ← 稳态路径，同进程 median
  4. 整页重建       主题切换走的就是这条路（_rebuild）← 稳态路径，同进程 median

1 和 2 是「冷启动路径」：每个样本一个全新进程，字体未缓存、画布未复用，
跟用户双击图标后的真实体验一致。3 和 4 是「稳态路径」：界面已经开着，
本来就在同一进程里发生，放进子进程反而是错的。
两类数字不可直接比较——冷路径天然比稳态慢一个数量级。

2 和 3 要分开报：页面缓存之后这两个数差一个数量级，混在一起看会得出
「切页很快」的错误结论，而用户第一次点某一页时付的是 2 的钱。

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
# 首次进入某一页的预算按「一次性」定，不按「点击」定：这一下是用户第一次
# 点进某页时付的建页钱。实测最慢的「从零到收到第一条通知」页 850ms 左右——
# 它是一页里上百个控件 + 几千个汉字首次栅格化的总和，Tk 没有更便宜的做法。
#
# 旧版有个「开窗后后台建页」的预热机制替用户先垫了这笔钱，P0 把它删了
# （它同步建页、一次冻住主线程 790ms，和「发送期间不冻结」冲突）。所以现在
# 这几个数字是**真要用户付**的，超预算就是超预算，不许再拿「反正有预热」解释。
BUDGET_COLD = 900
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


def _cold_ms(key: str, runs: int) -> float:
    """首次进入某一页的耗时：**冷启动路径**，每个样本一个全新进程，取中位数。

    为什么必须跨进程：
      1. 同一进程里一页只能「冷」一次——建完就进 `_pages` 了，第二次量到的是
         缓存命中（2~10ms），和首次进入差两个数量级。
      2. 就算强行从缓存里踢掉重建，字体已经栅格化过、画布已经复用，量到的是
         稳态重建，不是用户双击图标后第一次点进去要付的钱。

    和 `_startup_ms` 用同一种隔离方式，两个数字才可比。
    单次采样不可信：P0 实测同一份代码两次跑能差 2 倍（triggers 485 → 1370）。
    本轮再测更夸张——同一页 5 次：1195 / 128 / 101 / 147 / 85ms，10 倍差距，
    头一次跑要付操作系统读 .py、加载字体文件的冷缓存钱。所以先跑一个样本
    丢掉，再取 runs 个的中位数（和 `_median_ms` 同一个做法）。
    """
    import subprocess

    probe = _COLD_PROBE.format(root=str(ROOT), key=key)
    samples: list[float] = []

    def once() -> float | None:
        proc = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        lines = (proc.stdout or "").strip().splitlines()
        if proc.returncode != 0 or not lines:
            return None
        try:
            return float(lines[-1])
        except ValueError:
            return None

    once()                       # 预热：丢掉，只为了让 OS 缓存热起来
    for _ in range(runs):
        value = once()
        if value is not None:
            samples.append(value)
    if not samples:
        return -1.0
    return statistics.median(samples)


def _switch_ms(app, root, keys: list[str], key: str, runs: int) -> float:
    """测「已缓存的页来回切」的耗时。

    不能对同一页连续调用 _show_page —— 已经在那一页时它是空操作，会测出 0ms。
    所以每次都先切到别的页，再切回来。此时两页都在 self._pages 里，
    量到的就是纯粹的换页 + 进场动画开销。
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


# 冷进入同样要独立进程，理由见 `_cold_ms` 的 docstring。
# 注意：探针里必须先 `App()`（它会建首屏 channels），再切到目标页，
# 这样目标页才是真正「没建过」的状态。
_COLD_PROBE = """
import sys, time
sys.path.insert(0, {root!r})
import tkinter as tk
from wbn import config, theme
theme.enable_dpi_awareness()
config.save_default_if_missing()
root = tk.Tk()
root.withdraw()
import ui
app = ui.App(root)
root.update_idletasks()
t = time.perf_counter()
app._show_page({key!r})
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

    # 冷进入是**冷启动路径**：每个样本一个全新进程（字体未缓存、画布未复用），
    # 跟用户双击图标后第一次点进这一页付的钱一致。首屏 channels 不算在内——
    # 它的建页成本已经含在「启动建界面」那一行里。
    cold_total = 0.0
    cold_keys = [k for k in keys if k != app.current_page]
    for key in cold_keys:
        ms = _cold_ms(key, args.runs)
        if ms < 0:
            print(f"冷进入 {key} 测量失败（子进程没返回数字）")
            return 2
        cold_total += ms
        rows.append((f"首次进入 {key}", ms, BUDGET_COLD))

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
    print(f"  首次进入各页合计 ≈ {cold_total:.0f} ms，是用户逐页点开时"
          "一笔一笔付的；背后没有预热垫付")

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
