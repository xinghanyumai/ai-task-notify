"""启动层（P1 第 5.3 / 5.4 节）。

它是一个**独立的 Toplevel**，不在主窗口里叠遮罩——主窗口尺寸未定时叠遮罩
会闪一帧旧内容（5.3 第 1 条）。

七条硬要求逐条落在这里：

1. 独立 Toplevel、`overrideredirect(True)`、320×200 居中   → `__init__`
2. 进度线不带百分比数字                                    → 只画线，不画字
3. 绝不「走到 100% 然后停住等」                            → `finish()` 用
   `ease_out_quint` 在 180ms 内走完，超时则停在 90% 由调用方控制
4. Esc 可跳过                                              → `skip()`
5. 用户有输入（除 Esc）就放弃剩余预热                       → `on_user_input()`
6. 预热绝不越过 T2                                         → 销毁后 `alive` 为假，
   编排层每次回调先查 `alive`
7. 减弱动效：直接 100%、停 300ms、交接，不做渐显           → `finish()`

视觉（5.4）：垂直渐变背景（色差 6% 以内）、圆角 16、1px 描边、居中 logo 40px +
一层极淡光晕、应用名小字、底部进度线 200×2。**不加** slogan、版权号、
「正在加载…」、旋转 spinner。

Tkinter 的两个能力边界：

- 没有逐控件 alpha，但有**整窗** alpha（`wm attributes -alpha`），所以「整层
  淡入淡出」是能做真的，不是拿颜色近似。
- 圆角窗口靠 `-transparentcolor` 抠掉四个角。抠不掉的平台（非 Windows）退化成
  直角窗口，不影响功能。
"""

from __future__ import annotations

import tkinter as tk

from . import anim, theme
from . import widgets as W
from .tokens import MOTION, STK

# 逻辑尺寸。P1 5.3 定死 320×200。
_W, _H = 320, 200

# 透明色键：圆角之外那四个角用它填，系统会把这颜色抠成透明。
# 选品红是因为它不会出现在调色板里，不会误伤真实像素。
_KEY = "#ff00fe"


class Splash:
    """启动层窗口。生命周期：`show()` → `fade_in()` → 若干 `animate_to()`
    → `finish()` → 窗口销毁并回调 `on_handoff`。"""

    def __init__(self, root: tk.Misc, pal: theme.Palette, name: str,
                 on_handoff) -> None:
        self.root = root
        self.pal = pal
        self.name = name
        self.alive = True
        self._on_handoff = on_handoff
        self._on_input = None
        self._frac = 0.0
        self._drawn = 0.0
        self._fast = False

        self._w, self._h = theme.S(_W), theme.S(_H)
        self._bar_w = theme.S(200)
        self._bar_h = theme.S(2)
        self._bar_x = (self._w - self._bar_w) / 2
        self._bar_y = theme.S(160)

        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.configure(bg=_KEY)
        try:
            # 抠掉圆角外的四个角。失败就退化成直角窗口。
            win.attributes("-transparentcolor", _KEY)
        except tk.TclError:
            pass
        # 先全透明再画，避免「白底闪一帧」
        try:
            win.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        win.attributes("-topmost", True)
        self.win = win
        self._center()

        cv = tk.Canvas(win, width=self._w, height=self._h, bg=_KEY,
                       highlightthickness=0, bd=0)
        cv.pack()
        self.cv = cv

        self._paint()
        win.bind("<Escape>", lambda e: self.skip())
        win.bind("<Key>", self._user_input)
        win.bind("<Button-1>", self._user_input)

    # ------------------------------------------------------------ 几何

    def _center(self) -> None:
        """居中，但略高于正中：正中在视觉上会显得「往下掉」。"""
        try:
            sw = self.win.winfo_screenwidth()
            sh = self.win.winfo_screenheight()
        except tk.TclError:
            sw, sh = 1920, 1080
        x = (sw - self._w) // 2
        y = (sh - self._h) // 3
        self.win.geometry(f"{self._w}x{self._h}+{x}+{y}")

    # ------------------------------------------------------------ 绘制

    def _paint(self) -> None:
        cv, p = self.cv, self.pal
        w, h = self._w, self._h
        r = theme.S(16)

        # 背景：垂直渐变。这是全应用唯一一处大面积渐变——用户只在这里停留
        # 1~2 秒，看过就没了，不会造成长期视觉疲劳（P1 5.4）。
        W.linear_gradient(cv, (0, 0, w, h), p.splash_top, p.splash_bottom,
                          radius=r, tags="bg")
        W.round_rect(cv, 0.5, 0.5, w - 0.5, h - 0.5, r,
                     fill="", outline=p.border, width=STK.rule)

        # logo：40px 圆角方块 + 铃铛，和侧栏品牌区同一个画法，让人一眼认出
        # 「这就是刚才点的那个图标」。
        d = theme.S(40)
        lx = (w - d) / 2
        ly = theme.S(52)
        self._halo(w / 2, ly + d / 2)
        W.round_rect_grad(cv, lx, ly, lx + d, ly + d, theme.S(9),
                          p.brand_top, p.brand_bottom)
        pad = theme.S(9)
        W.draw_icon(cv, "bell", lx + pad, ly + pad, d - pad * 2,
                    p.on_brand, STK.icon)

        cv.create_text(w / 2, theme.S(120), text=self.name,
                       fill=p.text_secondary,
                       font=theme.font(self.win, theme.TS["small"]))

        # 进度轨道。用 border 而不是 bg_subtle：轨道落在渐变的下端，
        # 那里本来就接近 bg_subtle，用 bg_subtle 画等于看不见。
        W.round_rect(cv, self._bar_x, self._bar_y,
                     self._bar_x + self._bar_w, self._bar_y + self._bar_h,
                     self._bar_h / 2, fill=p.border, outline="")

    def _halo(self, cx: float, cy: float) -> None:
        """logo 后面一层极淡的品牌色光晕（同心圆递减）。

        没有直接调 `widgets.glow()`：那个函数拿 `bg_app` 当底色调和，而这里的
        底色是一块渐变，会在 logo 周围留下一圈和渐变对不上的平色。所以按
        logo 所在高度取渐变的局部颜色当底，色带能藏进渐变里。
        """
        p = self.pal
        base = theme.mix(p.splash_top, p.splash_bottom,
                         (cy / max(1, self._h)))
        r = theme.S(44)
        # (半径占比, 与 glow 的混合比)：由外向内逐渐加深。最外圈只混 5%，
        # 肉眼看到的是「logo 周围有一点空气」，而不是「背后压着一个圆盘」。
        # 第一版混到 40%，浅色下是一块直径 116px 的薄荷绿盘子，把主体盖住了。
        for frac, k in ((1.0, 0.05), (0.78, 0.09), (0.56, 0.13), (0.34, 0.17)):
            rr = r * frac
            col = theme.mix(base, p.glow, k)
            self.cv.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                                fill=col, outline=col, tags="bg")

    # ------------------------------------------------------------ 进度

    def set_progress(self, frac: float) -> None:
        """把已完成段画到 frac。只前进不后退，增量绘制。

        增量是有意的：进度线是横向渐变，一次性重画 400 条 1px 竖线在
        60fps 下会拖慢启动；而进度只会增长，所以每次只补新露出来的那一段。
        """
        if not self.alive:
            return
        frac = max(0.0, min(1.0, frac))
        self._frac = frac
        target = self._bar_w * frac
        if target <= self._drawn:
            return
        cv = self.cv
        y1 = self._bar_y
        y2 = self._bar_y + self._bar_h
        x = int(self._drawn)
        stop = int(target)
        while x < stop:
            col = theme.mix(self.pal.brand, self.pal.brand_ink,
                            x / self._bar_w)
            cv.create_line(self._bar_x + x, y1, self._bar_x + x, y2,
                           fill=col, tags="fill")
            x += 1
        self._drawn = target

    def animate_to(self, frac: float, ms: int | None = None) -> None:
        """把进度线补间到 frac。默认用 `MOTION.progress`（400ms）。"""
        start = self._frac
        span = frac - start
        if span <= 0:
            return
        anim.tween(self.cv, "progress", ms or MOTION.progress,
                   lambda t: self.set_progress(start + span * t),
                   ease=anim.ease_out_cubic, interval=MOTION.interval)

    # ------------------------------------------------------------ 生命周期

    def show(self) -> None:
        """把窗口显示出来。调用方随后应 `update()` 一次让它真的画到屏幕上。"""
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
        except tk.TclError:
            pass

    def fade_in(self) -> None:
        if anim.reduced():
            self._alpha(1.0)
            return
        anim.tween(self.win, "alpha", MOTION.splash_in,
                   lambda t: self._alpha(0.3 + 0.7 * t),
                   ease=anim.ease_out_cubic, interval=MOTION.interval)

    def on_user_input(self, callback) -> None:
        """用户按下除 Esc 以外的键 / 点了启动层时回调。编排层据此放弃预热。"""
        self._on_input = callback

    def _user_input(self, _event=None) -> None:
        if self._on_input is not None:
            self._on_input()

    def skip(self) -> None:
        """Esc：跳过剩余预热，直接进交接。

        `_fast` 让 `finish()` 不再补那段 180ms 的进度——用户按 Esc 就是为了
        快点进去，再让他看进度条走完最后 10% 是反的。淡出保留：那是交接
        本身，硬切会闪一下。
        """
        self._fast = True
        self.finish()

    def finish(self) -> None:
        """收尾：进度走满 → 淡出 → 交接。

        减弱动效时不走这一套：直接 100%、停 300ms、交接（5.3 第 7 条）。
        """
        if not self.alive:
            return
        self.alive = False

        if anim.reduced():
            self.set_progress(1.0)
            self._after(300, self._handoff)
            return

        base = self._frac

        def to_full(t: float) -> None:
            self.set_progress(base + (1.0 - base) * t)

        def fade() -> None:
            anim.tween(self.win, "alpha", MOTION.splash_out,
                       lambda t: self._alpha(1.0 - t),
                       ease=anim.ease_out_cubic,
                       interval=MOTION.interval,
                       on_done=self._handoff)

        if self._fast:
            self.set_progress(1.0)
            fade()
            return

        # 180ms 走完剩下的进度：比 MOTION.progress 短，因为这段是「赶时间」，
        # 不是「按页推进」。走完再淡出，用户看到的是「满了 → 消失」。
        anim.tween(self.cv, "progress", 180, to_full,
                   ease=anim.ease_out_quint, interval=MOTION.interval,
                   on_done=fade)

    def _handoff(self) -> None:
        self.alive = False
        # 先掐掉补间，再把销毁**延后**交给 root。
        #
        # 不能在这里直接 destroy：启动层上还挂着进度线的补间帧，那些 `after`
        # 回调已经排在队列里。窗口一销毁，tkinter 在回调收尾时会去清理自己
        # 注册的 Tcl 命令，而 `_tclCommands` 已随窗口销毁置空——抛
        # AttributeError，事件循环就废了（实测表现为「交接后界面卡死」）。
        # 120ms 足够把残留帧跑完，那时再销毁就干净了。
        anim.stop(self.cv, "progress")
        anim.stop(self.win, "alpha")
        cb = self._on_handoff
        self._on_handoff = None
        try:
            self.root.after(120, self._destroy)
        except tk.TclError:
            self._destroy()
        if cb is not None:
            cb()

    def _destroy(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def destroy(self) -> None:
        """不交接，直接拆掉（出错兜底用）。"""
        self.alive = False
        anim.stop(self.cv, "progress")
        anim.stop(self.win, "alpha")
        self._destroy()

    # ------------------------------------------------------------ 内部

    def _after(self, ms: int, fn) -> None:
        try:
            self.win.after(ms, fn)
        except tk.TclError:
            pass

    def _alpha(self, value: float) -> None:
        try:
            self.win.attributes("-alpha", max(0.0, min(1.0, value)))
        except tk.TclError:
            pass


def progress_for(built: int, total: int) -> float:
    """建完 `built` 页时进度线该在哪。

    T1 的区间是 40%→90%（P1 5.2）：40% 之前是 T0 的淡入 + 建壳，
    留 10% 给「走满」，这样进度线永远在动，不会停在 100% 等人。
    """
    if total <= 0:
        return 0.9
    return 0.4 + 0.5 * min(1.0, built / total)
