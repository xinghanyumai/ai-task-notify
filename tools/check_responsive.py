"""验证「发送测试消息期间界面仍然可交互」。

这是针对 P0 的可量化检查，不是感觉。分三段量，避免把不同的开销混在一起：

A. 空闲基线 —— 什么都不做，只量主线程每 20ms 打点的最大间隔
B. 纯发送   —— 把 `pipeline.send_test` 换成睡 3 秒的假实现，同样打点。
               如果网络还在主线程上，这里的最大间隔会接近 3000ms。
C. 发送 + 切页 —— 飞行途中真的切页，量 `_show_page` 的耗时。
               这一段量的是页面构建开销（第 4 组的事），不计入 P0 结论。

每段开始前先 `gc.collect()`，让分代回收的状态归零，否则「启动后第一次
整堆回收」的几百毫秒会随机落进任意一段，把结论搅浑。GC 本身的影响单独
用 gc.callbacks 计数报出来。

用法：python tools/check_responsive.py
退出码 0 = 通过。
"""
from __future__ import annotations

import gc
import sys
import time
import tkinter as tk

sys.path.insert(0, ".")

from wbn import anim, pipeline, theme  # noqa: E402

FAIL: list[str] = []
TICK_MS = 20
BLOCK_S = 3.0
STALL_LIMIT = 120.0   # ms，超过这个值人眼能看出「卡了一下」


def ok(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'✓' if cond else '✗'} {label}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAIL.append(label)


def main() -> None:
    theme.enable_dpi_awareness()
    theme.set_app_user_model_id()

    import ui

    root = tk.Tk()
    theme.install_tk_exception_hook(
        root,
        on_error=lambda text: FAIL.append(
            f"Tk 回调异常：{text.splitlines()[-1]}"),
    )
    app = ui.App(root)
    app._show_page("channels")
    root.update_idletasks()
    anim.set_reduced(False)

    # ---------------------------------------------------------- 打点器
    gaps: list[tuple[float, bool]] = []   # (间隔 ms, 当时是否在切页)
    marks: list[float] = []
    page_switches: list[float] = []
    state = {"stop": False, "switching": False}

    def tick() -> None:
        if state["stop"]:
            return
        now = time.monotonic()
        if marks:
            gaps.append(((now - marks[-1]) * 1000, state["switching"]))
        marks.append(now)
        root.after(TICK_MS, tick)

    def reset(seconds: float) -> None:
        """清空采样，跑满 seconds 秒，让主循环转起来。"""
        gc.collect()
        gaps.clear()
        marks.clear()
        state["stop"] = False
        root.after(TICK_MS, tick)
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            root.update()
            time.sleep(0.005)
        state["stop"] = True
        root.update()

    def worst(switching: bool) -> float:
        vals = [g for g, sw in gaps if sw is switching]
        return max(vals) if vals else 0.0

    def pump(cond, extra: float = 0.0) -> None:
        """在主线程里跑事件循环，直到 cond() 为假，再多跑 extra 秒。"""
        deadline = time.monotonic() + BLOCK_S + 5.0
        while cond() and time.monotonic() < deadline:
            root.update()
            time.sleep(0.005)
        end = time.monotonic() + extra
        while time.monotonic() < end:
            root.update()
            time.sleep(0.005)

    # ---------------------------------------------------------- A 空闲基线
    print("\n[A] 空闲基线（不做任何事）")
    reset(1.5)
    idle_worst = worst(False)
    print(f"    主线程最大停顿 {idle_worst:.0f}ms，采样 {len(gaps)} 点")

    # ---------------------------------------------------------- 准备假发送
    calls: list[float] = []

    def slow_send(cfg, targets=None):
        calls.append(time.monotonic())
        time.sleep(BLOCK_S)
        return [{"ok": True, "msg": "假发送成功", "channel": "wxmp"}]

    real_send = pipeline.send_test
    pipeline.send_test = slow_send

    # ---------------------------------------------------------- B 纯发送
    print("\n[B] 纯发送（3 秒假网络，期间不切页）")
    gc.collect()
    gaps.clear()
    marks.clear()
    state["stop"] = False
    state["switching"] = False
    root.after(TICK_MS, tick)

    btn = app.ch_test_btn or app.test_btn
    ok(btn is not None, "找到触发网络的按钮")
    mid_state: dict = {}
    t0 = time.monotonic()
    app._test_channel(app.current_channel)

    def sample() -> None:
        if time.monotonic() - t0 < BLOCK_S * 0.5:
            root.after(50, sample)
            return
        mid_state["loading"] = bool(getattr(btn, "_loading", False))
        mid_state["enabled"] = bool(getattr(btn, "_enabled", True))

    root.after(200, sample)

    pump(lambda: app._bg_busy, extra=0.5)
    elapsed = (time.monotonic() - t0) * 1000
    state["stop"] = True
    root.update()

    send_worst = worst(False)
    ok(bool(calls), "后台确实发起了网络调用")
    ok(elapsed >= BLOCK_S * 1000 * 0.9,
       f"整次发送耗时 {elapsed:.0f}ms（假网络固定 {BLOCK_S * 1000:.0f}ms）")
    ok(send_worst < STALL_LIMIT,
       f"发送期间主线程最大停顿 {send_worst:.0f}ms"
       f"（阈值 {STALL_LIMIT:.0f}ms，空闲基线 {idle_worst:.0f}ms，"
       f"{len(gaps)} 个采样点）",
       f"最长 {send_worst:.0f}ms")
    ok(mid_state.get("loading") is True, "飞行中按钮处于加载态")
    ok(mid_state.get("enabled") is False, "飞行中按钮被禁用")
    ok(getattr(btn, "_loading", True) is False, "结束后按钮退出加载态")
    ok(getattr(btn, "_enabled", False) is True, "结束后按钮恢复可点")
    ok(not app._bg_busy, "结束后忙标志已复位")

    # ---------------------------------------------------------- C 发送 + 切页
    print("\n[C] 发送途中切页（量页面构建耗时，归第 4 组）")
    gc.collect()
    gaps.clear()
    marks.clear()
    page_switches.clear()
    state["stop"] = False
    state["switching"] = False
    root.after(TICK_MS, tick)

    def switch_pages(keys: list[str] | None = None) -> None:
        todo_now = list(keys or ["triggers", "status", "template", "channels"])
        if not todo_now:
            return
        key = todo_now.pop(0)
        t = time.monotonic()
        app._show_page(key)
        root.update_idletasks()
        page_switches.append((time.monotonic() - t) * 1000)
        root.after(80, lambda: switch_pages(todo_now))

    t0 = time.monotonic()
    app._test_channel(app.current_channel)

    def start_switching() -> None:
        state["switching"] = True
        switch_pages()

    root.after(150, start_switching)
    pump(lambda: app._bg_busy, extra=0.3)
    state["stop"] = True
    root.update()

    ok(len(page_switches) >= 2,
       f"飞行途中成功切换 {len(page_switches)} 次页面"
       f"（{', '.join(f'{v:.0f}ms' for v in page_switches)}）")
    print(f"    切页期间主线程最大停顿 {worst(True):.0f}ms"
          f" —— 与切页耗时同量级，是页面构建开销，不计入 P0 结论")

    # ---------------------------------------------------------- D GC 影响
    print("\n[D] 分代回收对主线程的影响")
    counts = {"gen0": 0, "gen1": 0, "gen2": 0}

    def on_gc(phase: str, info: dict) -> None:
        if phase == "stop":
            counts[f"gen{info.get('generation', 0)}"] += 1

    gc.callbacks.append(on_gc)
    reset(3.0)
    gc.callbacks.remove(on_gc)
    print(f"    3 秒空闲内的回收次数：{counts}，阈值 {gc.get_threshold()}")
    ok(counts["gen2"] <= 1,
       f"3 秒内整堆回收 {counts['gen2']} 次（应 ≤1，多了就会随机卡顿）")

    # ---------------------------------------------------------- E Tab 顺序
    print("\n[E] Tab 顺序与视觉顺序一致")
    app._show_page("channels")
    root.update_idletasks()
    root.focus_force()
    root.update()

    order: list[tuple[str, object, int]] = []   # (名字, 控件, y)
    seen_ids: set[int] = set()
    cur = app.root.focus_get() or app.root
    for _ in range(60):
        try:
            nxt = app.root.tk.call("tk_focusNext", cur)
        except Exception:
            break
        if not nxt:
            break
        w = app.root.nametowidget(nxt)
        if id(w) in seen_ids:
            break
        seen_ids.add(id(w))
        try:
            order.append((str(nxt).rsplit(".", 1)[-1], w, w.winfo_rooty()))
        except tk.TclError:
            break
        cur = w

    # 判据：桌面布局不存在「一条 y 序列」。通道页是「左列表 / 右表单」两列，
    # 合理的顺序是「同一块内自上而下，块与块整块访问」。
    #
    # 块 = 最外层的分栏容器：沿祖先往上找，直到父容器是页面根或主区。
    # 不能按 x 坐标猜分栏——内容栏里的列表行在 x 上和侧栏有重叠区。
    page_kids = app.content.winfo_children()
    page_root = page_kids[0] if page_kids else None

    def block_of(w):
        p = w
        while p is not None:
            m = p.master
            if m is page_root or m is app.main:
                return p
            p = m
        return None

    blocks_w = [block_of(w) for _n, w, _y in order]
    names = [f"{b.winfo_name() if b is not None else '?'}" for b in blocks_w]
    # 合并成连续段：相邻同名块视为一段
    segs: list[str] = []
    seg_ids: list[int] = []
    for b, nm in zip(blocks_w, names, strict=False):
        bid = id(b)
        if not seg_ids or seg_ids[-1] != bid:
            segs.append(nm)
            seg_ids.append(bid)

    back = [(n, y) for i, (n, _w, y) in enumerate(order)
            if i and y < order[i - 1][2] - 40
            and id(blocks_w[i]) == id(blocks_w[i - 1])]

    ok(len(order) >= 5, f"Tab 走通了 {len(order)} 个控件")
    ok(len(seg_ids) == len(set(seg_ids)),
       f"块不被反复横跳（{len(seg_ids)} 段：{'→'.join(segs)}）",
       f"实际路径 {names}")
    ok(not back, "同一块内自上而下，0 处往回跳",
       "；".join(f"{n} @y={y}" for n, y in back[:5]))
    if order:
        print(f"    路径：{' → '.join(n for n, _w, _y in order[:10])}")

    pipeline.send_test = real_send
    root.update()
    root.destroy()

    print("\n" + "=" * 56)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
