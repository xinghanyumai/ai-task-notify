"""自绘控件库。

Tkinter 原生控件做不出圆角、投影和克制的交互反馈，所以这里用 Canvas
手工绘制一套。约定：

- 每个控件构造时都要拿到当前调色板 `pal` 和父容器底色 `bg`
  （Canvas 不能真透明，必须知道「自己压在哪一层上」）
- 尺寸一律走 `theme.S()` 换算，保证高 DPI 下比例正确
- 所有状态切换（hover / 按下 / 选中）都走 anim.tween 做颜色过渡，
  而不是瞬间跳变——这是「手感」和「廉价感」的分界线
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from . import anim, theme
from .theme import RADIUS_LG, RADIUS_MD, RADIUS_PILL, RADIUS_SM, S
from .tokens import DIM, MOTION, SP, STK

# 列表行圆角比卡片小一档，单独给个名字，免得和 RADIUS_XL 混淆
RADIUS_LG_ROW = 10


# ---------------------------------------------------------------- 绘制工具


def round_rect(cv: tk.Canvas, x1, y1, x2, y2, r, **kw):
    """用平滑多边形画圆角矩形（Tk 没有原生圆角）。"""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return cv.create_polygon(pts, smooth=True, **kw)


def round_rect_grad(cv: tk.Canvas, x1, y1, x2, y2, r, c_top: str, c_bottom: str,
                    tags: str = ""):
    """圆角矩形 + 垂直渐变。

    Tk 的 create_polygon 不支持渐变填充，这里按行扫描：每一行先算出
    该 y 处的左右边界（圆角段用圆的弦长收缩），再画一条 1px 细线。
    30px 高的按钮只有 30 次绘制，开销可以忽略，但观感比纯色高级很多。
    """
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    h = max(1.0, y2 - y1)
    steps = max(2, int(h))
    dy = h / steps
    for i in range(steps):
        y = y1 + i * dy
        inset = 0.0
        if y < y1 + r:
            d = r - (y - y1)
            inset = r - max(0.0, r * r - d * d) ** 0.5
        elif y > y2 - r:
            d = r - (y2 - y)
            inset = r - max(0.0, r * r - d * d) ** 0.5
        col = theme.mix(c_top, c_bottom, i / max(1, steps - 1))
        cv.create_line(x1 + inset, y + dy / 2, x2 - inset, y + dy / 2,
                       fill=col, width=max(1, int(round(dy)) + 1),
                       tags=tags or ())


def draw_shadow(cv: tk.Canvas, x1, y1, x2, y2, r, pal: theme.Palette,
                depth: int | None = None, tags: str = ""):
    """两层偏移圆角矩形模拟柔和投影（Tk 没有高斯模糊）。

    由外到内、由淡到浓各画一层，再把卡片本体盖上去，
    视觉上就是「卡片浮在页面上」而不是「贴了一张白纸」。
    """
    d = depth if depth is not None else S(4)
    for i, col in ((2, pal.shadow_far), (1, pal.shadow_near)):
        off = d * i / 2
        round_rect(cv, x1 + S(1), y1 + off * 0.5, x2 - S(1), y2 + off,
                   r, fill=col, outline=col, tags=tags or ())


# ---------------------------------------------------------------- 图标

# 统一 0~1 归一化坐标，乘 size 得到像素，保证任意尺寸下比例一致
def draw_icon(cv: tk.Canvas, name: str, x: float, y: float, size: float,
              color: str, width: float = 1.6):
    """手绘线性图标。x/y 是图标左上角，size 是外框边长。"""
    s = size
    w = max(1, int(round(width)))
    opt = {"fill": color, "width": w, "capstyle": "round", "joinstyle": "round"}

    def P(u: float, v: float) -> tuple[float, float]:
        return x + s * u, y + s * v

    if name == "bell":
        cv.create_arc(*P(0.18, 0.12), *P(0.82, 0.76), start=0, extent=180,
                      style="arc", outline=color, width=w)
        cv.create_line(*P(0.18, 0.44), *P(0.18, 0.74), **opt)
        cv.create_line(*P(0.82, 0.44), *P(0.82, 0.74), **opt)
        cv.create_line(*P(0.10, 0.74), *P(0.90, 0.74), **opt)
        cv.create_line(*P(0.50, 0.07), *P(0.50, 0.15), **opt)
        cv.create_arc(*P(0.39, 0.74), *P(0.61, 0.90), start=180, extent=180,
                      style="arc", outline=color, width=w)

    elif name == "bolt":
        cv.create_polygon(
            [*P(0.56, 0.05), *P(0.22, 0.55), *P(0.46, 0.55),
             *P(0.40, 0.95), *P(0.78, 0.41), *P(0.54, 0.41)],
            fill="", outline=color, width=w, joinstyle="round",
        )

    elif name == "doc":
        cv.create_line(*P(0.26, 0.08), *P(0.60, 0.08), *P(0.78, 0.27),
                       *P(0.78, 0.92), *P(0.26, 0.92), *P(0.26, 0.08), **opt)
        cv.create_line(*P(0.60, 0.08), *P(0.60, 0.27), *P(0.78, 0.27), **opt)
        for i, frac in enumerate((0.48, 0.62, 0.76)):
            cv.create_line(*P(0.38, frac),
                           *P(0.66 if i < 2 else 0.56, frac), **opt)

    elif name == "file":
        cv.create_line(*P(0.28, 0.08), *P(0.58, 0.08), *P(0.74, 0.26),
                       *P(0.74, 0.92), *P(0.28, 0.92), *P(0.28, 0.08), **opt)
        cv.create_line(*P(0.58, 0.08), *P(0.58, 0.26), *P(0.74, 0.26), **opt)

    elif name == "chart":
        cv.create_line(*P(0.14, 0.88), *P(0.88, 0.88), **opt)
        for left, top in ((0.24, 0.52), (0.45, 0.28), (0.66, 0.60)):
            cv.create_line(*P(left, 0.86), *P(left, top), **opt)
        cv.create_line(*P(0.24, 0.52), *P(0.45, 0.28), *P(0.66, 0.60), **opt)

    elif name == "check":
        cv.create_line(*P(0.20, 0.52), *P(0.42, 0.74), *P(0.80, 0.26), **opt)

    elif name == "chevron":
        cv.create_line(*P(0.40, 0.26), *P(0.64, 0.50), *P(0.40, 0.74), **opt)

    elif name == "chat":
        cv.create_line(*P(0.16, 0.30), *P(0.16, 0.14), *P(0.84, 0.14),
                       *P(0.84, 0.66), *P(0.44, 0.66), *P(0.28, 0.84),
                       *P(0.28, 0.66), *P(0.16, 0.66), *P(0.16, 0.30), **opt)
        for fx in (0.36, 0.50, 0.64):
            cv.create_oval(*P(fx - 0.03, 0.37), *P(fx + 0.03, 0.43),
                           fill=color, outline=color)

    elif name == "mail":
        cv.create_line(*P(0.12, 0.22), *P(0.88, 0.22), *P(0.88, 0.78),
                       *P(0.12, 0.78), *P(0.12, 0.22), **opt)
        cv.create_line(*P(0.12, 0.26), *P(0.50, 0.56), *P(0.88, 0.26), **opt)

    elif name == "link":
        cv.create_line(*P(0.42, 0.58), *P(0.58, 0.42), **opt)
        cv.create_arc(*P(0.10, 0.40), *P(0.52, 0.82), start=110, extent=200,
                      style="arc", outline=color, width=w)
        cv.create_arc(*P(0.48, 0.18), *P(0.90, 0.60), start=290, extent=200,
                      style="arc", outline=color, width=w)

    elif name == "phone":
        cv.create_line(*P(0.34, 0.10), *P(0.66, 0.10), *P(0.66, 0.90),
                       *P(0.34, 0.90), *P(0.34, 0.10), **opt)
        cv.create_line(*P(0.44, 0.80), *P(0.56, 0.80), **opt)

    elif name == "briefcase":
        cv.create_line(*P(0.12, 0.34), *P(0.88, 0.34), *P(0.88, 0.86),
                       *P(0.12, 0.86), *P(0.12, 0.34), **opt)
        cv.create_line(*P(0.36, 0.34), *P(0.36, 0.18), *P(0.64, 0.18),
                       *P(0.64, 0.34), **opt)

    elif name == "info":
        cv.create_oval(*P(0.12, 0.12), *P(0.88, 0.88), outline=color, width=w)
        cv.create_line(*P(0.50, 0.44), *P(0.50, 0.70), **opt)
        cv.create_oval(*P(0.46, 0.28), *P(0.54, 0.36), fill=color, outline=color)

    elif name == "alert":
        cv.create_line(*P(0.50, 0.10), *P(0.94, 0.86), *P(0.06, 0.86),
                       *P(0.50, 0.10), **opt)
        cv.create_line(*P(0.50, 0.38), *P(0.50, 0.62), **opt)
        cv.create_oval(*P(0.46, 0.68), *P(0.54, 0.76), fill=color, outline=color)

    elif name == "key":
        cv.create_oval(*P(0.10, 0.34), *P(0.46, 0.70), outline=color, width=w)
        cv.create_line(*P(0.42, 0.62), *P(0.90, 0.16), **opt)
        cv.create_line(*P(0.72, 0.34), *P(0.86, 0.48), **opt)

    elif name == "book":
        cv.create_line(*P(0.50, 0.22), *P(0.50, 0.88), **opt)
        cv.create_line(*P(0.50, 0.22), *P(0.14, 0.14), *P(0.14, 0.80),
                       *P(0.50, 0.88), **opt)
        cv.create_line(*P(0.50, 0.22), *P(0.86, 0.14), *P(0.86, 0.80),
                       *P(0.50, 0.88), **opt)


def draw_badge(cv: tk.Canvas, x: float, y: float, size: float, icon: str,
               fill: str, fg: str, radius: float | None = None,
               icon_scale: float = 0.54):
    """圆角方块图标徽章（列表行左侧那个小方块）。"""
    r = radius if radius is not None else size * 0.3
    round_rect(cv, x, y, x + size, y + size, r, fill=fill, outline=fill)
    inner = size * icon_scale
    draw_icon(cv, icon, x + (size - inner) / 2, y + (size - inner) / 2,
              inner, fg, max(1.4, size * 0.055))


# ---------------------------------------------------------------- 键盘可达

def draw_focus_ring(cv: tk.Canvas, x1, y1, x2, y2, r, pal: theme.Palette,
                    tags: str = "focusring", color: str | None = None):
    """键盘焦点环。

    自绘控件默认拿不到焦点，Tab 走一圈什么反馈都没有——这是自绘 UI
    最常见的可访问性缺口。这里统一用 2px 品牌色描边表达焦点，
    颜色用 border_focus（白底 3.47:1，满足非文本 3:1 要求）。
    """
    round_rect(cv, x1, y1, x2, y2, r, fill="", outline=color or pal.border_focus,
               width=STK.ring, tags=tags)


def enable_focus(widget, redraw: Callable[[], None],
                 activate: Callable[[], None] | None = None) -> None:
    """给自绘控件装上 Tab 焦点 + 空格/回车激活。

    activate 传 None 表示「可以聚焦但不可激活」（如纯展示行）。
    """
    widget._focused = False
    try:
        widget.configure(takefocus=1)
    except Exception:
        pass

    def fin(_e=None):
        widget._focused = True
        redraw()

    def fout(_e=None):
        widget._focused = False
        redraw()

    def key(_e=None):
        if activate:
            activate()
        return "break"

    widget.bind("<FocusIn>", fin)
    widget.bind("<FocusOut>", fout)
    if activate:
        widget.bind("<Return>", key)
        widget.bind("<space>", key)


def has_focus(widget) -> bool:
    return bool(getattr(widget, "_focused", False))


def elide(cv: tk.Canvas, font, text: str, max_w: int) -> str:
    """把文字裁到 max_w 像素以内，末尾补省略号。

    窄窗口下自绘控件里的文字不会自动折行也不会自动省略，会直接溢出到
    相邻元素上（比如列表副标题压住状态点）。所以凡是有可用宽度的自绘
    文本都走这里。
    """
    if not text or max_w <= 0:
        return ""
    if cv.tk.call("font", "measure", font, text) <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if cv.tk.call("font", "measure", font, text[:mid] + "…") <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo] + "…") if lo > 0 else "…"



# ---------------------------------------------------------------- 按钮


class Button(tk.Canvas):
    """圆角按钮。variant: primary / secondary / ghost / danger。

    primary 用垂直渐变 + 底部一档深色描边，做出「有厚度」的观感；
    hover / 按下都在 120ms 内插值，不跳色。
    """

    def __init__(
        self,
        master,
        text: str,
        pal: theme.Palette,
        command: Callable | None = None,
        variant: str = "secondary",
        icon: str | None = None,
        height: int | None = None,
        min_width: int = 0,
        font_size: int | None = None,
        bold: bool = False,
        bg: str | None = None,
        pad_x: int | None = None,
    ):
        self.pal = pal
        self.text = text
        self.variant = variant
        self.icon = icon
        self.command = command
        # height / font_size / pad_x 允许覆盖，但传进来的值必须已经过 DPI 换算
        # （和 Section.gap 同一个约定）。默认值全部取自令牌，所以「没传」这件事
        # 不会退化成某个散落的字面量。
        self.h = height if height is not None else DIM.BTN_H
        self.font_size = (font_size if font_size is not None
                          else theme.TS["small"])
        self.bold = bold
        self._enabled = True
        self._loading = False
        self._loading_text = "处理中…"
        self._spin = 0
        self._spin_job = None
        self._hover = False
        self._press = False
        self._hv = 0.0
        self._bg = bg or master.cget("bg")
        self._pad_x = pad_x if pad_x is not None else SP.lg

        f = theme.font(master, self.font_size, "bold" if bold else "normal")
        probe = tk.Canvas(master, width=1, height=1)
        tw = probe.tk.call("font", "measure", f, text) if text else 0
        probe.destroy()
        iw = DIM.ICON if icon else 0
        gap = SP.xs if icon and text else 0
        self.w = max(min_width, int(tw) + self._pad_x * 2 + iw + gap)

        super().__init__(master, width=self.w, height=self.h, bg=self._bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._colors = self._resolve_colors()
        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda e: self._draw())
        enable_focus(self, self._draw, self._activate)

    def _activate(self):
        if self._enabled and self.command:
            self.command()

    # -- 配色 --------------------------------------------------------
    def _resolve_colors(self) -> dict:
        p = self.pal
        if self.variant == "primary":
            return {"fill": p.brand, "fg": p.on_brand, "hover": p.brand_hover,
                    "active": p.brand_active, "outline": ""}
        if self.variant == "danger":
            return {"fill": p.danger_soft, "fg": p.danger, "hover": p.danger_soft,
                    "active": p.danger_soft, "outline": ""}
        if self.variant == "ghost":
            return {"fill": self._bg, "fg": p.text_secondary, "hover": p.bg_hover,
                    "active": p.bg_active, "outline": ""}
        return {"fill": p.bg_card, "fg": p.text, "hover": p.bg_hover,
                "active": p.bg_active, "outline": p.border}

    def _fill_now(self) -> str:
        c = self._colors
        base = c["fill"]
        if not self._enabled:
            return base
        target = c["active"] if self._press else (c["hover"] if self._hover else base)
        return theme.mix(base, target, self._hv if self._hover or self._press else 0.0)

    # -- 绘制 --------------------------------------------------------
    def _draw(self) -> None:
        self.delete("all")
        w = self.winfo_width() or self.w
        h = self.winfo_height() or self.h
        if w <= 1:
            w = self.w
        if h <= 1:
            h = self.h

        # 按下时整体下移 1px，模拟被压下去
        dy = S(1) if self._press else 0
        fill = self._fill_now()
        outline = self._colors["outline"] if self._enabled else self.pal.border
        fg = self._colors["fg"] if self._enabled else self.pal.text_tertiary
        r = S(RADIUS_MD)

        if self.variant == "primary" and self._enabled:
            top = theme.mix(self.pal.brand_top, "#ffffff", self._hv * 0.35)
            bot = theme.mix(self.pal.brand_bottom, "#ffffff", self._hv * 0.35)
            if self._press:
                top, bot = self.pal.brand_active, self.pal.brand_active
            round_rect_grad(self, S(1), S(1) + dy, w - S(1), h - S(2) + dy, r, top, bot)
        else:
            round_rect(self, S(1), S(1) + dy, w - S(1), h - S(1) + dy, r,
                       fill=fill, outline=outline or fill, width=STK.control)

        f = theme.font(self, self.font_size, "bold" if self.bold else "normal")
        cx = w / 2
        cy = h / 2 + dy
        if self._loading:
            self._paint_loading(cx, cy, fg, f)
        elif self.icon and self.text:
            icon_w = DIM.ICON
            tw = self.tk.call("font", "measure", f, self.text)
            total = icon_w + SP.xs + tw
            left = cx - total / 2
            draw_icon(self, self.icon, left, cy - icon_w / 2, icon_w, fg, STK.icon_thin)
            self.create_text(left + icon_w + SP.xs, cy, text=self.text, anchor="w",
                             fill=fg, font=f)
        elif self.text:
            self.create_text(cx, cy, text=self.text, fill=fg, font=f)

        if has_focus(self) and self._enabled:
            # 主按钮的填充本身就是品牌绿，环色再取 border_focus 就等于没画。
            # 这里让环色跟随「被压在什么底色上」：绿色填充上用深色环，
            # 浅色填充上用品牌环。同时往里缩 3px，环才读得出来是一圈。
            ring = (self.pal.on_brand if self.variant == "primary"
                    else self.pal.border_focus)
            draw_focus_ring(self, S(3), S(3) + dy, w - S(3), h - S(4) + dy,
                            max(S(4), r - S(2)), self.pal, color=ring)

    # -- 交互 --------------------------------------------------------
    def _tween_hover(self, target: float) -> None:
        start = self._hv

        def frame(t):
            self._hv = start + (target - start) * t
            self._draw()

        anim.tween(self, "hover", MOTION.quick, frame, anim.ease_out_cubic,
                   interval=MOTION.interval)

    def _on_enter(self, _e=None):
        if not self._enabled:
            return
        self._hover = True
        self._tween_hover(1.0)

    def _on_leave(self, _e=None):
        self._hover = False
        self._press = False
        self._tween_hover(0.0)

    def _on_press(self, _e=None):
        if not self._enabled:
            return
        self._press = True
        self._draw()

    def _on_release(self, e=None):
        if not self._enabled:
            return
        inside = 0 <= (e.x if e else 0) <= (self.winfo_width() or self.w)
        self._press = False
        self._draw()
        if inside and self.command:
            self.after(8, self.command)

    # -- 外部接口 ----------------------------------------------------
    def set_enabled(self, on: bool) -> None:
        # 幂等：状态没变就不重绘。这个方法是「随输入实时求值」调用的，
        # 每敲一个键都会走一遍，无条件重绘等于每键多花十几个 Tcl 调用。
        if on == self._enabled:
            return
        self._enabled = on
        self.configure(cursor="hand2" if on else "arrow")
        self._draw()

    def set_text(self, text: str) -> None:
        self.text = text
        self._draw()

    def set_variant(self, variant: str) -> None:
        self.variant = variant
        self._colors = self._resolve_colors()
        self._draw()

    # -- 加载态 ------------------------------------------------------
    def set_loading(self, on: bool, text: str | None = None) -> None:
        """转圈 + 禁用，直到调用方拿到结果。

        为什么加载态必须挂在按钮上、而不是只弹一条 toast：网络慢的时候
        toast 早就自己消失了，人看不到「还在跑」这件事，只会觉得点了没反应。
        按钮是「刚被点的那个东西」，状态留在原地最容易被看见。

        为什么用定时重绘而不是补间：加载的时长由网络决定，没有终点可补。
        系统开了减弱动效时不转，画一段静止的弧——按钮仍然是灰的、文字仍然
        写着「发送中」，状态可读，只是不动。
        """
        if on == self._loading and (text or "处理中…") == self._loading_text:
            return
        self._loading = bool(on)
        self._loading_text = text or "处理中…"
        # 加载中不接受点击：重复点会并发发起同一个请求
        self._enabled = not on
        self._hover = self._press = False
        self._spin = 0
        try:
            self.configure(cursor="arrow" if on else "hand2")
        except tk.TclError:
            return
        self._draw()
        if on:
            self._tick()
        elif self._spin_job is not None:
            try:
                self.after_cancel(self._spin_job)
            except Exception:
                pass
            self._spin_job = None

    def _tick(self) -> None:
        if not self._loading:
            self._spin_job = None
            return
        if anim.reduced():
            # 减弱动效：不转，弧停在固定角度
            self._spin_job = None
            return
        self._spin = (self._spin + 30) % 360
        try:
            self._draw()
            self._spin_job = self.after(MOTION.interval * 3, self._tick)
        except tk.TclError:
            self._spin_job = None

    def _paint_loading(self, cx: float, cy: float, fg: str, f) -> None:
        tw = self.tk.call("font", "measure", f, self._loading_text)
        d = DIM.ICON
        total = d + SP.xs + int(tw)
        left = cx - total / 2
        self.create_arc(left, cy - d / 2, left + d, cy + d / 2,
                        start=self._spin, extent=270, style="arc",
                        outline=fg, width=STK.ring)
        self.create_text(left + d + SP.xs, cy, text=self._loading_text,
                         anchor="w", fill=fg, font=f)


# ---------------------------------------------------------------- 卡片


# ---------------------------------------------------------------- 开关


class Switch(tk.Canvas):
    """胶囊开关。滑块用 ease-out-back 滑过去，轨道颜色同步插值。"""

    def __init__(self, master, pal: theme.Palette, value: bool = False,
                 command: Callable[[bool], None] | None = None,
                 bg: str | None = None, width: int | None = None,
                 height: int | None = None):
        self.pal = pal
        self.value = bool(value)
        self.command = command
        self._bg = bg or master.cget("bg")
        self.w = width if width is not None else DIM.SWITCH_W
        self.h = height if height is not None else DIM.SWITCH_H
        self._pos = 1.0 if self.value else 0.0
        super().__init__(master, width=self.w, height=self.h + S(2), bg=self._bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._draw()
        self.bind("<Button-1>", self._toggle)
        enable_focus(self, self._draw, self._toggle)

    def _draw(self) -> None:
        self.delete("all")
        p = self.pal
        t = self._pos
        track = theme.mix(p.track, p.brand, t)
        y1 = S(1)
        y2 = self.h + S(1)
        # 关态轨道是浅灰压在更浅的页面底上，几乎看不出形状。
        # 加一圈 border_control 描边，开关的「可点区域」才读得出来。
        round_rect(self, 1, y1, self.w - 1, y2, (y2 - y1) / 2,
                   fill=track, outline=theme.mix(p.border_control, track, t),
                   width=STK.control)

        knob = self.h - S(4)
        travel = self.w - knob - S(4)
        x = S(2) + travel * t
        cy = (y1 + y2) / 2
        # 滑块投影：往下 1px 压一档暗色，滑块才不像贴纸
        self.create_oval(x + S(0.5), cy - knob / 2 + S(1.2),
                         x + knob + S(0.5), cy + knob / 2 + S(1.2),
                         fill=theme.mix(p.track, "#000000", 0.22), outline="")
        self.create_oval(x, cy - knob / 2, x + knob, cy + knob / 2,
                         fill="#ffffff", outline="")

        if has_focus(self):
            draw_focus_ring(self, S(0.5), y1 - S(2.5), self.w - S(0.5),
                            y2 + S(2.5), (y2 - y1) / 2 + S(2), p)

    def _toggle(self, _e=None):
        self.value = not self.value
        self.animate(1.0 if self.value else 0.0)
        if self.command:
            self.command(self.value)

    def animate(self, target: float, duration: int | None = None) -> None:
        start = self._pos

        def frame(t):
            self._pos = start + (target - start) * t
            self._draw()

        # 开关滑块是唯一用 ease_out_back 的地方：它有物理感，末端要轻微过冲。
        anim.tween(self, "knob", duration or MOTION.base, frame,
                   anim.ease_out_back, interval=MOTION.interval)

    def set_value(self, v: bool, animate: bool = False) -> None:
        self.value = bool(v)
        if animate:
            self.animate(1.0 if self.value else 0.0)
        else:
            anim.stop(self, "knob")
            self._pos = 1.0 if self.value else 0.0
            self._draw()


# ---------------------------------------------------------------- 复选框


class Checkbox(tk.Canvas):
    """方形勾选框，选中时填充品牌色并画白色勾。"""

    def __init__(self, master, pal: theme.Palette, text: str, value: bool = False,
                 command: Callable[[bool], None] | None = None,
                 bg: str | None = None, font_size: int = 9,
                 text_color: str | None = None):
        self.pal = pal
        self.value = bool(value)
        self.command = command
        self.label = text
        self._bg = bg or master.cget("bg")
        self.font_size = font_size
        self.text_color = text_color
        self.box = DIM.CHECK_BOX
        self._hover = False
        self._hv = 0.0

        f = theme.font(master, font_size)
        probe = tk.Canvas(master, width=1, height=1)
        tw = probe.tk.call("font", "measure", f, text) if text else 0
        probe.destroy()
        w = self.box + S(10) + int(tw) + S(4)

        super().__init__(master, width=w, height=self.box + S(6), bg=self._bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._draw()
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        enable_focus(self, self._draw, self._toggle)

    def _draw(self) -> None:
        self.delete("all")
        p = self.pal
        top = S(3)
        if self.value:
            fill = p.brand
            outline = p.brand
        else:
            fill = theme.mix(p.bg_input, p.bg_hover, self._hv)
            outline = theme.mix(p.border_control, p.border_focus, self._hv)

        round_rect(self, 0.5, top + 0.5, self.box - 0.5, top + self.box - 0.5,
                   S(RADIUS_SM), fill=fill, outline=outline, width=STK.control)
        if self.value:
            draw_icon(self, "check", S(2), top + S(2), self.box - S(4),
                      p.on_brand, STK.icon_bold)
        self.create_text(self.box + S(10), top + self.box / 2, anchor="w",
                         text=self.label, fill=self.text_color or p.text,
                         font=theme.font(self, self.font_size))
        if has_focus(self):
            draw_focus_ring(self, 0.5, top - S(1.5), self.winfo_width() - S(0.5),
                            top + self.box + S(1.5), S(6), p)

    def _set_hover(self, on: bool) -> None:
        self._hover = on
        target = 1.0 if on else 0.0
        start = self._hv

        def frame(t):
            self._hv = start + (target - start) * t
            self._draw()

        anim.tween(self, "hover", MOTION.quick, frame, anim.ease_out_cubic,
                   interval=MOTION.interval)

    def _toggle(self, _e=None):
        self.value = not self.value
        self._draw()
        if self.command:
            self.command(self.value)

    def set_value(self, v: bool) -> None:
        self.value = bool(v)
        self._draw()


# ---------------------------------------------------------------- 导航项


class NavItem(tk.Canvas):
    """左侧导航项：选中时品牌浅底 + 左侧竖条 + 图标文字转品牌色。"""

    def __init__(self, master, pal: theme.Palette, key: str, label: str, icon: str,
                 command: Callable[[str], None], bg: str | None = None,
                 height: int | None = None, badge: str = ""):
        self.pal = pal
        self.key = key
        self.label = label
        self.icon_name = icon
        self.command = command
        self._bg = bg or master.cget("bg")
        self.selected = False
        self._hover = False
        self._hv = 0.0
        self.badge = badge
        self.h = height if height is not None else DIM.NAV_ITEM_H
        super().__init__(master, height=self.h, bg=self._bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._draw()
        self.bind("<Button-1>", lambda e: self.command(self.key))
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Configure>", lambda e: self._draw())
        enable_focus(self, self._draw, lambda: self.command(self.key))

    def _draw(self) -> None:
        self.delete("all")
        p = self.pal
        w = self.winfo_width() or S(200)
        if w <= 1:
            w = S(200)
        x1, y1, x2, y2 = S(2), S(2), w - S(6), self.h - S(2)

        if self.selected:
            # 选中态只做浅底 + 文字转深品牌色。「你在哪」由左侧竖脊上的
            # 实心节点表达，这里不再重复画竖条——同一个信息说两遍是噪音。
            round_rect(self, x1, y1, x2, y2, S(RADIUS_MD),
                       fill=p.brand_soft, outline=p.brand_soft)
            fg = p.brand_ink
            weight = "bold"
        else:
            if self._hv > 0.01:
                round_rect(self, x1, y1, x2, y2, S(RADIUS_MD),
                           fill=theme.mix(p.sidebar, p.bg_hover, self._hv),
                           outline=theme.mix(p.sidebar, p.bg_hover, self._hv))
            fg = theme.mix(p.text_secondary, p.text, self._hv)
            weight = "normal"

        ic = S(16)
        ix = S(12)
        iy = (self.h - ic) / 2
        draw_icon(self, self.icon_name, ix, iy, ic, fg, STK.icon_thin)
        self.create_text(ix + ic + S(11), self.h / 2, anchor="w", text=self.label,
                         fill=fg, font=theme.font(self, theme.TS["body"], weight))
        if self.badge:
            bw = max(S(20), S(7) * len(self.badge))
            round_rect(self, w - S(14) - bw, self.h / 2 - S(8),
                       w - S(14), self.h / 2 + S(8), S(RADIUS_PILL),
                       fill=p.brand, outline=p.brand)
            self.create_text(w - S(14) - bw / 2, self.h / 2, text=self.badge,
                             fill=p.on_brand,
                             font=theme.font(self, theme.TS["micro"], "bold"))

        if has_focus(self):
            draw_focus_ring(self, x1, y1, x2, y2, S(RADIUS_MD), p)

    def _set_hover(self, on: bool) -> None:
        if self._hover == on:
            return
        self._hover = on
        start = self._hv
        target = 1.0 if on else 0.0

        def frame(t):
            self._hv = start + (target - start) * t
            self._draw()

        anim.tween(self, "hover", MOTION.quick, frame, anim.ease_out_cubic,
                   interval=MOTION.interval)

    def set_selected(self, on: bool) -> None:
        if self.selected != on:
            self.selected = on
            self._draw()

    def set_badge(self, text: str) -> None:
        if self.badge != text:
            self.badge = text
            self._draw()


# ---------------------------------------------------------------- 分段控件


class Segmented(tk.Canvas):
    """分段选择器，选中块平滑滑动。"""

    def __init__(self, master, pal: theme.Palette, options: list[tuple[str, str]],
                 value: str, command: Callable[[str], None] | None = None,
                 bg: str | None = None, height: int | None = None,
                 font_size: int | None = None):
        self.pal = pal
        self.options = options
        self.value = value
        self.command = command
        self._bg = bg or master.cget("bg")
        self.h = height if height is not None else DIM.SEG_H
        self.font_size = (font_size if font_size is not None
                          else theme.TS["micro"])
        f = theme.font(master, self.font_size)
        probe = tk.Canvas(master, width=1, height=1)
        widths = [int(probe.tk.call("font", "measure", f, label)) + SP.lg
                  for _, label in options]
        probe.destroy()
        self.seg_w = widths
        self.w = sum(widths) + SP.xs
        super().__init__(master, width=self.w, height=self.h, bg=self._bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        keys = [k for k, _ in options]
        self._idx = keys.index(value) if value in keys else 0
        self._slide = float(self._idx)
        self._hover_idx = -1
        self._draw()
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self._set_hover(-1))
        # 分段控件是纯自绘的，不接键盘的话「切模板类型」「切主题」这两处
        # 键盘用户就完全够不到。方向键切换 + 焦点环。
        enable_focus(self, self._draw)
        self.bind("<Left>", lambda e: self._step(-1))
        self.bind("<Right>", lambda e: self._step(1))
        self.bind("<Up>", lambda e: self._step(-1))
        self.bind("<Down>", lambda e: self._step(1))

    def _seg_bounds(self, i: int) -> tuple[float, float]:
        x = S(2) + sum(self.seg_w[:i])
        return x, x + self.seg_w[i]

    def _draw(self) -> None:
        self.delete("all")
        p = self.pal
        track = p.bg_subtle if p.name == "light" else p.bg_app
        round_rect(self, 0.5, 0.5, self.w - 0.5, self.h - 0.5, S(RADIUS_MD),
                   fill=track, outline=p.border, width=STK.control)

        x1, x2 = self._thumb_bounds(self._slide)
        thumb = p.bg_card if p.name == "light" else p.bg_hover
        # 选中块也带一点点投影，才浮得起来
        round_rect(self, x1, S(3.5), x2, self.h - S(2) + S(1.5), S(RADIUS_SM),
                   fill=p.shadow_near, outline=p.shadow_near)
        round_rect(self, x1, S(2), x2, self.h - S(2), S(RADIUS_SM),
                   fill=thumb, outline=p.border, width=STK.control)

        for i, (_key, label) in enumerate(self.options):
            a, b = self._seg_bounds(i)
            selected = i == self._idx
            color = p.text if selected else (
                p.text_secondary if i == self._hover_idx else p.text_tertiary)
            self.create_text((a + b) / 2, self.h / 2, text=label, fill=color,
                             font=theme.font(self, self.font_size,
                                             "bold" if selected else "normal"))

        if has_focus(self):
            draw_focus_ring(self, S(0.5), S(0.5), self.w - S(0.5),
                            self.h - S(0.5), S(RADIUS_MD), p)

    def _thumb_bounds(self, idx: float) -> tuple[float, float]:
        i = int(idx)
        frac = idx - i
        x1 = S(2) + sum(self.seg_w[:i])
        if frac > 0 and i + 1 < len(self.seg_w):
            x1 += self.seg_w[i] * frac
            width = self.seg_w[i] * (1 - frac) + self.seg_w[i + 1] * frac
        else:
            width = self.seg_w[min(i, len(self.seg_w) - 1)]
        return x1, x1 + width

    def _set_hover(self, idx: int) -> None:
        if idx != self._hover_idx:
            self._hover_idx = idx
            self._draw()

    def _on_motion(self, e):
        idx = -1
        for i in range(len(self.options)):
            a, b = self._seg_bounds(i)
            if a <= e.x <= b:
                idx = i
                break
        self._set_hover(idx)

    def _on_click(self, e):
        for i, (_key, _) in enumerate(self.options):
            a, b = self._seg_bounds(i)
            if a <= e.x <= b:
                self._step(i - self._idx)
                return

    def _step(self, delta: int) -> None:
        i = max(0, min(len(self.options) - 1, self._idx + delta))
        if i == self._idx:
            return
        key = self.options[i][0]
        start = self._slide
        target = float(i)
        self._idx = i
        self.value = key

        def frame(t):
            self._slide = start + (target - start) * t
            self._draw()

        anim.tween(self, "thumb", MOTION.base, frame, anim.ease_out_quint,
                   interval=MOTION.interval)
        if self.command:
            self.command(key)


# ---------------------------------------------------------------- 输入框


class Field(tk.Frame):
    """带圆角边框的输入框。focus 时边框渐变为品牌色。"""

    def __init__(self, master, pal: theme.Palette, value: str = "",
                 placeholder: str = "", show: str | None = None,
                 multiline: bool = False, height: int | None = None,
                 font_size: int | None = None, bg: str | None = None):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        self.placeholder = placeholder
        self.multiline = multiline
        self._focused = False
        self._focus_t = 0.0
        self.h = height if height is not None else DIM.FIELD_H

        super().__init__(master, bg=self._bg)
        self.cv = tk.Canvas(self, bg=self._bg, highlightthickness=0, bd=0,
                            height=self.h)
        self.cv.pack(fill="both", expand=True)

        f = theme.font(self, font_size if font_size is not None
                       else theme.TS["small"])
        if multiline:
            self.entry = tk.Text(self.cv, bd=0, relief="flat", font=f,
                                 bg=pal.bg_input, fg=pal.text,
                                 insertbackground=pal.brand,
                                 highlightthickness=0, wrap="word",
                                 padx=SP.xs, pady=SP.xs, spacing1=1, spacing3=2)
            self.entry.insert("1.0", value)
            self.entry.configure(height=4)
        else:
            self._var = tk.StringVar(value=value)
            self.entry = tk.Entry(self.cv, bd=0, relief="flat", font=f,
                                  bg=pal.bg_input, fg=pal.text,
                                  insertbackground=pal.brand,
                                  highlightthickness=0, show=show or "",
                                  textvariable=self._var)
            self._var.trace_add("write", lambda *a: self._update_placeholder())

        # 单行用 w 锚点（垂直居中）；多行必须用 nw，否则窗口以 y 为中心
        # 向上向下各展开一半，文字会被顶到边框外面去。
        self._win = self.cv.create_window(
            S(11), S(9) if multiline else self.h / 2, window=self.entry,
            anchor="nw" if multiline else "w")
        self._placeholder_id = None
        self._ph_visible = False

        self.cv.bind("<Configure>", self._on_conf)
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self.entry.bind("<KeyRelease>", lambda e: self._update_placeholder())
        # 点画布空白处也能聚焦到输入框
        self.cv.bind("<Button-1>", lambda e: self.entry.focus_set())
        self._update_placeholder()

    def _on_conf(self, e=None):
        """重画圆角背景。

        注意：不能依赖事件对象的宽高——`event_generate("<Configure>")`
        派发的事件 width/height 是 0，会把背景画到画布外面去，
        表现就是「输入框一点就没了」。这里统一回退到 winfo 实测尺寸。
        """
        w = getattr(e, "width", 0) or 0
        h = getattr(e, "height", 0) or 0
        if w <= 1:
            w = self.cv.winfo_width()
        if h <= 1:
            h = self.cv.winfo_height()
        if w <= 1 or h <= 1:
            return

        self.cv.delete("fieldbg")
        t = self._focus_t
        border = theme.mix(self.pal.border_control, self.pal.border_focus, t)
        fill = theme.mix(self.pal.bg_input, self.pal.brand_soft_2, t * 0.6)
        if t > 0.02:
            # 聚焦时加一圈极淡的外发光，边界更清楚
            round_rect(self.cv, -S(1), -S(1), w + S(1), h + S(1), S(RADIUS_MD + 2),
                       fill=theme.mix(self.pal.bg_input, self.pal.brand_soft, t * 0.5),
                       outline="", tags="fieldbg")
        round_rect(self.cv, 0.5, 0.5, w - 0.5, h - 0.5, S(RADIUS_MD),
                   fill=fill, outline=border,
                   width=STK.ring if t > 0.5 else STK.control, tags="fieldbg")
        self.cv.tag_lower("fieldbg")

        inner_w = max(1, w - S(22))
        if self.multiline:
            self.cv.coords(self._win, S(11), S(9))
            self.cv.itemconfigure(self._win, width=inner_w,
                                  height=max(S(60), h - S(18)))
        else:
            self.cv.coords(self._win, S(11), h / 2)
            self.cv.itemconfigure(self._win, width=inner_w)

    def _animate_focus(self, target: float) -> None:
        start = self._focus_t

        def frame(t):
            self._focus_t = start + (target - start) * t
            self._on_conf()

        anim.tween(self, "focus", MOTION.quick, frame, anim.ease_out_cubic,
                   interval=MOTION.interval)

    def _on_focus_in(self, _e=None):
        self._focused = True
        self._animate_focus(1.0)

    def _on_focus_out(self, _e=None):
        self._focused = False
        self._animate_focus(0.0)

    def _update_placeholder(self):
        """占位符只在「空且未隐藏」时显示；状态没变就不重建，避免闪烁。"""
        want = bool(self.placeholder) and not self.get()
        if want == self._ph_visible and (self._placeholder_id or not want):
            return
        if self._placeholder_id:
            self.cv.delete(self._placeholder_id)
            self._placeholder_id = None
        self._ph_visible = want
        if not want:
            return
        if self.multiline:
            x, y, anchor = S(15), S(12), "nw"
        else:
            x, y, anchor = S(13), self.h / 2, "w"
        self._placeholder_id = self.cv.create_text(
            x, y, anchor=anchor, text=self.placeholder,
            fill=self.pal.placeholder, font=theme.font(self, theme.TS["small"]),
        )

    # -- 值接口 ------------------------------------------------------
    def get(self) -> str:
        if self.multiline:
            return self.entry.get("1.0", "end-1c")
        return self.entry.get()

    def set(self, value: str) -> None:
        if self.multiline:
            self.entry.delete("1.0", "end")
            self.entry.insert("1.0", value)
        else:
            self.entry.delete(0, "end")
            self.entry.insert(0, value)
        self._update_placeholder()

    def focus_input(self) -> None:
        try:
            self.entry.focus_set()
            if not self.multiline:
                self.entry.icursor("end")
        except Exception:
            pass

    def bind_change(self, cb: Callable[[], None]) -> None:
        def on_key(_e=None):
            self._update_placeholder()
            cb()

        self.entry.bind("<KeyRelease>", on_key)


# ---------------------------------------------------------------- 滚动容器


class ScrollFrame(tk.Frame):
    """纵向滚动容器。滚动条是自绘的细条，只在内容超出时出现。

    滚轮走补间而不是一格里跳，长表单滚动时不会「咔咔」地顿。
    """

    def __init__(self, master, pal: theme.Palette, bg: str | None = None,
                 bar_width: int = 8):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        self._target = 0.0
        super().__init__(master, bg=self._bg)

        self.bar = tk.Canvas(self, width=S(bar_width), bg=self._bg,
                             highlightthickness=0, bd=0)
        self.bar.pack(side="right", fill="y")

        self.cv = tk.Canvas(self, bg=self._bg, highlightthickness=0, bd=0,
                            yscrollcommand=self._on_scroll)
        self.cv.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self.cv, bg=self._bg)
        self._win = self.cv.create_window(0, 0, window=self.inner, anchor="nw")
        self._wheel_bound = False
        self._bind_wheel()
        self._last_w = -1
        self._drawn_view = (0.0, 1.0)

        self.inner.bind("<Configure>", self._on_inner)
        self.cv.bind("<Configure>", self._on_canvas)
        self.cv.bind("<Enter>", lambda e: self._bind_wheel())
        self.cv.bind("<Leave>", lambda e: self._unbind_wheel())
        self.bar.bind("<Configure>", lambda e: self._draw_bar(*self._last_view))

    def _on_inner(self, _e=None):
        try:
            # 用 inner 的请求高度而不是 bbox("all")：bbox 要遍历画布上所有
            # item，而这里只关心那一个 window item 有多高。
            h = max(self.inner.winfo_reqheight(), self.inner.winfo_height())
            self.cv.configure(scrollregion=(0, 0, self._last_w, h))
        except tk.TclError:
            return
        self._refresh_bar()

    def _on_canvas(self, e):
        # 宽度没变就不用改 inner 的宽度——改了会反过来再触发一次 _on_inner
        if e.width == self._last_w:
            return
        self._last_w = e.width
        self.cv.itemconfigure(self._win, width=e.width)
        self._refresh_bar()

    # -- 滚动条 ------------------------------------------------------
    _last_view = (0.0, 1.0)

    def _refresh_bar(self):
        try:
            first, last = self.cv.yview()
        except Exception:
            return
        self._last_view = (first, last)
        self._draw_bar(first, last)

    def _on_scroll(self, first, last):
        self._last_view = (float(first), float(last))
        self._draw_bar(float(first), float(last))

    def _draw_bar(self, first: float, last: float):
        # 视图没变就不重画：一次重画是 delete + 几个 create，而 <Configure>
        # 会在布局收敛前反复触发，同样的参数画第二遍没有任何意义。
        if (first, last) == self._drawn_view and self.bar.find_all():
            return
        self._drawn_view = (first, last)
        self.bar.delete("all")
        h = self.bar.winfo_height()
        if h <= 1:
            anim.later(self, 40, self._refresh_bar)
            return
        if first <= 0.0 and last >= 1.0:
            return  # 内容没超出，不画
        top = first * h
        bottom = max(last * h, top + S(28))
        bw = self.bar.winfo_width() or S(8)
        color = self.pal.border_strong
        round_rect(self.bar, S(2.5), top + S(2), bw - S(2.5), bottom - S(2),
                   (bw - S(5)) / 2, fill=color, outline=color)

    # -- 滚轮 --------------------------------------------------------
    def _bind_wheel(self, _e=None):
        if self._wheel_bound:
            return
        self._wheel_bound = True
        self.cv.bind_all("<MouseWheel>", self._on_wheel)
        self.cv.bind_all("<Button-4>", self._on_wheel)
        self.cv.bind_all("<Button-5>", self._on_wheel)

    def _unbind_wheel(self, _e=None):
        self._wheel_bound = False
        self.cv.unbind_all("<MouseWheel>")
        self.cv.unbind_all("<Button-4>")
        self.cv.unbind_all("<Button-5>")

    def _on_wheel(self, e):
        if not self._wheel_bound:
            return
        try:
            if getattr(e, "num", None) == 4:
                direction = -1
            elif getattr(e, "num", None) == 5:
                direction = 1
            else:
                direction = -1 if e.delta > 0 else 1
            total = max(1, self.inner.winfo_height())
            cur = self.cv.yview()[0]
            # 上一段补间还没跑完时，从它预期的终点继续，手感才连贯
            if abs(cur - self._target) > 0.08:
                self._target = cur
            step = S(72) * direction / total
            self._target = max(0.0, min(1.0, self._target + step))
            start, target = cur, self._target

            def frame(t):
                self.cv.yview_moveto(start + (target - start) * t)

            anim.tween(self, "scroll", MOTION.base, frame, anim.ease_out_quint,
                       interval=MOTION.interval)
        except Exception:
            pass

    # -- 定位 --------------------------------------------------------
    def to_top(self):
        self._target = 0.0
        self.cv.yview_moveto(0)

    def see(self, widget, margin: int = 8) -> None:
        """滚动到让 widget 完整可见。

        Tk 的 Canvas 没有 Listbox 那种 see()，只能自己算：
        取控件在 inner 里的 y，和当前视口上下沿比较，再按比例换算成
        yview_moveto 需要的 0~1 位置。
        """
        try:
            self.inner.update_idletasks()
            total = self.inner.winfo_height()
            view = self.cv.winfo_height()
            if total <= 1 or view <= 1 or total <= view:
                return
            top = widget.winfo_y()
            bottom = top + widget.winfo_height()
            cur = self.cv.canvasy(0)
            if top - margin < cur:
                target = top - margin
            elif bottom + margin > cur + view:
                target = bottom + margin - view
            else:
                return
            frac = max(0.0, min(1.0, target / total))
            self._target = frac
            start = cur / total

            def frame(t):
                self.cv.yview_moveto(start + (frac - start) * t)

            anim.tween(self, "scroll", MOTION.base, frame, anim.ease_out_quint,
                       interval=MOTION.interval)
        except Exception:
            pass

    # -- 过渡 --------------------------------------------------------
    def slide_in(self, dx: int = 0, dy: int | None = None,
                 duration: int | None = None) -> None:
        """把内容整体从偏移处滑到位。

        直接动 Canvas 里那个 window item 的坐标，不碰布局，
        所以不会引起重排闪烁。
        """
        # dx / dy 是已经过 DPI 换算的像素（调用方传 SP.*），这里不再套 S()。
        ox = dx
        oy = dy if dy is not None else SP.sm

        def frame(t):
            try:
                self.cv.coords(self._win, ox * (1 - t), oy * (1 - t))
            except tk.TclError:
                pass

        anim.tween(self, "slide", duration or MOTION.base, frame,
                   anim.ease_out_quint, interval=MOTION.interval)


# ---------------------------------------------------------------- 段落文本


class Paragraph(tk.Label):
    """自动折行的只读段落。

    为什么用 Label + wraplength，而不是 Text + count(displaylines)：

    1. 实测 Tk 8.6 的 Label 在设了 wraplength 之后**能正确按字符折中文**
       （没有空格也照折，超长 URL 也能断开），之前的「Label 折行很怪」
       是因为没设 wraplength 或设得比实际宽度大。
    2. Text 的高度必须自己算，而算高度依赖「控件当前宽度」——控件宽度又要
       等高度定了才分配，于是要么首帧只有一行、要么整页抖一下。
       交给 Label 之后高度由 Tk 内部算，一次布局就位，不再有回弹。
    3. 早期版本用 count(..., "displaylines") 逐字 measure，40 个段落
       要几千次 Tcl 调用，切页会卡将近 1 秒。Label 是 C 层排版，快两个数量级。
    """

    def __init__(self, master, pal: theme.Palette, text: str, bg: str, fg: str,
                 size: int | None = None, mono: bool = False,
                 spacing: int | None = None, lh: int = 0):
        self.pal = pal
        # spacing 是段落的上下留白，属于间距，走 SP；传进来的必须是已换算的像素。
        pad = spacing if spacing is not None else SP.xxs
        super().__init__(
            master, text=text, bg=bg, fg=fg,
            font=theme.font(master, size if size is not None else theme.TS["body"],
                            mono=mono),
            justify="left", anchor="w", bd=0, padx=0, pady=pad,
            # 先给一个偏小的折行宽度：Label 的请求宽度不会超过它，
            # 所以父容器不会因为「文本太长」而被撑开。真正的宽度在
            # 第一次 Configure 时换成实测值。
            wraplength=S(160),
        )
        self.bind("<Configure>", self._fit)

    def _fit(self, e=None):
        w = (getattr(e, "width", 0) or 0) if e is not None else 0
        if w <= 1:
            w = self.winfo_width()
        if w <= 1:
            return
        want = max(S(60), w - S(2))
        try:
            if abs(int(self.cget("wraplength")) - want) > 2:
                self.configure(wraplength=want)
        except Exception:
            pass

    def set_text(self, text: str) -> None:
        self.configure(text=text)

    def set_fg(self, color: str) -> None:
        self.configure(fg=color)


# ---------------------------------------------------------------- 列表行


class ListRow(tk.Canvas):
    """可点选的列表行：图标徽章 + 标题 + 副标题 + 右侧状态点。

    状态点用「实心点 + 外圈光环」表示启用，灰色空心点表示未启用；
    启用态的光环会随 hover 微亮，这一层比纯色点更有信息量。
    """

    def __init__(self, master, pal: theme.Palette, title: str, subtitle: str = "",
                 active: bool = False, dot: str | None = None,
                 trailing: str = "", command: Callable | None = None,
                 bg: str | None = None, height: int | None = None,
                 icon: str = "", icon_on: bool = True, badge: str = ""):
        self.pal = pal
        self.title = title
        self.subtitle = subtitle
        self.active = active
        self.dot = dot
        self.trailing = trailing
        self.command = command
        self._bg = bg or master.cget("bg")
        self.h = height if height is not None else DIM.ROW_H
        self.icon = icon
        self.icon_on = icon_on
        self.badge = badge
        self._hover = False
        self._hv = 0.0
        super().__init__(master, height=self.h, bg=self._bg,
                         highlightthickness=0, bd=0,
                         cursor="hand2" if command else "arrow")
        self._draw()
        self.bind("<Configure>", lambda e: self._draw())
        if command:
            self.bind("<Button-1>", lambda e: command())
            self.bind("<Enter>", lambda e: self._set_hover(True))
            self.bind("<Leave>", lambda e: self._set_hover(False))
            enable_focus(self, self._draw, command)

    def _draw(self) -> None:
        self.delete("all")
        p = self.pal
        w = self.winfo_width() or S(240)
        if w <= 1:
            w = S(240)
        x1, y1, x2, y2 = S(6), S(1), w - S(6), self.h - S(1)

        if self.active:
            round_rect(self, x1, y1, x2, y2, S(RADIUS_LG_ROW),
                       fill=p.brand_soft, outline=p.brand_soft)
        elif self._hv > 0.01:
            col = theme.mix(self._bg, p.bg_hover, self._hv)
            round_rect(self, x1, y1, x2, y2, S(RADIUS_LG_ROW), fill=col, outline=col)

        title_color = p.text if (self.active or self._hv > 0.5) else p.text_secondary

        left = S(10)
        if self.icon:
            size = S(26)
            by = (self.h - size) / 2
            if self.active:
                bfill, bfg = p.brand, p.on_brand
            else:
                bfill = theme.mix(p.bg_subtle, p.brand_soft, self._hv)
                bfg = theme.mix(p.text_tertiary, p.brand_ink, self._hv)
            draw_badge(self, left, by, size, self.icon, bfill, bfg,
                       radius=S(8), icon_scale=0.56)
            left += size + S(10)
        elif self.dot:
            r = S(3.5)
            cy = self.h / 2
            self.create_oval(left + S(3) - r, cy - r, left + S(3) + r, cy + r,
                             fill=self.dot, outline=self.dot)
            left += S(18)

        right = w - S(14)
        if self.badge:
            bw = max(S(34), S(6) * len(self.badge))
            round_rect(self, right - bw, self.h / 2 - S(9), right, self.h / 2 + S(9),
                       S(RADIUS_PILL), fill=p.brand_soft, outline=p.brand_soft)
            self.create_text(right - bw / 2, self.h / 2, text=self.badge,
                             fill=p.brand_ink, font=theme.font(self, theme.TS["micro"], "bold"))
            right -= bw + S(8)

        if self.dot and self.icon:
            r = S(3.5)
            cy = self.h / 2
            cx = right - r
            if self.icon_on:
                # 外圈光环：用底色混出来的淡品牌色，模拟半透明
                halo = theme.mix(self._bg if not self.active else p.brand_soft,
                                 p.brand, 0.28)
                self.create_oval(cx - r * 2.1, cy - r * 2.1, cx + r * 2.1, cy + r * 2.1,
                                 fill=halo, outline="")
            self.create_oval(cx - r, cy - r, cx + r, cy + r,
                             fill=self.dot, outline=self.dot)
            right -= r * 2.1 + S(6)

        if self.trailing:
            self.create_text(right, self.h / 2, anchor="e", text=self.trailing,
                             fill=p.text_tertiary, font=theme.font(self, theme.TS["micro"]))
            right -= S(6) + self.tk.call(
                "font", "measure", theme.font(self, theme.TS["micro"]), self.trailing)

        text_x = left + S(2)
        avail = int(right - text_x - S(4))
        f_title = theme.font(self, theme.TS["body"],
                             "bold" if self.active else "normal")
        f_sub = theme.font(self, theme.TS["micro"])
        if self.subtitle:
            self.create_text(text_x, self.h / 2 - S(8), anchor="w",
                             text=elide(self, f_title, self.title, avail),
                             fill=title_color, font=f_title)
            self.create_text(text_x, self.h / 2 + S(9), anchor="w",
                             text=elide(self, f_sub, self.subtitle, avail),
                             fill=p.text_tertiary, font=f_sub)
        else:
            self.create_text(text_x, self.h / 2, anchor="w",
                             text=elide(self, f_title, self.title, avail),
                             fill=title_color, font=f_title)

        if has_focus(self):
            draw_focus_ring(self, x1, y1, x2, y2, S(RADIUS_LG_ROW), p)

    def _set_hover(self, on: bool) -> None:
        if self._hover == on:
            return
        self._hover = on
        start = self._hv
        target = 1.0 if on else 0.0

        def frame(t):
            self._hv = start + (target - start) * t
            self._draw()

        anim.tween(self, "hover", MOTION.quick, frame, anim.ease_out_cubic,
                   interval=MOTION.interval)

    def set_active(self, on: bool) -> None:
        if self.active != on:
            self.active = on
            self._draw()

    def update_state(self, subtitle: str | None = None, dot: str | None = None,
                     icon_on: bool | None = None, trailing: str | None = None,
                     badge: str | None = None) -> None:
        """就地更新状态，不重建控件（避免列表闪烁）。"""
        if subtitle is not None:
            self.subtitle = subtitle
        if dot is not None:
            self.dot = dot
        if icon_on is not None:
            self.icon_on = icon_on
        if trailing is not None:
            self.trailing = trailing
        if badge is not None:
            self.badge = badge
        self._draw()


# ---------------------------------------------------------------- 统计块


# ---------------------------------------------------------------- 徽章 / 步骤


class Pill(tk.Canvas):
    """小胶囊标签，如「推荐」「2 个已启用」。"""

    def __init__(self, master, pal: theme.Palette, text: str, fg: str | None = None,
                 fill: str | None = None, bg: str | None = None, size: int = 7):
        self.pal = pal
        self.text = text
        self._bg = bg or master.cget("bg")
        self.fg = fg or pal.brand_ink
        self.fill = fill or pal.brand_soft
        self._size = size
        f = theme.font(master, size, "bold")
        probe = tk.Canvas(master, width=1, height=1)
        tw = probe.tk.call("font", "measure", f, text)
        probe.destroy()
        self.w = int(tw) + S(16)
        self.h = S(20)
        super().__init__(master, width=self.w, height=self.h, bg=self._bg,
                         highlightthickness=0, bd=0)
        self._draw()

    def _draw(self):
        self.delete("all")
        f = theme.font(self, self._size, "bold")
        round_rect(self, 0, S(1), self.w, self.h - S(1), (self.h - S(2)) / 2,
                   fill=self.fill, outline=self.fill)
        self.create_text(self.w / 2, self.h / 2, text=self.text, fill=self.fg, font=f)

    def set_text(self, text: str, fg: str | None = None, fill: str | None = None):
        f = theme.font(self, self._size, "bold")
        tw = self.tk.call("font", "measure", f, text)
        self.text = text
        if fg:
            self.fg = fg
        if fill:
            self.fill = fill
        self.w = int(tw) + S(16)
        self.configure(width=self.w)
        self._draw()


class Step(tk.Frame):
    """教程步骤：序号圆点 + 标题 + 说明。"""

    def __init__(self, master, pal: theme.Palette, index: int, title: str,
                 body: str, bg: str | None = None, last: bool = False):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg)

        # 高度必须给一个小的请求值：Canvas 默认 265px 高，
        # 只写 width 的话每个步骤会被撑成一大块空白。
        rail = tk.Canvas(self, width=S(30), height=S(20), bg=self._bg,
                         highlightthickness=0, bd=0)
        rail.pack(side="left", fill="y")

        text = tk.Frame(self, bg=self._bg)
        text.pack(side="left", fill="both", expand=True, pady=(0, SP.lg))

        tk.Label(text, text=title, bg=self._bg, fg=pal.text,
                 font=theme.font(self, theme.TS["small"], "bold")).pack(anchor="w")
        Paragraph(text, pal, body, self._bg, pal.text_secondary,
                  size=theme.TS["micro"], spacing=SP.xxs).pack(fill="x", pady=(SP.xs, 0))

        def paint(_e=None):
            h = self.winfo_height()
            if h <= 1:
                return
            rail.delete("all")
            cy = S(11)
            if not last:
                rail.create_line(S(15), cy + S(10), S(15), h - S(2),
                                 fill=pal.border, width=STK.rule)
            rail.create_oval(S(15) - S(9), cy - S(9), S(15) + S(9), cy + S(9),
                             fill=pal.brand_soft, outline=pal.brand_soft)
            rail.create_text(S(15), cy, text=str(index), fill=pal.brand_ink,
                             font=theme.font(self, theme.TS["micro"], "bold"))

        self.bind("<Configure>", paint)
        anim.later(self, 20, paint)


class Collapsible(tk.Frame):
    """可折叠区块：标题行 + 展开后的内容区。

    用于通道详情页的「怎么配置」——默认收起，页面就不会被一大段说明
    顶到屏幕外面去；没配好的通道默认展开，第一次用的人不用去找。
    """

    def __init__(self, master, pal: theme.Palette, title: str,
                 bg: str | None = None, expanded: bool = False,
                 icon: str = ""):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        self.title = title
        self.icon = icon
        self.expanded = expanded
        self._hover = False
        super().__init__(master, bg=self._bg)

        head = tk.Frame(self, bg=self._bg, cursor="hand2")
        head.pack(fill="x")
        self.head = head

        if icon:
            ic = tk.Canvas(head, width=DIM.ICON, height=DIM.ICON, bg=self._bg,
                           highlightthickness=0, bd=0, cursor="hand2")
            ic.pack(side="left", padx=(0, SP.sm))
            draw_icon(ic, icon, 0, 0, DIM.ICON, pal.brand_ink, STK.icon_thin)
            ic.bind("<Button-1>", lambda e: self.toggle())

        self.lbl = tk.Label(head, text=title, bg=self._bg, fg=pal.text,
                            font=theme.font(self, theme.TS["small"], "bold"), cursor="hand2")
        self.lbl.pack(side="left")

        self.chev = tk.Canvas(head, width=DIM.ICON, height=DIM.ICON, bg=self._bg,
                              highlightthickness=0, bd=0, cursor="hand2")
        self.chev.pack(side="left", padx=(SP.xs, 0))

        self.body = tk.Frame(self, bg=self._bg)
        if expanded:
            self.body.pack(fill="x", pady=(SP.sm, 0))

        for w in (head, self.lbl, self.chev):
            w.bind("<Button-1>", lambda e: self.toggle())
            w.bind("<Enter>", lambda e: self._set_hover(True))
            w.bind("<Leave>", lambda e: self._set_hover(False))

        # 折叠标题本身是个按钮，键盘必须够得到。文字控件用下划线做焦点提示，
        # 比画一圈方框更符合它「一段文字」的形态。
        try:
            self.lbl.configure(takefocus=1)
        except Exception:
            pass
        self.lbl.bind("<FocusIn>", lambda e: self._focus(True))
        self.lbl.bind("<FocusOut>", lambda e: self._focus(False))
        self.lbl.bind("<Return>", lambda e: self.toggle())
        self.lbl.bind("<space>", lambda e: self.toggle())
        self._paint()

    def _focus(self, on: bool) -> None:
        f = theme.font(self, theme.TS["body"], "bold")
        if on:
            self.lbl.configure(font=(f[0], f[1], "bold underline"),
                               fg=self.pal.brand_ink)
        else:
            self.lbl.configure(font=f, fg=self.pal.text)

    def _paint(self):
        self.chev.delete("all")
        # 收起时朝右、展开时朝下，方向本身就是状态提示
        if self.expanded:
            self.chev.create_line(S(4), S(6), S(8), S(10), S(12), S(6),
                                  fill=self.pal.text_tertiary, width=STK.icon_thin,
                                  capstyle="round", joinstyle="round")
        else:
            self.chev.create_line(S(6), S(4), S(10), S(8), S(6), S(12),
                                  fill=self.pal.text_tertiary, width=STK.icon_thin,
                                  capstyle="round", joinstyle="round")

    def _set_hover(self, on: bool):
        if self._hover != on:
            self._hover = on
            self.lbl.configure(fg=self.pal.brand_ink if on else self.pal.text)

    def toggle(self):
        self.expanded = not self.expanded
        if self.expanded:
            self.body.pack(fill="x", pady=(SP.sm, 0))
        else:
            self.body.pack_forget()
        self._paint()


class Toast(tk.Frame):
    """底部浮出的轻提示，自动消失。出现时上滑 + 颜色渐显。

    宽度按内容自适应，但有上限：一条「发送异常：<一长串 traceback>」如果
    不设上限，toast 会横向撑出窗口，超出部分被画布裁掉，人反而看不到
    错误的后半句。所以文字超过 DIM.TOAST_W 就折行，画布随之长高。
    """

    def __init__(self, master, pal: theme.Palette):
        self.pal = pal
        self._timer = None
        super().__init__(master, bg=pal.bg_card, highlightthickness=0)
        self.cv = tk.Canvas(self, bg=pal.bg_card, highlightthickness=0, bd=0,
                            height=DIM.TOAST_H)
        self.cv.pack(fill="both", expand=True)
        self.icon = tk.Canvas(self.cv, width=DIM.ICON_LG, height=DIM.ICON_LG, bg=pal.bg_card,
                              highlightthickness=0, bd=0)
        self._icon_win = self.cv.create_window(S(14), DIM.TOAST_H / 2,
                                               window=self.icon, anchor="w")
        self.label = tk.Label(self.cv, text="", bg=pal.bg_card, fg=pal.text,
                              font=theme.font(master, theme.TS["small"]),
                              justify="left")
        self._win = self.cv.create_window(S(40), DIM.TOAST_H / 2,
                                          window=self.label, anchor="w")
        self.cv.bind("<Configure>", self._draw)

    def _fit(self) -> None:
        """按文字实测尺寸定画布宽高，并把图标与文字重新垂直居中。"""
        self.label.configure(wraplength=DIM.TOAST_W)
        self.label.update_idletasks()
        tw = min(self.label.winfo_reqwidth(), DIM.TOAST_W)
        th = self.label.winfo_reqheight()
        h = max(DIM.TOAST_H, th + SP.md)
        self.cv.configure(width=S(40) + tw + SP.md, height=h)
        self.cv.coords(self._icon_win, S(14), h / 2)
        self.cv.coords(self._win, S(40), h / 2)

    def _draw(self, e=None):
        self.cv.delete("bg")
        w = self.cv.winfo_width() or S(300)
        h = self.cv.winfo_height() or DIM.TOAST_H
        p = self.pal
        round_rect(self.cv, 0, S(2), w, h + S(2), S(RADIUS_LG),
                   fill=p.shadow_near, outline=p.shadow_near, tags="bg")
        round_rect(self.cv, 0.5, 0.5, w - 0.5, h - 0.5, S(RADIUS_LG),
                   fill=p.bg_card, outline=p.border, width=STK.control, tags="bg")
        self.cv.tag_lower("bg")

    def show(self, text: str, kind: str = "ok", ms: int = 2600) -> None:
        p = self.pal
        colors = {
            "ok": (p.success, "check"),
            "warn": (p.warning, "alert"),
            "error": (p.danger, "alert"),
            "info": (p.brand_ink, "info"),
        }
        color, icon = colors.get(kind, colors["ok"])
        self.icon.delete("all")
        draw_badge(self.icon, 0, 0, DIM.ICON_LG, icon, color, "#ffffff", radius=S(RADIUS_SM),
                   icon_scale=0.62)
        self.label.configure(text=text, fg=p.text)
        self._fit()
        if self._timer:
            try:
                self.after_cancel(self._timer)
            except Exception:
                pass
        self._timer = self.after(ms, self.hide)
        self.lift()

        def frame(t):
            self.place(relx=0.5, rely=1.0, anchor="s", y=-S(14) + int(S(10) * (1 - t)))

        self.place(relx=0.5, rely=1.0, anchor="s", y=-S(14) + S(10))
        anim.tween(self, "in", MOTION.base, frame, anim.ease_out_cubic,
                   interval=MOTION.interval)

    def hide(self) -> None:
        self._timer = None

        def frame(t):
            try:
                self.place(relx=0.5, rely=1.0, anchor="s",
                           y=-S(14) + int(S(8) * t))
            except tk.TclError:
                pass

        anim.tween(self, "in", MOTION.quick, frame, anim.ease_out_cubic,
                   interval=MOTION.interval,
                   on_done=self.place_forget)


# ================================================================
# 以下为「链路」语汇的部件。整个产品的核心内容是一条
#   事件 → 过滤 → 渲染 → 通道 → 手机
# 的链路，所以导航是链路、通道是出口、状态是时间轴。
# 这些部件不是通用 UI 套件，是照着这个隐喻做的。
# ================================================================


class NavRail(tk.Canvas):
    """左侧导航的竖脊。

    导航项本身不画「你在哪」，由这根竖脊上的节点表达：选中项是实心
    品牌色节点 + 一圈浅光晕，未选中是空心小点。教程页不在链路上，
    用一段虚线隔开——虚线本身就是「这里断开」的信息。

    用法：先 add() 所有 NavItem，布局稳定后 repaint()。
    """

    def __init__(self, master, pal: theme.Palette, bg: str | None = None,
                 width: int = 22):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        self.w = S(width)
        self._entries: list[tuple[object, bool]] = []
        super().__init__(master, width=self.w, bg=self._bg,
                         highlightthickness=0, bd=0)
        self._pend = False
        self.bind("<Configure>", lambda e: self._schedule())
        self.after_idle(self._schedule)

    def add(self, item, gap_before: bool = False) -> None:
        self._entries.append((item, gap_before))
        # 节点位置取决于每个导航项最终的 y。子控件布局完成比父容器晚，
        # 只在自身 <Configure> 里重绘会拿到全是 0 的坐标（节点会叠成一坨），
        # 所以每个 item 的 Configure 也要触发一次（合并成一帧）。
        item.bind("<Configure>", lambda e: self._schedule(), add="+")

    def _schedule(self) -> None:
        if self._pend:
            return
        self._pend = True

        def run():
            self._pend = False
            self.repaint()

        anim.later(self, 16, run)

    def set_selected(self, key: str, animate: bool = True) -> None:
        """选中项变化时，让一个点沿竖脊滑到新节点。

        这是整个界面里唯一「讲故事」的动效：切换页面时能看见信号从
        链路的一个节点移动到另一个节点。一次 240ms，不重复、不循环。
        """
        idx = next((i for i, (it, _g) in enumerate(self._entries)
                    if getattr(it, "key", None) == key), None)
        if idx is None:
            return
        pts = self._ys()
        prev = getattr(self, "_sel_idx", None)
        self._sel_idx = idx
        if not animate or prev is None or prev == idx or prev >= len(pts) \
                or idx >= len(pts) or anim.reduced():
            self.repaint()
            return
        y0, y1 = pts[prev][0], pts[idx][0]
        self.repaint()
        cx = self.w / 2.0

        def frame(t):
            self.delete("travel")
            y = y0 + (y1 - y0) * t
            r = S(3.6)
            self.create_oval(cx - r, y - r, cx + r, y + r,
                             fill=self.pal.brand, outline=self.pal.brand,
                             tags="travel")

        anim.tween(self, "travel", MOTION.slow, frame, anim.ease_out_quint,
                   interval=MOTION.interval, on_done=lambda: self.delete("travel"))

    def _ys(self) -> list[tuple[float, bool]]:
        out = []
        for item, gap in self._entries:
            try:
                out.append((item.winfo_y() + item.winfo_height() / 2.0, gap))
            except Exception:
                out.append((0.0, gap))
        return out

    def repaint(self) -> None:
        self.delete("all")
        pts = self._ys()
        if len(pts) < 1:
            return
        cx = self.w / 2.0
        for i in range(1, len(pts)):
            y0, y1 = pts[i - 1][0], pts[i][0]
            if abs(y1 - y0) < 2:
                continue
            if pts[i][1]:
                self.create_line(cx, y0 + S(4), cx, y1 - S(4), fill=self.pal.rail,
                                 width=STK.rule, dash=(S(1), S(4)))
            else:
                self.create_line(cx, y0, cx, y1, fill=self.pal.rail, width=STK.rule)

        for (cy, _gap), (item, _g) in zip(pts, self._entries, strict=False):
            sel = bool(getattr(item, "selected", False))
            if sel:
                self.create_oval(cx - S(9), cy - S(9), cx + S(9), cy + S(9),
                                 fill=self.pal.brand_soft, outline="")
                r = S(4)
                self.create_oval(cx - r, cy - r, cx + r, cy + r,
                                 fill=self.pal.brand, outline=self.pal.brand)
            else:
                r = S(2.8)
                self.create_oval(cx - r, cy - r, cx + r, cy + r,
                                 fill=self._bg, outline=self.pal.rail,
                                 width=STK.rule)


class Section(tk.Frame):
    """页面内的分节：标题 + 一条向右延伸的细线 + 内容。

    这是用来替代「把每块内容都塞进白色圆角卡片」的。结构靠留白和
    一条细线区分层级，不靠容器边框——页面读起来像一页文档，
    而不是一堆积木。
    """

    def __init__(self, master, pal: theme.Palette, title: str, desc: str = "",
                 bg: str | None = None, gap: int | None = None):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg)

        head = tk.Frame(self, bg=self._bg)
        head.pack(fill="x")

        tk.Label(head, text=title, bg=self._bg, fg=pal.text,
                 font=theme.font(self, theme.TS["section"], "bold")).pack(side="left")

        line = tk.Canvas(head, height=S(11), bg=self._bg, highlightthickness=0, bd=0)
        line.pack(side="left", fill="x", expand=True, padx=(SP.sm, 0))
        line.bind("<Configure>", lambda e, c=line: self._paint_rule(c))

        if desc:
            Paragraph(self, pal, desc, self._bg, pal.text_tertiary,
                      size=theme.TS["small"], spacing=2).pack(
                fill="x", pady=(SP.xs, 0))

        self.body = tk.Frame(self, bg=self._bg)
        # gap 传的是已换算过的像素；不传就用分节默认的下边距
        self.body.pack(fill="x", pady=(SP.sm, gap if gap is not None else SP.lg))

    def _paint_rule(self, c: tk.Canvas):
        c.delete("all")
        w = c.winfo_width()
        if w <= 2:
            return
        c.create_line(0, c.winfo_height() / 2, w, c.winfo_height() / 2,
                      fill=self.pal.border, width=STK.rule)


class Metric(tk.Frame):
    """一个数字 + 一句标签。刻意不做成卡片。

    数字用等宽字体：这几项数据是要横向对比的，等宽才能让个位对齐。
    这里用等宽不是「拿等宽装技术感」，是内容本身需要。
    """

    def __init__(self, master, pal: theme.Palette, label: str, value: str,
                 color: str, bg: str | None = None, unit: str = ""):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        self.color = color
        self._unit = unit
        super().__init__(master, bg=self._bg)
        row = tk.Frame(self, bg=self._bg)
        row.pack(anchor="w")
        self.value_lbl = tk.Label(row, text=value, bg=self._bg, fg=color,
                                  font=theme.font(self, theme.TS["title"],
                                                  "bold", mono=True))
        self.value_lbl.pack(side="left")
        if unit:
            tk.Label(row, text=unit, bg=self._bg, fg=pal.text_tertiary,
                     font=theme.font(self, theme.TS["micro"])).pack(
                side="left", padx=(SP.xs, 0), pady=(SP.xs, 0))
        tk.Label(self, text=label, bg=self._bg, fg=pal.text_tertiary,
                 font=theme.font(self, theme.TS["micro"])).pack(
            anchor="w", pady=(SP.xxs, 0))

    def set_value(self, value: str, color: str | None = None) -> None:
        self.value_lbl.configure(text=value)
        if color:
            self.value_lbl.configure(fg=color)


class Timeline(tk.Frame):
    """发送记录时间轴。

    一行一条记录：等宽时间 → 结果节点 → 通道名 → 摘要。
    竖向有一根极淡的线把记录串起来，暗示「这是一串发生过的事」。

    第一次显示时做一次错峰点亮（每行间隔 45ms），这是整个界面里
    唯一的编排式动效；之后不再重复，避免变成「每页都 fade-in」。
    """

    ROW_H = 42

    def __init__(self, master, pal: theme.Palette, bg: str | None = None):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        self._rows: list[tk.Canvas] = []
        self._revealed = False
        super().__init__(master, bg=self._bg)

    def set_rows(self, rows: list[dict]) -> None:
        """rows: [{time, channel, summary, ok}]"""
        for w in self._rows:
            w.destroy()
        self._rows = []
        for _i, r in enumerate(rows):
            cv = tk.Canvas(self, height=S(self.ROW_H), bg=self._bg,
                           highlightthickness=0, bd=0)
            cv.pack(fill="x")
            cv._data = r
            cv._rev = 0.0
            cv.bind("<Configure>", lambda e, c=cv: self._paint(c))
            self._rows.append(cv)
        self._revealed = False
        self.after(30, self.reveal)

    def _paint(self, cv: tk.Canvas) -> None:
        cv.delete("all")
        p = self.pal
        r = getattr(cv, "_data", {})
        t = getattr(cv, "_rev", 1.0)
        w = cv.winfo_width() or S(400)
        if w <= 1:
            return
        cy = S(self.ROW_H) / 2
        x_time = S(4)
        x_node = S(74)
        x_text = S(92)

        ok = r.get("ok")
        # ok 为 None 表示「已发出但未拿到结果」——用中性色，不要谎报成功
        dot = p.success if ok is True else (p.danger if ok is False else p.text_tertiary)
        dot = theme.mix(p.border_strong, dot, t)

        # 竖向连线（除了最后一行都往下延伸）
        if cv is not self._rows[-1]:
            cv.create_line(x_node, cy, x_node, S(self.ROW_H), fill=p.border,
                           width=STK.rule)

        cv.create_text(x_time, cy, anchor="w", text=r.get("time", ""),
                       fill=theme.mix(p.border_strong, p.text_tertiary, t),
                       font=theme.font(self, theme.TS["mono"], mono=True))

        rr = S(3.2) * (0.4 + 0.6 * t)
        cv.create_oval(x_node - rr, cy - rr, x_node + rr, cy + rr,
                       fill=dot, outline=dot)

        chan = r.get("channel", "")
        if chan:
            f = theme.font(self, theme.TS["body"], "bold")
            cv.create_text(x_text, cy, anchor="w", text=chan,
                           fill=theme.mix(p.bg_app, p.text, t), font=f)
            cw = cv.tk.call("font", "measure", f, chan)
            cv.create_text(x_text + cw + S(10), cy, anchor="w",
                           text=r.get("summary", ""),
                           fill=theme.mix(p.bg_app, p.text_tertiary, t),
                           font=theme.font(self, theme.TS["small"]))
        else:
            # 只有一句正文时不必分两栏，一行读完最省事
            cv.create_text(x_text, cy, anchor="w", text=r.get("summary", ""),
                           fill=theme.mix(p.bg_app, p.text_secondary, t),
                           font=theme.font(self, theme.TS["small"]))

    def reveal(self) -> None:
        if self._revealed:
            return
        self._revealed = True
        for i, cv in enumerate(self._rows):
            if anim.reduced():
                cv._rev = 1.0
                self._paint(cv)
                continue

            def run(c=cv):
                def frame(t):
                    c._rev = t
                    self._paint(c)

                anim.tween(c, "rev", MOTION.slow, frame, anim.ease_out_quint,
                           interval=MOTION.interval)

            anim.later(cv, 45 * i, run)


class EmptyState(tk.Frame):
    """空状态：一句解释 + 一个出口动作。

    「没有记录」本身不是错误，但必须告诉人下一步做什么。
    """

    def __init__(self, master, pal: theme.Palette, icon: str, title: str,
                 body: str, bg: str | None = None,
                 action: tuple[str, Callable] | None = None):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg)
        box = tk.Frame(self, bg=self._bg)
        box.pack(anchor="w", pady=(SP.sm, SP.sm))

        ic = tk.Canvas(box, width=DIM.ICON_BOX, height=DIM.ICON_BOX, bg=self._bg,
                       highlightthickness=0, bd=0)
        ic.pack(anchor="w")
        draw_badge(ic, 0, 0, DIM.ICON_BOX, icon, pal.bg_subtle, pal.text_tertiary,
                   radius=S(9), icon_scale=0.5)

        tk.Label(box, text=title, bg=self._bg, fg=pal.text,
                 font=theme.font(self, theme.TS["body"], "bold")).pack(
            anchor="w", pady=(SP.sm, 0))
        Paragraph(box, pal, body, self._bg, pal.text_tertiary,
                  size=theme.TS["small"], spacing=3).pack(fill="x", pady=(SP.xxs, 0))
        if action:
            text, cmd = action
            Button(box, text, pal, cmd, variant="secondary",
                   bg=self._bg).pack(anchor="w", pady=(SP.sm, 0))
