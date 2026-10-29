#!/usr/bin/env python
"""首屏可交互耗时基线（web 版）。

**这个脚本是 W5 重写的。** 旧版量的是 Tk 的四条路径（App() 构造、首次进页、
切页、整页重建），因为 Tk 是单线程的，切页时同步建控件会把界面冻住。那套数字
的最终态在 [`docs/baseline-tkinter.md`](../docs/baseline-tkinter.md)，作历史对照。

web 版没有「同步建控件」这回事：切页是改 DOM，主线程停顿时长由 DevTools 的
Performance 录制看（W6 §4.1），不是 Python 能打点的。Python 这边唯一能可靠
测量、且用户真的在等的，是**从双击到首屏可交互**这一段：

    A 数：进程起 → 前端第一次调通 bridge        ← 本脚本量的就是这个
    B 数：App() 构造 → 首屏可交互                ← 需要 app.py 内部打点，W6 补
    C 数：实际 DPI 缩放                          ← W6 §4.1 要求一起报，否则 A/B 不可比

A 数含解释器启动 + pywebview import + WebView2 初始化，所以它是**上界**。
拿它和 tkinter 的 164.1ms 直接比是错的——那个数不含进程启动，两套口径不能相减。

怎么量：起一个 `python app.py` 子进程，`WBN_BENCH=1` 让 app.py 在「前端第一次
调通 bridge」时往 stdout 打一行 `[bench] first-interactive 1234ms`，读到就收工，
把整棵进程树杀掉（WebView2 会派生 msedgewebview2.exe，只杀父进程会留一堆）。

用法：
    python tools/bench_ui.py                 # 人看的表格
    python tools/bench_ui.py --strict        # 超预算退出码 1，给 CI / 验收用
    python tools/bench_ui.py --runs 5        # 多跑几轮取中位数（默认 3）

预算 600ms 的来历：W6 §4.1 定的目标值，不是本脚本拍的。**要改就改
`BUDGET_MS` 并同步改 W6，不许为了让数字变绿而调。**
"""

from __future__ import annotations

import argparse
import os
import re
import statistics
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BUDGET_MS = 600
LINE_RE = re.compile(r"\[bench\] first-interactive (\d+)ms")
TIMEOUT_S = 60


def console_python() -> str:
    """用控制台版 python：pythonw.exe 没有 stdout，读不到打点。"""
    exe = Path(sys.executable)
    plain = exe.with_name("python.exe")
    return str(plain) if plain.exists() else str(exe)


def kill_tree(proc: subprocess.Popen) -> None:
    """整棵杀掉。WebView2 是独立进程树，只 kill 父进程会留下孤儿。"""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, check=False)
    else:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def one_run() -> int | None:
    """起一次 app.py，返回首屏可交互毫秒数；没读到打点返回 None。"""
    env = dict(os.environ, WBN_BENCH="1")
    env.pop("WBN_DEBUG", None)          # debug 会开 DevTools，拖慢首帧
    proc = subprocess.Popen(
        [console_python(), str(ROOT / "app.py")],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=0x08000000 if sys.platform == "win32" else 0,
    )
    try:
        box: list[str | None] = [None]

        def reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                m = LINE_RE.search(line)
                if m:
                    box[0] = m.group(1)
                    return

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(TIMEOUT_S)
        return int(box[0]) if box[0] else None
    finally:
        kill_tree(proc)


def main() -> int:
    ap = argparse.ArgumentParser(description="首屏可交互耗时基线")
    ap.add_argument("--runs", type=int, default=3, help="跑几轮取中位数（默认 3）")
    ap.add_argument("--strict", action="store_true", help="超预算时退出码 1")
    args = ap.parse_args()

    if not (ROOT / "app.py").exists():
        print("找不到 app.py", file=sys.stderr)
        return 2

    samples: list[int] = []
    for i in range(max(1, args.runs)):
        ms = one_run()
        if ms is None:
            print(f"第 {i + 1} 轮没读到打点。可能原因：", file=sys.stderr)
            print("  · pywebview 没装 / WebView2 缺失 → 走了原生提示框降级",
                  file=sys.stderr)
            print("  · 窗口起来了但前端没调通 bridge（看 logs/ 里的存活探针日志）",
                  file=sys.stderr)
            return 2
        samples.append(ms)
        print(f"  第 {i + 1} 轮：{ms} ms")

    median = int(statistics.median(samples))
    print()
    print(f"首屏可交互（进程起算，中位数 of {len(samples)}）：{median} ms")
    print(f"预算：{BUDGET_MS} ms  →  {'通过' if median <= BUDGET_MS else '超标'}")
    print()
    print("口径说明：含解释器启动 + pywebview import + WebView2 初始化。"
          "切页耗时量不了，见 W6 §4.1（DevTools Performance）。")

    if args.strict and median > BUDGET_MS:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
