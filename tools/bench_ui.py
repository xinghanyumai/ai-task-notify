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
#      源码形态一条，exe 形态另一条（见下）。
A_BUDGET_MS = 2500
#   A（exe 形态）：**双击口径**，即含 PyInstaller 自解压那一段。
#      W7 实测（2026-10-30，本机，各 5 轮取中位数）：
#          源码进程内 1321ms ｜ exe 进程内 1581ms ｜ exe 双击口径 2549ms
#      也就是说 exe 比源码多出来的 ~1230ms 里，约 970ms 是自解压 + bootloader，
#      约 260ms 是 frozen 解释器启动——**我们自己的代码一行都没变慢**
#      （B 数源码 17–27ms / exe 15–30ms，同一量级）。
#      定 3500 = 实测中位数 + 约 37% 余量。
#      为什么不贴着实测定 3000：5 轮里有 1981 也有 3128，机器忙的时候会翻过去。
#      预算的用途是「发现回归」，不是「每天掷骰子」。
#      **注意第一次启动**（清掉 _MEI 且系统文件缓存是冷的）会到 4000ms 上下，
#      那是冷缓存，不是回归——所以判据取多次的**中位数**，不看单次。
A_BUDGET_EXE_MS = 3500
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


def clear_mei_dirs() -> int:
    """清掉 `%TEMP%\\_MEI*`，返回删了几个。

    onefile 每次启动都会解压一个新的 `_MEIxxxx`，正常退出时自己删。但
    上一轮如果是被 `taskkill /F` 强杀的（bench 就是这么收工的），目录会留下来。
    留着不影响正确性（每次是新名字），但会占几百 MB，而且**冷启动的定义
    就是「没有可复用的解压结果」**——所以量之前先清，量出来的才叫冷启动。
    """
    import glob
    import shutil
    import tempfile

    n = 0
    for d in glob.glob(os.path.join(tempfile.gettempdir(), "_MEI*")):
        if not os.path.isdir(d):
            continue
        try:
            shutil.rmtree(d, ignore_errors=True)
            n += 1
        except OSError:
            pass
    return n


def one_run_exe(exe: Path, cold: bool = False) -> tuple[int | None, int | None, int | None]:
    """起一次 `通知.exe`，返回 (A 进程内口径, B, A 双击口径)。

    **两个 A 都要，别只给一个。** 这是 W7 最容易糊过去的地方：

    · `_PROCESS_T0` 在 `app.py` 模块顶层，**它已经在自解压之后了**。
      onefile 的 bootloader 先把 `_MEIxxxx` 解到 `%TEMP%`，然后才启动 Python、
      才 import app。所以 bench 打出来的 A **不含自解压**。
    · 用户感受到的是「双击 → 窗口能点」，**含自解压**。
      这里用 Popen 的时刻当零点，轮询到打点文件出现为止——这就是双击口径。

    两者的差 ≈ 自解压 + bootloader 启动的开销，也是 exe 形态真正比源码慢的那一段。

    **和源码形态的另一个差别是读打点的方式**：exe 是 `console=False` 编的，
    没有 stdout，`print()` 进黑洞、重定向也读不到。所以 app.py 的 `_bench()`
    多给了一条文件通道（`WBN_BENCH_FILE`），这里传一个临时文件过去轮询。

    这个函数存在的意义是「可复现」：exe 形态的 A 数如果只能靠人掐秒表，
    那 W7 定的新预算下次就没人能重新验证。
    """
    import tempfile
    import time

    with tempfile.TemporaryDirectory(prefix="wbn_bench_") as td:
        out = Path(td) / "bench.txt"
        env = dict(os.environ, WBN_BENCH="1", WBN_BENCH_FILE=str(out))
        env.pop("WBN_DEBUG", None)
        if cold:
            clear_mei_dirs()
        # 零点必须在 Popen **之前**：差这几十微秒无所谓，差在 Popen 之后就
        # 把「进程创建」也算进自研代码了。
        t_wall0 = time.perf_counter()
        proc = subprocess.Popen(
            [str(exe)],
            cwd=str(exe.parent),        # 和双击一样：工作目录 = exe 旁边
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        try:
            box: dict[str, str | None] = {"a": None, "b": None}
            wall: int | None = None
            deadline = time.monotonic() + TIMEOUT_S
            while time.monotonic() < deadline:
                if out.exists():
                    text = out.read_text(encoding="utf-8", errors="replace")
                    m = LINE_RE.search(text)
                    if m:
                        box["a"] = m.group(1)
                        wall = int((time.perf_counter() - t_wall0) * 1000)
                        break
                    m = SELF_RE.search(text)
                    if m:
                        box["b"] = m.group(1)
                if proc.poll() is not None:
                    break
                time.sleep(0.01)
            a = int(box["a"]) if box["a"] else None
            b = int(box["b"]) if box["b"] else None
            return a, b, wall
        finally:
            kill_tree(proc)


def main() -> int:
    ap = argparse.ArgumentParser(description="首屏可交互耗时基线")
    ap.add_argument("--runs", type=int, default=3, help="跑几轮取中位数（默认 3）")
    ap.add_argument("--strict", action="store_true", help="超预算时退出码 1")
    ap.add_argument(
        "--cold",
        action="store_true",
        help="每次启动前清 %%TEMP%%\\_MEI*（exe 形态量冷启动用）",
    )
    ap.add_argument(
        "--exe",
        nargs="?",
        const=str(ROOT / "通知.exe"),
        default=None,
        metavar="PATH",
        help="量 exe 形态（默认根目录的 通知.exe）。不传就是源码形态。",
    )
    args = ap.parse_args()

    if args.exe is None and not (ROOT / "app.py").exists():
        print("找不到 app.py", file=sys.stderr)
        return 2
    exe = Path(args.exe) if args.exe else None
    if exe is not None and not exe.exists():
        print(f"找不到 {exe}，先跑 python tools/build_exe.py", file=sys.stderr)
        return 2

    a_budget = A_BUDGET_EXE_MS if exe else A_BUDGET_MS

    samples_a: list[int] = []
    samples_b: list[int] = []
    samples_wall: list[int] = []
    for i in range(max(1, args.runs)):
        if exe:
            ms_a, ms_b, ms_wall = one_run_exe(exe, cold=args.cold)
        else:
            ms_a, ms_b = one_run()
            ms_wall = None
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
        if ms_wall is not None:
            samples_wall.append(ms_wall)
        tail = f"  双击口径={ms_wall}ms" if ms_wall is not None else ""
        print(f"  第 {i + 1} 轮：A={ms_a}ms  B={ms_b if ms_b is not None else '—'}ms{tail}")

    median_a = int(statistics.median(samples_a))
    print()
    print(f"A 首屏可交互（进程起算，中位数 of {len(samples_a)}）：{median_a} ms")
    if exe:
        # exe 形态下这条**不含自解压**，所以预算不挂在它上面，只作参考。
        print("  （exe 形态下这条不含自解压，预算挂在下面的双击口径上）")
    else:
        print(f"  预算 {a_budget} ms（仅源码形态）→ "
              f"{'通过' if median_a <= a_budget else '超标'}")
    median_wall = None
    if samples_wall:
        median_wall = int(statistics.median(samples_wall))
        print(f"A 首屏可交互（双击口径 = 含自解压，中位数 of {len(samples_wall)}）："
              f"{median_wall} ms")
        print(f"  预算 {A_BUDGET_EXE_MS} ms → "
              f"{'通过' if median_wall <= A_BUDGET_EXE_MS else '超标'}")
        print(f"  其中自解压 + bootloader ≈ {median_wall - median_a} ms"
              "（双击口径 − 进程内口径）")
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
    if not exe:
        print("**exe 形态必须重测 A**：`python tools/bench_ui.py --exe`。")
    else:
        print("exe 形态的 A 里多了一段 PyInstaller 自解压（解到 %TEMP%\\_MEIxxxx），"
              "这段和代码质量无关，所以 exe 的预算单列，不用源码那个 2500。")

    if args.strict:
        # exe 形态守「双击口径」（用户真正等的那段）；源码形态守进程内口径。
        if exe:
            if median_wall is not None and median_wall > A_BUDGET_EXE_MS:
                return 1
        elif median_a > a_budget:
            return 1
        if median_b is not None and median_b > B_BUDGET_MS:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
