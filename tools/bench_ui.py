#!/usr/bin/env python
"""首屏可交互耗时基线（web 版）。

**这个脚本是 W5 重写的。** 旧版量的是 Tk 的四条路径（App() 构造、首次进页、
切页、整页重建），因为 Tk 是单线程的，切页时同步建控件会把界面冻住。那套数字
的最终态在 [`docs/baseline-tkinter.md`](../docs/baseline-tkinter.md)，作历史对照。

web 版没有「同步建控件」这回事：切页是改 DOM，主线程停顿时长由 DevTools 的
Performance 录制看（W6 §4.1），不是 Python 能打点的。Python 这边唯一能可靠
测量、且用户真的在等的，是**从双击到首屏可交互**这一段：

    A 数：进程起 → 前端第一次调通 bridge        ← 用户真正在等的那段
    B 数：A 里**属于本项目**的那部分（减去两个第三方 import 的余量）

**A 是架构决定的，B 才是防回归的。** A 里 96% 不是我们的代码（pywebview 208ms
+ WinForms 程序集 1009ms + WebView2 运行时 878ms，见 W6 复核），所以 A 只能当
「有没有整体退化」的护栏；以后谁在 `Bridge.__init__` 里同步读日志、同步打网络，
A 会涨、B 也会涨，而**只有 B 能指到是我们自己写的哪一段**。

怎么量：起一个 `python app.py` 子进程，`WBN_BENCH=1` 让 app.py 往 stdout 打两行：

    [bench] self-code 40ms              ← Bridge() 构造完成时，B 数
    [bench] first-interactive 1234ms    ← 前端第一次调通 bridge，A 数

读到 A 就收工，把整棵进程树杀掉（WebView2 会派生 msedgewebview2.exe，
只杀父进程会留一堆）。

用法：
    python tools/bench_ui.py                 # 人看的表格
    python tools/bench_ui.py --strict        # 超预算退出码 1，给 CI / 验收用
    python tools/bench_ui.py --runs 5        # 多跑几轮取中位数（默认 3）

**预算是 PM 裁定的（W6.3 §2），不是本脚本拍的。** 要改就改下面两个常量
并同步改 W6.3，不许为了让数字变绿而调。

**exe 形态必须重测。** PyInstaller 的自解压会让 A 再涨，涨多少现在不知道；
`A_BUDGET_MS` 只对源码形态成立，W7 打包后不许直接沿用。
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

# 双基线（W6.3 §2）。**两条一起守，不许只留第一条。**
#   A：进程起 → 首屏可交互。架构决定的，不是优化目标。
#      **只对源码形态成立**——exe 的自解压会让它再涨，W7 必须重测。
A_BUDGET_MS = 2500
#   B：A 里属于本项目的那部分。这条才是防回归的。
B_BUDGET_MS = 100

LINE_RE = re.compile(r"\[bench\] first-interactive (\d+)ms")
SELF_RE = re.compile(r"\[bench\] self-code (\d+)ms")
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


def one_run() -> tuple[int | None, int | None]:
    """起一次 app.py，返回 (A 首屏可交互, B 自研段) 毫秒数；没读到给 None。

    B 那行比 A 早打（`Bridge()` 建在窗口之前），所以读到 A 时 B 一定已经在
    `box` 里了——不用再等。
    """
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
        box: dict[str, str | None] = {"a": None, "b": None}

        def reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                m = LINE_RE.search(line)
                if m:
                    box["a"] = m.group(1)
                    return
                m = SELF_RE.search(line)
                if m:
                    box["b"] = m.group(1)

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(TIMEOUT_S)
        a = int(box["a"]) if box["a"] else None
        b = int(box["b"]) if box["b"] else None
        return a, b
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

    samples_a: list[int] = []
    samples_b: list[int] = []
    for i in range(max(1, args.runs)):
        ms_a, ms_b = one_run()
        if ms_a is None:
            print(f"第 {i + 1} 轮没读到打点。可能原因：", file=sys.stderr)
            print("  · pywebview 没装 / WebView2 缺失 → 走了原生提示框降级",
                  file=sys.stderr)
            print("  · 窗口起来了但前端没调通 bridge（看 logs/ 里的存活探针日志）",
                  file=sys.stderr)
            return 2
        samples_a.append(ms_a)
        if ms_b is not None:
            samples_b.append(ms_b)
        print(f"  第 {i + 1} 轮：A={ms_a}ms  B={ms_b if ms_b is not None else '—'}ms")

    median_a = int(statistics.median(samples_a))
    print()
    print(f"A 首屏可交互（进程起算，中位数 of {len(samples_a)}）：{median_a} ms")
    print(f"  预算 {A_BUDGET_MS} ms（**仅源码形态**）→ "
          f"{'通过' if median_a <= A_BUDGET_MS else '超标'}")
    if samples_b:
        median_b = int(statistics.median(samples_b))
        print(f"B 自研代码段（中位数 of {len(samples_b)}）：{median_b} ms")
        print(f"  预算 {B_BUDGET_MS} ms → "
              f"{'通过' if median_b <= B_BUDGET_MS else '超标'}")
    else:
        median_b = None
        print("B 自研代码段：没读到 [bench] self-code（app.py 的打点被改坏了？）")
    print()
    print("口径说明：A 含解释器启动 + pywebview import + WinForms 程序集 + "
          "WebView2 初始化，96% 不是我们的代码；B 是减去两个第三方 import 后的余量，"
          "也就是「我们自己那段」。切页耗时量不了，见 W6 §4.1（DevTools Performance）。")
    print("**exe 形态必须重测 A**（PyInstaller 自解压会让它再涨），不许沿用上面这个数。")

    if args.strict:
        if median_a > A_BUDGET_MS:
            return 1
        if median_b is not None and median_b > B_BUDGET_MS:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
