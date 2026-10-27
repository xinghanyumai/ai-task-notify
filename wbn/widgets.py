"""自绘控件库。

Tkinter 原生控件做不出圆角、层次和克制的交互反馈，所以这里用 Canvas
手工绘制一套。约定：

- 每个控件构造时都要拿到当前调色板 `pal` 和父容器底色 `bg`
  （Canvas 不能真透明，必须知道「自己压在哪一层上」）
- 尺寸一律走 `theme.S()` / 令牌换算，保证高 DPI 下比例正确
- 所有状态切换（hover / 按下 / 选中）都走 anim.tween 做过渡，
  而不是瞬间跳变——这是「手感」和「廉价感」的分界线

这一版的设计取向（相对上一版）
------------------------------
1. **主操作是墨色，不是品牌绿。** 绿色只出现在「系统状态」上：
   开关打开、连接正常、发送成功。凡是「点一下执行」，一律墨色。
2. **重新引入卡片。** 上一版为了避开模板感，把所有内容铺在底色上，
   结果是一屏没有边界的长表单，扫读成本很高。这一版用「细边框卡片 +
   卡片内细分隔线」组织设置项：边界靠 1px 线，不靠投影和底色差。
3. **进场动效改成指数缓出 + 依次错开。** 上一版所有东西统一 180ms
   三次缓出，中段速度不够，读起来像「被推到位」。这一版用 ease_out_expo，
   前 100ms 走完八成位移，剩下的慢慢收尾。
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from . import anim, theme
from .theme import RADIUS_LG, RADIUS_MD, RADIUS_PILL, RADIUS_SM, RADIUS_XS, S
from .tokens import DIM, MOTION, SP, STK

# ---------------------------------------------------------------- 文本测量

# 每个 toplevel 复用一个 1×1 的隐藏画布做字体测量。
#
# 为什么不是每个控件自己建一个：上一版 Button / Checkbox / Segmented 各自
# 在 __init__ 里 `tk.Canvas(master, width=1, height=1)` → 测量 → destroy，
# 一页二十几个控件就是二十几次画布创建与销毁，这是切页慢的隐形大头之一。
_probes: dict[int, tk.Canvas] = {}


def _probe(widget) -> tk.Canvas | None:
    try:
        tl = widget.winfo_toplevel()
    except Exception:
        return None
    key = id(tl)
    cv = _probes.get(key)
    if cv is not None:
        try:
            if cv.winfo_exists():
                return cv
        except Exception:
            pass
    try:
        cv = tk.Canvas(tl, width=1, height=1, highlightthickness=0, bd=0)
    except Exception:
        return None
    _probes[key] = cv
    return cv


def measure(widget, font, text: str) -> int:
    """字体测量。拿不到 Tk 就按字符数粗估，绝不抛异常。"""
    if not text:
        return 0
    cv = _probe(widget)
    if cv is None:
        return len(text) * 7
    try:
        return int(cv.tk.call("font", "measure", font, text))
    except Exception:
        return len(text) * 7


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
    30px 高的图形只有 30 次绘制，开销可以忽略，但观感比纯色高级很多。
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

    只在真的浮起来的东西上用：Toast、分段控件的滑块、下拉。
    卡片不用投影——卡片的边界靠 1px 描边表达，加了投影反而显脏。
    """
    d = depth if depth is not None else S(3)
    for i, col in ((2, pal.shadow_far), (1, pal.shadow_near)):
        off = d * i / 2
        round_rect(cv, x1 + S(0.5), y1 + off * 0.5, x2 - S(0.5), y2 + off,
                   r, fill=col, outline=col, tags=tags or ())


def hairline(cv: tk.Canvas, x1, y, x2, color: str, tags: str = ""):
    """1px 分隔线。用 create_line 而不是 create_rectangle：
    高 DPI 下矩形的描边会被渲染成两档，线不会。"""
    return cv.create_line(x1, y, x2, y, fill=color, width=STK.rule,
                          tags=tags or ())


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

    elif name == "close":
        cv.create_line(*P(0.26, 0.26), *P(0.74, 0.74), **opt)
        cv.create_line(*P(0.74, 0.26), *P(0.26, 0.74), **opt)

    elif name == "sliders":
        for fy in (0.26, 0.5, 0.74):
            cv.create_line(*P(0.10, fy), *P(0.90, fy), **opt)
        for fx, fy in ((0.36, 0.26), (0.64, 0.5), (0.44, 0.74)):
            cv.create_oval(*P(fx - 0.09, fy - 0.09), *P(fx + 0.09, fy + 0.09),
                           fill=color, outline=color)

    elif name == "dot":
        cv.create_oval(*P(0.34, 0.34), *P(0.66, 0.66), fill=color, outline=color)


def draw_badge(cv: tk.Canvas, x: float, y: float, size: float, icon: str,
               fill: str, fg: str, radius: float | None = None,
               icon_scale: float = 0.52):
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
    颜色用 border_focus（白底 5.16:1，满足非文本 3:1 要求）。
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
    if measure(cv, font, text) <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(cv, font, text[:mid] + "…") <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo] + "…") if lo > 0 else "…"


# ---------------------------------------------------------------- 按钮


class Button(tk.Canvas):
    """圆角按钮。variant: primary / secondary / ghost / danger。

    primary 是墨色实心（不是品牌绿）。理由见模块 docstring 第 1 条：
    彩色实心按钮是「消费级」最强的信号，墨色按钮读起来是专业工具。
    品牌绿留给开关、状态点这些「系统状态」。
    """

    def __init__(
        self,
        master,
        text: str,
        pal: theme.Palette,
        command: Callable | None = None,
        variant: str = "secondary",
        icon: str | None = None,
        height: int = DIM.BTN_H,
        min_width: int = 0,
        font_size: int = theme.TS["small"],
        bold: bool = False,
        bg: str | None = None,
        pad_x: int = 16,
    ):
        self.pal = pal
        self.text = text
        self.variant = variant
        self.icon = icon
        self.command = command
        self.h = S(height)
        self.font_size = font_size
        self.bold = bold
        self._enabled = True
        self._hover = False
        self._press = False
        self._hv = 0.0
        self._loading = False
        self._loading_text = "处理中…"
        self._spin = 0
        self._spin_job = None
        self._bg = bg or master.cget("bg")
        self._pad_x = S(pad_x)

        f = theme.font(master, font_size, "bold" if bold else "normal")
        tw = measure(master, f, text)
        iw = DIM.ICON if icon else 0
        gap = SP.sm if icon and text else 0
        self.w = max(S(min_width), int(tw) + self._pad_x * 2 + iw + gap)

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
            return {"fill": p.ink, "fg": p.on_ink, "hover": p.ink_hover,
                    "active": p.ink_active, "outline": ""}
        if self.variant == "danger":
            return {"fill": p.danger_soft, "fg": p.danger,
                    "hover": theme.mix(p.danger_soft, p.danger, 0.10),
                    "active": theme.mix(p.danger_soft, p.danger, 0.18),
                    "outline": theme.mix(p.danger_soft, p.danger, 0.22)}
        if self.variant == "ghost":
            return {"fill": self._bg, "fg": p.text_secondary,
                    "hover": p.bg_hover, "active": p.bg_active, "outline": ""}
        return {"fill": p.bg_card, "fg": p.text, "hover": p.bg_hover,
                "active": p.bg_active, "outline": p.border_strong}

    def _fill_now(self) -> str:
        c = self._colors
        base = c["fill"]
        if not self._enabled:
            return self.pal.bg_subtle
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
        p = self.pal
        r = S(RADIUS_MD)

        if self._loading:
            fill = theme.mix(self._colors["fill"], p.bg_subtle, 0.5)
            outline = ""
            fg = self._colors["fg"]
        elif not self._enabled:
            fill, fg = p.bg_subtle, p.text_tertiary
            outline = p.border
        else:
            fill = self._fill_now()
            fg = self._colors["fg"]
            outline = self._colors["outline"]

        round_rect(self, S(0.5), S(0.5) + dy, w - S(0.5), h - S(0.5) + dy, r,
                   fill=fill, outline=outline or fill,
                   width=STK.control if outline else 0)

        f = theme.font(self, self.font_size, "bold" if self.bold else "normal")
        cx = w / 2
        cy = h / 2 + dy
        if self._loading:
            self._paint_loading(cx, cy, fg, f)
        elif self.icon and self.text:
            icon_w = DIM.ICON
            tw = measure(self, f, self.text)
            total = icon_w + SP.sm + tw
            left = cx - total / 2
            draw_icon(self, self.icon, left, cy - icon_w / 2, icon_w, fg, STK.icon_thin)
            self.create_text(left + icon_w + SP.sm, cy, text=self.text, anchor="w",
                             fill=fg, font=f)
        elif self.text:
            self.create_text(cx, cy, text=self.text, fill=fg, font=f)

        if has_focus(self) and self._enabled and not self._loading:
            draw_focus_ring(self, S(2.5), S(2.5) + dy, w - S(2.5), h - S(3) + dy,
                            max(S(4), r - S(2)), p)

    # -- 交互 --------------------------------------------------------
    def _tween_hover(self, target: float) -> None:
        start = self._hv

        def frame(t):
            self._hv = start + (target - start) * t
            self._draw()

        anim.tween(self, "hover", MOTION.quick, frame, anim.ease_out_cubic,
                   interval=MOTION.interval)

    def _on_enter(self, _e=None):
        if not self._enabled or self._loading:
            return
        self._hover = True
        self._tween_hover(1.0)

    def _on_leave(self, _e=None):
        self._hover = False
        self._press = False
        self._tween_hover(0.0)

    def _on_press(self, _e=None):
        if not self._enabled or self._loading:
            return
        self._press = True
        self._draw()

    def _on_release(self, e=None):
        if not self._enabled or self._loading:
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
        if text == self.text:
            return
        self.text = text
        self._draw()

    def set_variant(self, variant: str) -> None:
        if variant == self.variant:
            return
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
        tw = measure(self, f, self._loading_text)
        d = DIM.ICON
        total = d + SP.sm + int(tw)
        left = cx - total / 2
        self.create_arc(left, cy - d / 2, left + d, cy + d / 2,
                        start=self._spin, extent=270, style="arc",
                        outline=fg, width=STK.ring)
        self.create_text(left + d + SP.sm, cy, text=self._loading_text,
                         anchor="w", fill=fg, font=f)


# ---------------------------------------------------------------- 开关


class Switch(tk.Canvas):
    """胶囊开关。滑块用 ease-out-back 滑过去，轨道颜色同步插值。

    开关是少数几个用品牌绿的地方：它表达的是「系统状态」，不是「操作」。
    """

    def __init__(self, master, pal: theme.Palette, value: bool = False,
                 command: Callable[[bool], None] | None = None,
                 bg: str | None = None, width: int | None = None,
                 height: int | None = None):
        self.pal = pal
        self.value = bool(value)
        self.command = command
        self._bg = bg or master.cget("bg")
        self.w = S(width if width is not None else DIM.SWITCH_W)
        self.h = S(height if height is not None else DIM.SWITCH_H)
        self._pos = 1.0 if self.value else 0.0
        super().__init__(master, width=self.w, height=self.h + S(4), bg=self._bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._draw()
        self.bind("<Button-1>", self._toggle)
        enable_focus(self, self._draw, self._toggle)

    def _draw(self) -> None:
        self.delete("all")
        p = self.pal
        t = self._pos
        track = theme.mix(p.track, p.brand, t)
        y1 = S(2)
        y2 = self.h + S(2)
        # 关态轨道是浅灰压在更浅的页面底上，几乎看不出形状。
        # 加一圈描边，开关的「可点区域」才读得出来。
        round_rect(self, S(0.5), y1, self.w - S(0.5), y2, (y2 - y1) / 2,
                   fill=track, outline=theme.mix(p.border_control, track, t),
                   width=STK.control)

        knob = self.h - S(6)
        travel = self.w - knob - S(6)
        x = S(3) + travel * t
        cy = (y1 + y2) / 2
        # 滑块投影：往下 1px 压一档暗色，滑块才不像贴纸
        self.create_oval(x + S(0.5), cy - knob / 2 + S(1),
                         x + knob + S(0.5), cy + knob / 2 + S(1),
                         fill=theme.mix(p.track, "#000000", 0.18), outline="")
        self.create_oval(x, cy - knob / 2, x + knob, cy + knob / 2,
                         fill="#ffffff", outline="")

        if has_focus(self):
            draw_focus_ring(self, S(0.5), y1 - S(2), self.w - S(0.5),
                            y2 + S(2), (y2 - y1) / 2 + S(2), p)

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
    """方形勾选框，选中时填充墨色并画白色勾。"""

    def __init__(self, master, pal: theme.Palette, text: str, value: bool = False,
                 command: Callable[[bool], None] | None = None,
                 bg: str | None = None, font_size: int = theme.TS["small"],
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
        tw = measure(master, f, text)
        w = self.box + SP.md + int(tw) + SP.xs

        super().__init__(master, width=w, height=self.box + S(8), bg=self._bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._draw()
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        enable_focus(self, self._draw, self._toggle)

    def _draw(self) -> None:
        self.delete("all")
        p = self.pal
        top = S(4)
        if self.value:
            fill = theme.mix(p.ink, p.ink_hover, self._hv)
            outline = fill
        else:
            fill = theme.mix(p.bg_input, p.bg_hover, self._hv)
            outline = theme.mix(p.border_control, p.text_secondary, self._hv)

        round_rect(self, S(0.5), top + S(0.5), self.box - S(0.5),
                   top + self.box - S(0.5), S(RADIUS_XS), fill=fill,
                   outline=outline, width=STK.control)
        if self.value:
            draw_icon(self, "check", S(2.5), top + S(2.5), self.box - S(5),
                      p.on_ink, STK.icon_bold)
        self.create_text(self.box + SP.md, top + self.box / 2, anchor="w",
                         text=self.label, fill=self.text_color or p.text,
                         font=theme.font(self, self.font_size))
        if has_focus(self):
            draw_focus_ring(self, S(0.5), top - S(1.5), self.winfo_width() - S(0.5),
                            top + self.box + S(1.5), S(RADIUS_SM), p)

    def _set_hover(self, on: bool) -> None:
        if self._hover == on:
            return
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
        v = bool(v)
        if v == self.value:
            return
        self.value = v
        self._draw()


# ---------------------------------------------------------------- 导航项


class NavItem(tk.Canvas):
    """左侧导航项。

    选中态 = 浅底 + 左侧 2px 墨色指示条 + 文字转主色。
    指示条用墨色不用品牌绿：绿色表达「系统状态」，当前位置不是状态，
    它是导航位置。把两者用同一个颜色表达会让「哪条通道开着」和
    「我在哪一页」混成一种视觉语言。
    """

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
        self.h = S(height if height is not None else DIM.NAV_ITEM_H)
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
        x1, y1, x2, y2 = 0, S(1), w, self.h - S(1)

        if self.selected:
            round_rect(self, x1, y1, x2, y2, S(RADIUS_MD),
                       fill=p.bg_subtle, outline=p.bg_subtle)
            fg = p.text
            weight = "bold"
        else:
            if self._hv > 0.01:
                col = theme.mix(p.sidebar, p.bg_hover, self._hv)
                round_rect(self, x1, y1, x2, y2, S(RADIUS_MD),
                           fill=col, outline=col)
            fg = theme.mix(p.text_tertiary, p.text, self._hv)
            weight = "normal"

        ic = DIM.ICON
        ix = SP.md
        iy = (self.h - ic) / 2
        draw_icon(self, self.icon_name, ix, iy, ic, fg,
                  STK.icon if self.selected else STK.icon_thin)
        self.create_text(ix + ic + SP.md, self.h / 2, anchor="w", text=self.label,
                         fill=fg, font=theme.font(self, theme.TS["body"], weight))

        if self.selected:
            # 指示条：贴着左边缘的一小段竖线，高度只有行高的一半
            bar_h = self.h * 0.44
            round_rect(self, 0, (self.h - bar_h) / 2, S(2),
                       (self.h + bar_h) / 2, S(1), fill=p.ink, outline=p.ink)

        if self.badge:
            bw = max(S(20), measure(self, theme.font(self, theme.TS["micro"]), self.badge)
                     + SP.sm)
            round_rect(self, w - SP.md - bw, self.h / 2 - S(9),
                       w - SP.md, self.h / 2 + S(9), S(RADIUS_PILL),
                       fill=p.brand, outline=p.brand)
            self.create_text(w - SP.md - bw / 2, self.h / 2, text=self.badge,
                             fill=p.on_brand,
                             font=theme.font(self, theme.TS["micro"], "bold"))

        if has_focus(self):
            draw_focus_ring(self, x1 + S(1), y1, x2 - S(1), y2, S(RADIUS_MD), p)

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
        self.h = S(height if height is not None else DIM.SEG_H)
        self.font_size = (font_size if font_size is not None
                          else theme.TS["micro"])
        f = theme.font(master, self.font_size)
        widths = [measure(master, f, label) + SP.xl for _, label in options]
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
        x = SP.xxs + sum(self.seg_w[:i])
        return x, x + self.seg_w[i]

    def _draw(self) -> None:
        self.delete("all")
        p = self.pal
        round_rect(self, S(0.5), S(0.5), self.w - S(0.5), self.h - S(0.5),
                   S(RADIUS_MD), fill=p.bg_subtle, outline=p.border,
                   width=STK.control)

        x1, x2 = self._thumb_bounds(self._slide)
        # 选中块带一点点投影，才浮得起来
        round_rect(self, x1, S(2.5), x2, self.h - S(1.5), S(RADIUS_SM),
                   fill=p.shadow_near, outline=p.shadow_near)
        round_rect(self, x1, S(1.5), x2, self.h - S(2.5), S(RADIUS_SM),
                   fill=p.bg_card, outline=p.border_strong, width=STK.control)

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
        x1 = SP.xxs + sum(self.seg_w[:i])
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
    """带圆角边框的输入框。focus 时边框渐变为焦点色并浮出一圈柔光。"""

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
        self.h = S(height if height is not None else DIM.FIELD_H)

        super().__init__(master, bg=self._bg)
        self.cv = tk.Canvas(self, bg=self._bg, highlightthickness=0, bd=0,
                            height=self.h)
        self.cv.pack(fill="both", expand=True)

        f = theme.font(self, font_size if font_size is not None
                       else theme.TS["small"])
        if multiline:
            self.entry = tk.Text(self.cv, bd=0, relief="flat", font=f,
                                 bg=pal.bg_input, fg=pal.text,
                                 insertbackground=pal.brand_ink,
                                 highlightthickness=0, wrap="word",
                                 padx=SP.xs, pady=SP.xs, spacing1=1, spacing3=2)
            self.entry.insert("1.0", value)
            self.entry.configure(height=4)
        else:
            self._var = tk.StringVar(value=value)
            self.entry = tk.Entry(self.cv, bd=0, relief="flat", font=f,
                                  bg=pal.bg_input, fg=pal.text,
                                  insertbackground=pal.brand_ink,
                                  highlightthickness=0, show=show or "",
                                  textvariable=self._var)
            self._var.trace_add("write", lambda *a: self._update_placeholder())

        # 单行用 w 锚点（垂直居中）；多行必须用 nw，否则窗口以 y 为中心
        # 向上向下各展开一半，文字会被顶到边框外面去。
        self._win = self.cv.create_window(
            SP.md, SP.sm if multiline else self.h / 2, window=self.entry,
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
        fill = self.pal.bg_input
        if t > 0.02:
            # 聚焦时加一圈极淡的外发光，边界更清楚
            round_rect(self.cv, -S(1.5), -S(1.5), w + S(1.5), h + S(1.5),
                       S(RADIUS_MD + 2),
                       fill=theme.mix(self.pal.bg_input, self.pal.brand_soft,
                                      t * 0.55),
                       outline="", tags="fieldbg")
        round_rect(self.cv, S(0.5), S(0.5), w - S(0.5), h - S(0.5), S(RADIUS_MD),
                   fill=fill, outline=border,
                   width=STK.ring if t > 0.5 else STK.control, tags="fieldbg")
        self.cv.tag_lower("fieldbg")

        inner_w = max(1, w - SP.xl * 2)
        if self.multiline:
            self.cv.coords(self._win, SP.md, SP.sm)
            self.cv.itemconfigure(self._win, width=inner_w,
                                  height=max(S(60), h - SP.lg))
        else:
            self.cv.coords(self._win, SP.md, h / 2)
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
            x, y, anchor = SP.md + SP.xs, SP.sm + SP.xs, "nw"
        else:
            x, y, anchor = SP.md + SP.xxs, self.h / 2, "w"
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
                 bar_width: int = 6):
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
        self._lastw = -1
        self._drawn_view = (0.0, 1.0)
        self._last_view = (0.0, 1.0)

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
            self.cv.configure(scrollregion=(0, 0, self._lastw, h))
        except tk.TclError:
            return
        self._refresh_bar()

    def _on_canvas(self, e):
        # 宽度没变就不用改 inner 的宽度——改了会反过来再触发一次 _on_inner
        if e.width == self._lastw:
            return
        self._lastw = e.width
        self.cv.itemconfigure(self._win, width=e.width)
        self._refresh_bar()

    # -- 滚动条 ------------------------------------------------------
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
            # 还没布局完，过一会儿再画。但只在真的显示着的时候等：
            # 页面缓存之后，没被 pack 出来的页面也有一堆 ScrollFrame，
            # 它们的滚动条高度永远是 1，无条件重排就会变成每 40ms 唤醒一次
            # 的死循环——白烧 CPU，窗口销毁后还会在日志里刷一串
            # 「invalid command name ..._refresh_bar」。
            if self.bar.winfo_ismapped():
                anim.later(self, 40, self._refresh_bar)
            return
        if first <= 0.0 and last >= 1.0:
            return  # 内容没超出，不画
        top = first * h
        bottom = max(last * h, top + S(28))
        bw = self.bar.winfo_width() or S(6)
        color = self.pal.border_strong
        round_rect(self.bar, S(2), top + S(2), bw - S(2), bottom - S(2),
                   (bw - S(4)) / 2, fill=color, outline=color)

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
            step = S(80) * direction / total
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
            m = S(margin)
            if top >= cur and bottom <= cur + view:
                return
            target_y = top - m if top < cur else bottom - view + m
            start = self.cv.yview()[0]
            target = max(0.0, min(1.0, target_y / total))
            self._target = target

            def frame(t):
                self.cv.yview_moveto(start + (target - start) * t)

            anim.tween(self, "scroll", MOTION.base, frame, anim.ease_out_quint,
                       interval=MOTION.interval)
        except Exception:
            return

    def slide_in(self, dx: int = 0, dy: int | None = None,
                 duration: int | None = None) -> None:
        """内容整体轻轻归位。

        用 yview 做位移是行不通的（内容没超出时没有滚动空间），
        所以这里移动 inner 在画布上的坐标，动画结束后归零。
        """
        if anim.reduced():
            return
        d = S(dy if dy is not None else SP.md)
        ox = S(dx)

        def frame(t):
            off = (1 - t) * d
            offx = (1 - t) * ox
            try:
                self.cv.coords(self._win, offx, off)
            except tk.TclError:
                pass

        def done():
            try:
                self.cv.coords(self._win, 0, 0)
            except tk.TclError:
                pass

        anim.tween(self, "slide", duration or MOTION.base, frame,
                   anim.ease_out_expo, interval=MOTION.interval, on_done=done)


# ---------------------------------------------------------------- 段落


class Paragraph(tk.Label):
    """会按宽度自动折行的正文标签。"""

    def __init__(self, master, pal: theme.Palette, text: str, bg: str, fg: str,
                 size: int | None = None, wrap: int = DIM.HINT_WRAP,
                 line: float | None = None, spacing: int | None = None,
                 mono: bool = False):
        self.pal = pal
        self._wrap = S(wrap)
        super().__init__(master, text=text, bg=bg, fg=fg, anchor="w",
                         justify="left", wraplength=self._wrap,
                         font=theme.font(master, size if size is not None
                                         else theme.TS["small"], mono=mono))
        self.bind("<Configure>", self._fit)

    def _fit(self, e=None):
        w = getattr(e, "width", 0) or self.winfo_width()
        if w > 1 and abs(w - self._wrap) > S(4):
            self._wrap = w
            self.configure(wraplength=w)

    def set_text(self, text: str) -> None:
        self.configure(text=text)

    def set_fg(self, color: str) -> None:
        self.configure(fg=color)


# ---------------------------------------------------------------- 卡片


class Card(tk.Frame):
    """圆角卡片：Canvas 画底，内容装在嵌入的 Frame 里。

    为什么卡片要自己画圆角而不是用 tk.Frame 的 highlightborder：
    后者只能画直角。直角边框在 12px 圆角的按钮和输入框旁边会显得很硬。

    高度由内容决定：__init__ 末尾把画布高度设成 holder 的请求高度，
    之后内容高度变化（折叠面板展开之类）再同步一次。
    """

    def __init__(self, master, pal: theme.Palette, *, title: str = "",
                 desc: str = "", bg: str | None = None, pad: int | None = None,
                 radius: int = RADIUS_LG, border: bool = True,
                 gap: int | None = None, fill: str | None = None):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        # 卡片的填充色默认跟画布一致：这一版用的是「描边分组」而不是
        # 「白底浮起」。原因很实在——卡片里的每个子控件都带着自己的
        # 底色（页面统一传 bg_app），填白底会在卡片内部留下一圈灰边。
        # 描边分组在 Vercel / Linear 的设置页里也是同一个做法。
        self._fill = fill or pal.bg_app
        self._radius = radius
        self._border = border
        self._pad = S(pad if pad is not None else DIM.CARD_PAD)
        self._gap = S(gap) if gap is not None else SP.md
        self._lastw = -1
        self._dy = 0

        super().__init__(master, bg=self._bg)
        self.cv = tk.Canvas(self, bg=self._bg, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)

        # holder 是嵌进画布的那一层，body 才是内容区（对外沿用旧名）
        self.holder = tk.Frame(self.cv, bg=self._fill)
        self._win = self.cv.create_window(0, 0, window=self.holder, anchor="nw")

        self.head = None
        self.title_lbl = None
        self.desc_lbl = None
        if title or desc:
            self.head = tk.Frame(self.holder, bg=self._fill)
            self.head.pack(fill="x", padx=self._pad, pady=(self._pad, 0))
            if title:
                self.title_lbl = tk.Label(
                    self.head, text=title, bg=self._fill, fg=pal.text,
                    anchor="w", font=theme.font(self, theme.TS["section"], "bold"))
                self.title_lbl.pack(fill="x")
            if desc:
                self.desc_lbl = tk.Label(
                    self.head, text=desc, bg=self._fill, fg=pal.text_tertiary,
                    anchor="w", justify="left", wraplength=S(540),
                    font=theme.font(self, theme.TS["small"]))
                self.desc_lbl.pack(fill="x", pady=(SP.xs, 0))

        self.body = tk.Frame(self.holder, bg=self._fill)
        self.body.pack(fill="both", expand=True, padx=self._pad,
                       pady=(self._gap if self.head is not None else self._pad,
                             self._pad))

        self.cv.bind("<Configure>", self._on_conf)
        self.holder.bind("<Configure>", self._on_body)

    # -- 几何 --------------------------------------------------------
    def _sync_height(self, h: int | None = None) -> None:
        """把画布高度对齐到内容高度。

        高度来源是 holder 自己的 <Configure> 事件（Tk 布局完成后会带着真实
        高度过来），不是主动去问请求高度。

        为什么不能用 `holder.update_idletasks()` 去问：那是**全局**空闲刷新，
        会把整个应用重新布局一遍。一页里五到八张卡片互相触发，实测一次
        页面构建里这个方法被调用 13~29 次、单次 20~35ms，占掉整页六成以上
        的构建时间——这就是「切页很慢」的真正来源。
        """
        if h is None:
            h = self.holder.winfo_reqheight()
        h = max(1, int(h))
        try:
            if int(self.cv.cget("height")) != h:
                self.cv.configure(height=h)
        except tk.TclError:
            return

    def _on_body(self, e=None) -> None:
        # e.height 是 holder 布局后的真实高度，直接拿来用，不再回问
        self._sync_height(getattr(e, "height", None))

    def _on_conf(self, e):
        if e.width == self._lastw:
            return
        self._lastw = e.width
        try:
            self.cv.itemconfigure(self._win, width=e.width)
            self.cv.coords(self._win, 0, self._dy)
            self._paint(e.width, e.height)
        except tk.TclError:
            return

    def _paint(self, w: int, h: int):
        self.cv.delete("cardbg")
        p = self.pal
        r = S(self._radius)
        dy = self._dy
        outline = p.border if self._border else self._fill
        round_rect(self.cv, S(0.5), S(0.5) + dy, w - S(0.5), h - S(0.5) + dy, r,
                   fill=self._fill, outline=outline, width=STK.control,
                   tags="cardbg")
        self.cv.tag_lower("cardbg")

    # -- 进场 --------------------------------------------------------
    def set_offset(self, dy: float) -> None:
        """把整张卡片下移 dy 像素（进场动画用）。"""
        self._dy = dy
        try:
            w = self.cv.winfo_width()
            h = self.cv.winfo_height()
            if w > 1 and h > 1:
                # 顺带把嵌入窗口的宽度对齐一次。页面缓存之后，从别的页切回来
                # 时画布宽度可能已经变了，而 <Configure> 要等事件队列才派发，
                # 中间会有一帧内容仍按旧宽度排版。进场时直接钳死，省掉这一帧。
                if w != self._lastw:
                    self._lastw = w
                    self.cv.itemconfigure(self._win, width=w)
                self.cv.coords(self._win, 0, dy)
                self._paint(w, h)
        except tk.TclError:
            return

    def restyle(self) -> None:
        """调色板换了之后重画底与文字颜色（切主题时用）。"""
        p = self.pal
        self.configure(bg=self._bg)
        self.cv.configure(bg=self._bg)
        self.holder.configure(bg=self._fill)
        self.body.configure(bg=self._fill)
        if self.head is not None:
            self.head.configure(bg=self._fill)
        if self.title_lbl is not None:
            self.title_lbl.configure(bg=self._fill, fg=p.text)
        if self.desc_lbl is not None:
            self.desc_lbl.configure(bg=self._fill, fg=p.text_tertiary)
        self._paint(self.cv.winfo_width(), self.cv.winfo_height())


class Section(Card):
    """兼容旧名：分节 = 卡片。第三个位置参数仍是标题。"""

    def __init__(self, master, pal: theme.Palette, title: str = "",
                 desc: str = "", bg: str | None = None, gap: int | None = None,
                 **kw):
        super().__init__(master, pal, title=title, desc=desc, bg=bg, gap=gap, **kw)


# ---------------------------------------------------------------- 设置行


class Row(tk.Frame):
    """卡片里的一行设置：左边标题与说明，右边控件。

    这是设置页最常见的单元。把「标签 + 说明 + 控件」固定成三栏，
    是为了让同一页里所有行的基线对齐——参差的行是廉价感的主要来源。
    """

    def __init__(self, master, pal: theme.Palette, label: str, hint: str = "",
                 bg: str | None = None, wrap: int = 420):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg)

        self.left = tk.Frame(self, bg=self._bg)
        self.left.pack(side="left", fill="x", expand=True)
        self.label = tk.Label(self.left, text=label, bg=self._bg, fg=pal.text,
                              anchor="w", justify="left",
                              font=theme.font(self, theme.TS["body"]))
        self.label.pack(fill="x")
        self.hint = None
        if hint:
            self.hint = tk.Label(self.left, text=hint, bg=self._bg,
                                 fg=pal.text_tertiary, anchor="w",
                                 justify="left", wraplength=S(wrap),
                                 font=theme.font(self, theme.TS["small"]))
            self.hint.pack(fill="x", pady=(SP.xxs, 0))

        self.right = tk.Frame(self, bg=self._bg)
        self.right.pack(side="right", padx=(SP.lg, 0))

    def set_hint(self, text: str) -> None:
        if self.hint is None:
            if not text:
                return
            self.hint = tk.Label(self.left, text=text, bg=self._bg,
                                 fg=self.pal.text_tertiary, anchor="w",
                                 justify="left", wraplength=S(420),
                                 font=theme.font(self, theme.TS["small"]))
            self.hint.pack(fill="x", pady=(SP.xxs, 0))
            return
        self.hint.configure(text=text)


# ---------------------------------------------------------------- 列表行


class ListRow(tk.Canvas):
    """列表行：左侧图标徽章 + 标题 + 副标题 + 右侧状态点。

    用于通道列表。选中态是浅底 + 主色文字，不用品牌绿——
    理由同 NavItem。
    """

    def __init__(self, master, pal: theme.Palette, title: str, subtitle: str = "",
                 icon: str = "dot", command: Callable | None = None,
                 bg: str | None = None, height: int | None = None,
                 dot: str = "", icon_on: bool | None = None,
                 active: bool = False):
        self.pal = pal
        self.title = title
        self.subtitle = subtitle
        self.icon_name = icon
        self.command = command
        self._bg = bg or master.cget("bg")
        self.active = bool(active)
        self._hover = False
        self._hv = 0.0
        self.dot = dot
        self.dot_color = dot
        # icon_on：徽章是否点亮（通道已启用）。None 表示跟随 active。
        self.icon_on = bool(active) if icon_on is None else bool(icon_on)
        self.h = S(height if height is not None else DIM.ROW_H)
        super().__init__(master, height=self.h, bg=self._bg,
                         highlightthickness=0, bd=0,
                         cursor="hand2" if command else "arrow")
        self._draw()
        if command:
            self.bind("<Button-1>", lambda e: self.command())
            self.bind("<Enter>", lambda e: self._set_hover(True))
            self.bind("<Leave>", lambda e: self._set_hover(False))
            enable_focus(self, self._draw, self.command)
        self.bind("<Configure>", lambda e: self._draw())

    def _draw(self) -> None:
        self.delete("all")
        p = self.pal
        w = self.winfo_width() or S(220)
        if w <= 1:
            w = S(220)
        x1, y1, x2, y2 = 0, S(1), w, self.h - S(1)

        if self.active:
            round_rect(self, x1, y1, x2, y2, S(RADIUS_MD),
                       fill=p.bg_subtle, outline=p.bg_subtle)
        elif self._hv > 0.01:
            col = theme.mix(self._bg, p.bg_hover, self._hv)
            round_rect(self, x1, y1, x2, y2, S(RADIUS_MD), fill=col, outline=col)

        # 徽章
        bs = DIM.ICON_BOX
        bx = SP.sm
        by = (self.h - bs) / 2
        if self.icon_on:
            draw_badge(self, bx, by, bs, self.icon_name, p.ink, p.on_ink)
        else:
            draw_badge(self, bx, by, bs, self.icon_name,
                       theme.mix(p.bg_subtle, p.bg_hover, self._hv),
                       theme.mix(p.text_tertiary, p.text, self._hv))

        # 文字
        tx = bx + bs + SP.md
        dot_w = SP.md if self.dot_color else 0
        avail = w - tx - SP.md - dot_w
        ft = theme.font(self, theme.TS["body"], "bold" if self.active else "normal")
        fs = theme.font(self, theme.TS["micro"])
        fg = p.text if self.active else theme.mix(p.text_secondary, p.text, self._hv)
        cy = self.h / 2
        if self.subtitle:
            self.create_text(tx, cy - S(8), anchor="w", fill=fg, font=ft,
                             text=elide(self, ft, self.title, avail))
            self.create_text(tx, cy + S(8), anchor="w", fill=p.text_tertiary,
                             font=fs,
                             text=elide(self, fs, self.subtitle, avail))
        else:
            self.create_text(tx, cy, anchor="w", fill=fg, font=ft,
                             text=elide(self, ft, self.title, avail))

        if self.dot_color:
            cx = w - SP.md - S(3)
            self.create_oval(cx - S(3), cy - S(3), cx + S(3), cy + S(3),
                             fill=self.dot_color, outline=self.dot_color)

        if has_focus(self):
            draw_focus_ring(self, x1 + S(1), y1, x2 - S(1), y2, S(RADIUS_MD), p)

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
                     title: str | None = None, icon_on: bool | None = None) -> None:
        changed = False
        if subtitle is not None and subtitle != self.subtitle:
            self.subtitle = subtitle
            changed = True
        if title is not None and title != self.title:
            self.title = title
            changed = True
        if dot is not None and dot != self.dot_color:
            self.dot_color = dot
            changed = True
        if icon_on is not None and bool(icon_on) != self.icon_on:
            self.icon_on = bool(icon_on)
            changed = True
        if changed:
            self._draw()


# ---------------------------------------------------------------- 标签


class Pill(tk.Canvas):
    """小标签：一行浅底小字。用来标「推荐」「未配置」这类状态。"""

    def __init__(self, master, pal: theme.Palette, text: str, fg: str | None = None,
                 fill: str | None = None, bg: str | None = None,
                 font_size: int | None = None, size: int | None = None):
        self.pal = pal
        self.text = text
        self.fg = fg
        self.fill = fill
        self._bg = bg or master.cget("bg")
        self.font_size = font_size or size or theme.TS["micro"]
        f = theme.font(master, self.font_size, "bold")
        self.w = measure(master, f, text) + SP.lg
        self.h = S(20)
        super().__init__(master, width=self.w, height=self.h, bg=self._bg,
                         highlightthickness=0, bd=0)
        self._draw()

    def _draw(self):
        self.delete("all")
        p = self.pal
        fill = self.fill or p.bg_subtle
        fg = self.fg or p.text_tertiary
        round_rect(self, S(0.5), S(0.5), self.w - S(0.5), self.h - S(0.5),
                   S(RADIUS_PILL), fill=fill, outline=fill)
        self.create_text(self.w / 2, self.h / 2, text=self.text, fill=fg,
                         font=theme.font(self, self.font_size, "bold"))

    def set_text(self, text: str, fg: str | None = None, fill: str | None = None):
        if text == self.text and fg == self.fg and fill == self.fill:
            return
        self.text = text
        if fg is not None:
            self.fg = fg
        if fill is not None:
            self.fill = fill
        f = theme.font(self, self.font_size, "bold")
        self.w = measure(self, f, text) + SP.lg
        self.configure(width=self.w)
        self._draw()


# ---------------------------------------------------------------- 步骤


class Step(tk.Frame):
    """教程页的一步：序号圆点 + 标题 + 正文。"""

    def __init__(self, master, pal: theme.Palette, index: int = 0, title: str = "",
                 body: str = "", bg: str | None = None, i: int | None = None,
                 last: bool = False):
        if i is not None:
            index = i
        p = pal
        self.pal = pal
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg)

        left = tk.Frame(self, bg=self._bg, width=S(32))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        # 序号圆点取 24（= SP.xl）：和下面正文的缩进用同一个数，
        # 就不用再为「圆点有多宽」单独造一个令牌。
        self.mark = tk.Canvas(left, width=SP.xl, height=SP.xl, bg=self._bg,
                              highlightthickness=0, bd=0)
        self.mark.pack(anchor="n", pady=(SP.xxs, 0))
        round_rect(self.mark, 0.5, 0.5, S(23.5), S(23.5), S(12),
                   fill=p.bg_subtle, outline=p.border, width=STK.control)
        self.mark.create_text(S(12), S(12), text=str(index), fill=p.text_secondary,
                              font=theme.font(self, theme.TS["micro"], "bold"))

        right = tk.Frame(self, bg=self._bg)
        right.pack(side="left", fill="both", expand=True)
        tk.Label(right, text=title, bg=self._bg, fg=p.text, anchor="w",
                 font=theme.font(self, theme.TS["body"], "bold")).pack(fill="x")
        if body:
            Paragraph(right, p, body, self._bg, p.text_secondary,
                      size=theme.TS["small"], wrap=520).pack(fill="x",
                                                             pady=(SP.xxs, 0))


# ---------------------------------------------------------------- 折叠


class Collapsible(tk.Frame):
    """可折叠的说明块。默认收起，展开时高度渐变。"""

    def __init__(self, master, pal: theme.Palette, title: str,
                 body: str = "", bg: str | None = None, open_: bool = False,
                 expanded: bool | None = None, icon: str | None = None):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        self.open = bool(open_ if expanded is None else expanded)
        self.icon = icon
        self._hover = False
        self._angle = 90.0 if self.open else 0.0
        super().__init__(master, bg=self._bg)

        self.head = tk.Frame(self, bg=self._bg, cursor="hand2")
        self.head.pack(fill="x")
        self._chev_w = 14
        self.chev = tk.Canvas(self.head, width=S(self._chev_w),
                              height=S(self._chev_w),
                              bg=self._bg, highlightthickness=0, bd=0)
        self.chev.pack(side="left", padx=(0, SP.sm))
        self.lbl = tk.Label(self.head, text=title, bg=self._bg,
                            fg=pal.text_tertiary, anchor="w",
                            font=theme.font(self, theme.TS["small"]))
        self.lbl.pack(side="left")
        self._paint()

        self.body = tk.Frame(self, bg=self._bg)
        self.para = Paragraph(self.body, pal, body, self._bg, pal.text_secondary,
                              size=theme.TS["small"], wrap=520)
        # 正文缩进 = 箭头宽度 + 箭头右侧间距，跟标题文字左对齐
        self.para.pack(fill="x", padx=(S(self._chev_w) + SP.sm, 0),
                       pady=(SP.xs, 0))
        if self.open:
            self.body.pack(fill="x")

        for w in (self.head, self.lbl, self.chev):
            w.bind("<Button-1>", lambda e: self.toggle())
            w.bind("<Enter>", lambda e: self._set_hover(True))
            w.bind("<Leave>", lambda e: self._set_hover(False))
        self.lbl.bind("<FocusIn>", lambda e: self._focus(True))
        self.lbl.bind("<FocusOut>", lambda e: self._focus(False))
        try:
            self.lbl.configure(takefocus=1)
        except Exception:
            pass
        self.lbl.bind("<Return>", lambda e: self.toggle())
        self.lbl.bind("<space>", lambda e: self.toggle())

    def _paint(self):
        self.chev.delete("all")
        col = self.pal.text if self._hover else self.pal.text_tertiary
        cx, cy = S(7), S(7)
        # 一个 90° 的 V 形，展开时转成向下
        a = self._angle
        import math

        rad = math.radians(a)
        for sgn in (-1, 1):
            dx = math.cos(rad + sgn * math.radians(48)) * S(4.2)
            dy = math.sin(rad + sgn * math.radians(48)) * S(4.2)
            self.chev.create_line(cx, cy, cx + dx, cy + dy, fill=col,
                                  width=STK.icon_thin, capstyle="round")

    def _focus(self, on: bool):
        self.lbl.configure(fg=self.pal.text if on else self.pal.text_tertiary)

    def _set_hover(self, on: bool):
        if self._hover == on:
            return
        self._hover = on
        self.lbl.configure(fg=self.pal.text_secondary if on else self.pal.text_tertiary)
        self._paint()

    def toggle(self):
        self.open = not self.open
        start = self._angle
        target = 90.0 if self.open else 0.0
        if self.open:
            self.body.pack(fill="x")

        def frame(t):
            self._angle = start + (target - start) * t
            self._paint()

        def done():
            if not self.open:
                self.body.pack_forget()

        anim.tween(self, "chev", MOTION.base, frame, anim.ease_out_cubic,
                   interval=MOTION.interval, on_done=done)


# ---------------------------------------------------------------- 提示条


class Toast(tk.Frame):
    """右下角浮出的提示条。

    带一条进度线：它同时表达「还剩多久消失」和「刚刚发生了一件事」，
    比单纯淡出更有信息量。

    进场用 place 的位置做动画，而不是在画布内偏移绘制：
    画布内的偏移会被画布边界裁掉，滑入的那一截根本看不见。
    """

    ICONS = {"ok": "check", "warn": "alert", "error": "alert", "info": "info"}

    def __init__(self, master, pal: theme.Palette):
        self.pal = pal
        super().__init__(master, bg=master.cget("bg"))
        self._cw = S(DIM.TOAST_W)
        self._ch = S(DIM.TOAST_H)
        self.cv = tk.Canvas(self, bg=master.cget("bg"), highlightthickness=0,
                             bd=0, width=self._cw, height=self._ch)
        self.cv.pack()
        self._kind = "ok"
        self._text = ""
        self._lines = []
        self._job = None
        self._progress = 1.0
        self._tick_job = None
        self._rest = S(20)      # 静止时距右下角的距离

    # -- 布局 --------------------------------------------------------
    def _place(self, dy: int = 0) -> None:
        try:
            self.place(relx=1.0, rely=1.0, anchor="se",
                       x=-self._rest, y=-self._rest + dy)
            self.lift()
        except tk.TclError:
            return

    def _wrap(self, text: str, f) -> list[str]:
        maxw = self._cw - SP.xl * 2 - DIM.ICON - SP.md
        out, line = [], ""
        for ch in text:
            if measure(self, f, line + ch) > maxw and line:
                out.append(line)
                line = ch
            else:
                line += ch
        if line:
            out.append(line)
        return out[:4] or [""]

    def _fit(self) -> None:
        f = theme.font(self, theme.TS["small"])
        self._lines = self._wrap(self._text, f)
        self._ch = max(S(DIM.TOAST_H), S(18) + S(20) * len(self._lines))
        try:
            self.cv.configure(width=self._cw, height=self._ch)
        except tk.TclError:
            return

    # -- 绘制 --------------------------------------------------------
    def _draw(self) -> None:
        cv = self.cv
        cv.delete("all")
        p = self.pal
        w, h = self._cw, self._ch
        r = S(RADIUS_LG)
        draw_shadow(cv, S(1), S(1), w - S(1), h - S(1), r, p, depth=S(6))
        round_rect(cv, S(0.5), S(0.5), w - S(0.5), h - S(0.5), r,
                   fill=p.bg_card, outline=p.border, width=STK.control)

        color = {"ok": p.success, "warn": p.warning, "error": p.danger,
                 "info": p.text_secondary}.get(self._kind, p.success)
        icon = self.ICONS.get(self._kind, "info")
        cy = h / 2
        draw_icon(cv, icon, SP.lg, cy - S(8), DIM.ICON, color, STK.icon_bold)

        f = theme.font(self, theme.TS["small"])
        tx = SP.lg + DIM.ICON + SP.md
        n = len(self._lines)
        for i, line in enumerate(self._lines):
            ly = cy + (i - (n - 1) / 2) * S(20)
            cv.create_text(tx, ly, anchor="w", text=line, fill=p.text, font=f)

        if self._progress < 0.999:
            pw = (w - S(2)) * max(0.0, self._progress)
            round_rect(cv, S(1), h - S(3), S(1) + pw, h - S(1),
                       S(1), fill=theme.mix(p.bg_card, color, 0.5), outline="")

    # -- 显示 --------------------------------------------------------
    def show(self, text: str, kind: str = "ok", ms: int = 2600) -> None:
        self._kind = kind
        self._text = text
        self._progress = 1.0
        self._fit()
        self._draw()
        self._place(S(14))

        def frame(t):
            self._place(int(S(14) * (1 - t)))

        anim.tween(self, "in", MOTION.base, frame, anim.ease_out_expo,
                   interval=MOTION.interval)

        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        self._job = self.after(ms, self.hide)
        self._start_progress(ms)

    def _start_progress(self, ms: int) -> None:
        if self._tick_job is not None:
            try:
                self.after_cancel(self._tick_job)
            except Exception:
                pass
            self._tick_job = None
        if anim.reduced():
            self._progress = 1.0
            return
        steps = max(1, ms // 60)

        def tick(i=0):
            if i >= steps:
                self._tick_job = None
                return
            self._progress = 1.0 - (i + 1) / steps
            self._draw()
            try:
                self._tick_job = self.after(60, tick, i + 1)
            except tk.TclError:
                self._tick_job = None

        tick()

    def hide(self) -> None:
        for attr in ("_job", "_tick_job"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)

        def frame(t):
            self._place(int(S(14) * t))

        def done():
            try:
                self.place_forget()
            except tk.TclError:
                pass

        anim.tween(self, "out", MOTION.base, frame, anim.ease_out_cubic,
                   interval=MOTION.interval, on_done=done)


# ---------------------------------------------------------------- 指标


class Metric(tk.Frame):
    """一个数字 + 一句标签。运行状态页顶部那一排。"""

    def __init__(self, master, pal: theme.Palette, label: str, value: str,
                 color: str | None = None, bg: str | None = None):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg)
        self.val = tk.Label(self, text=value, bg=self._bg,
                            fg=color or pal.text, anchor="w",
                            font=theme.font(self, theme.TS["title"], "bold"))
        self.val.pack(fill="x")
        tk.Label(self, text=label, bg=self._bg, fg=pal.text_tertiary, anchor="w",
                 font=theme.font(self, theme.TS["micro"])).pack(
            fill="x", pady=(SP.xxs, 0))

    def set_value(self, value: str, color: str | None = None) -> None:
        self.val.configure(text=value, fg=color or self.pal.text)


# ---------------------------------------------------------------- 时间轴


class Timeline(tk.Frame):
    """运行状态页的发送记录。左侧一条竖线串起若干节点。"""

    def __init__(self, master, pal: theme.Palette, bg: str | None = None):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg)
        self.rows: list[dict] = []
        self._limit: int | None = None
        self.cv = tk.Canvas(self, bg=self._bg, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Configure>", lambda e: self._paint(self.cv))

    def set_rows(self, rows: list[dict]) -> None:
        self.rows = rows
        self._limit = None
        self._paint(self.cv)

    def _paint(self, cv: tk.Canvas) -> None:
        cv.delete("all")
        p = self.pal
        w = cv.winfo_width() or S(480)
        if w <= 1:
            w = S(480)
        rows = self.rows
        if self._limit is not None:
            rows = rows[: self._limit]
        if not rows:
            cv.create_text(w / 2, S(28), text="还没有发送记录", fill=p.text_tertiary,
                           font=theme.font(self, theme.TS["small"]))
            cv.configure(height=S(64))
            return

        y = S(10)
        lx = S(6)
        for i, r in enumerate(rows):
            okv = bool(r.get("ok"))
            color = p.success if okv else p.danger
            # 节点之间的连线
            if i > 0:
                cv.create_line(lx, y - S(10), lx, y, fill=p.border,
                               width=STK.rule)
            cv.create_oval(lx - S(3), y - S(3), lx + S(3), y + S(3),
                           fill=color, outline=color)
            cv.create_text(lx + S(14), y, anchor="w", text=r.get("time", ""),
                           fill=p.text_tertiary,
                           font=theme.font(self, theme.TS["micro"], mono=True))
            tx = lx + S(62)
            chan = r.get("channel") or ""
            if chan:
                fc = theme.font(self, theme.TS["small"], "bold")
                cv.create_text(tx, y, anchor="w", text=chan, fill=p.text_secondary,
                               font=fc)
                tx += measure(cv, fc, chan) + SP.md
            msg = r.get("msg") or r.get("summary") or ""
            cv.create_text(tx, y, anchor="w",
                           text=elide(cv, theme.font(self, theme.TS["small"]),
                                      msg, w - tx - SP.md),
                           fill=p.text if okv else p.danger,
                           font=theme.font(self, theme.TS["small"]))
            y += S(30)
        cv.configure(height=y + S(6))

    def reveal(self) -> None:
        """逐行浮现。只在进入运行状态页时跑一次。

        做法是控制「画到第几行」而不是给每行加一个控件：
        行本身是画布上的图元，多建一层控件只为做动画是纯浪费。
        """
        if anim.reduced() or not self.rows:
            return
        total = len(self.rows)

        def frame(t):
            self._limit = max(1, int(round(t * total)))
            self._paint(self.cv)

        def done():
            self._limit = None
            self._paint(self.cv)

        anim.tween(self, "rev", MOTION.slow, frame, anim.ease_out_expo,
                   interval=MOTION.interval, on_done=done)


# ---------------------------------------------------------------- 空状态


class EmptyState(tk.Frame):
    """空状态：一个图标、一句标题、一段说明，以及一个出口动作。

    出口动作是硬要求：没有动作的空状态是死胡同，用户只能退回去。
    """

    def __init__(self, master, pal: theme.Palette, icon: str, title: str,
                 desc: str = "", bg: str | None = None,
                 action: tuple[str, Callable] | None = None):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg)

        # 圆角方块边长 = 图标盒的 2 倍，图标在方块里居中
        box_w = DIM.ICON_BOX * 2
        box = tk.Canvas(self, width=S(box_w), height=S(box_w), bg=self._bg,
                        highlightthickness=0, bd=0)
        box.pack(pady=(SP.xl, SP.md))
        round_rect(box, S(2), S(2), S(box_w - 2), S(box_w - 2),
                   S(DIM.ICON_BOX - 2),
                   fill=pal.bg_subtle, outline=pal.border, width=STK.control)
        inset = S(DIM.ICON_BOX - DIM.ICON_LG // 2)
        draw_icon(box, icon, inset, inset, S(DIM.ICON_LG), pal.text_tertiary,
                  STK.icon_thin)

        tk.Label(self, text=title, bg=self._bg, fg=pal.text,
                 font=theme.font(self, theme.TS["body"], "bold")).pack()
        if desc:
            Paragraph(self, pal, desc, self._bg, pal.text_tertiary,
                      size=theme.TS["small"], wrap=380).pack(pady=(SP.xs, 0))
        if action:
            text, cb = action
            Button(self, text, pal, command=cb, variant="secondary",
                   bg=self._bg).pack(pady=(SP.lg, SP.xl))
