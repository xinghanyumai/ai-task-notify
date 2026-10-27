"""自绘控件库。

Tkinter 原生控件做不出圆角、层次和克制的交互反馈，所以这里用 Canvas
手工绘制一套。约定：

- 每个控件构造时都要拿到当前调色板 `pal` 和父容器底色 `bg`
  （Canvas 不能真透明，必须知道「自己压在哪一层上」）
- 尺寸一律走 `theme.S()` / 令牌换算，保证高 DPI 下比例正确
- 所有状态切换（hover / 按下 / 选中）都走 anim.tween 做过渡，
  而不是瞬间跳变——这是「手感」和「廉价感」的分界线

P1 这一版的设计取向（相对上一版）
--------------------------------
1. **去卡片化。** 上一版每个区块套一张描边卡片，卡片里还套卡片，一屏下来
   边框比内容还多。这一版把所有 `Card` 换成 `Group`：**分组标题 + 行列表 +
   底部 1px 分隔线，不用边框**。分组靠留白和分隔线，不靠盒子。
   判据是全站卡片数量 = 0（启动层除外）。

2. **主操作是墨色，hover 才出现渐变。** 绿色只出现在「系统状态」上：
   开关打开、连接正常、发送成功。凡是「点一下执行」，一律墨色。
   墨色按钮的 hover 态允许加一层自上而下的线性渐变（≤36px 高，在 P1 1.4
   的装饰配额内），因为小面积的渐变是「精致」，大面积就是「卡」和「脏」。

3. **字号只有四级。** 17 页面名 / 13 区块标题 / 10 正文 / 9 辅助。
   上一版 11/10/9/8 挤在 4pt 内，等于没有层级。

4. **装饰是有配额的，不是禁令。** 渐变、光晕、装饰图标都允许，但必须过
   「减法 / 焦点 / 面积」三个测试。`linear_gradient()` 在面积超过
   320×200 时**直接抛错**，就是为了让「渐变铺满背景」这种写法在开发期
   就撞墙，而不是等到用户看见卡顿。
"""

from __future__ import annotations

import math
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

# 测量结果缓存。(字体元组, 文本) → 像素宽度。
#
# 为什么要缓存：`elide()` 对每个超长字符串要做 log2(n) 次测量，一页几十行
# 就是几百次 `font measure`。每次都是一趟 Tcl 调用，单独看 0.07ms 不算什么，
# 乘以几百次就是几十毫秒——profile 里 status 页的 elide 占到 50%。
# 缓存键是 (font, text)：同一个字体的同一段文字，在任何 DPI、任何控件上
# 宽度都一样，跨页复用完全安全。
_mcache: dict[tuple, int] = {}
_MCACHE_MAX = 8192


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
    key = (font, text)
    hit = _mcache.get(key)
    if hit is not None:
        return hit
    cv = _probe(widget)
    if cv is None:
        return len(text) * 7
    try:
        value = int(cv.tk.call("font", "measure", font, text))
    except Exception:
        value = len(text) * 7
    if len(_mcache) > _MCACHE_MAX:
        _mcache.clear()
    _mcache[key] = value
    return value


def measure_cache_clear() -> None:
    """DPI 变了之后要清一次——同一段文字在 150% 缩放下宽度不一样。"""
    _mcache.clear()


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


# 渐变单块的面积上限（逻辑像素）。320×200 是 P1 1.4 给的配额。
#
# 为什么要有这个闸门：Tkinter 的渐变是逐线绘制的，320×200 约 200 条线、
# 2ms 可以接受；铺满 1200×780 要 780 条线，而且 resize 时每次重画都会卡。
# 写成一个会抛错的硬限制，比写一句「请注意不要滥用」有用得多——
# 违规的写法在开发期就炸，不会活到用户面前。
GRAD_MAX_W = 320
GRAD_MAX_H = 200


def linear_gradient(cv: tk.Canvas, box, c1: str, c2: str,
                    steps: int | None = None, tags: str = "",
                    horizontal: bool = False, radius: float = 0):
    """在 box=(x1,y1,x2,y2) 里画一块线性渐变。

    面积超过 320×200（逻辑像素）直接抛 `ValueError`——见 `GRAD_MAX_W` 的说明。
    `radius > 0` 时按圆角矩形裁剪（逐行算出该行的左右边界，圆角段用弦长收缩）。

    只给三个地方用：启动层背景、主按钮 hover、强调块。
    """
    x1, y1, x2, y2 = box
    w, h = abs(x2 - x1), abs(y2 - y1)
    if w * h > S(GRAD_MAX_W) * S(GRAD_MAX_H):
        raise ValueError(
            f"渐变面积 {int(w)}×{int(h)} 超过配额 {GRAD_MAX_W}×{GRAD_MAX_H}："
            "大面积渐变在 Tkinter 上既慢又脏，请改用纯色或降低尺寸"
        )
    if w <= 0 or h <= 0:
        return
    span = h if not horizontal else w
    n = steps if steps is not None else max(2, int(span))
    n = max(2, min(n, int(span) + 1))
    r = max(0, min(radius, w / 2, h / 2)) if radius else 0

    for i in range(n):
        col = theme.mix(c1, c2, i / (n - 1))
        if horizontal:
            x = x1 + i * w / n
            cv.create_line(x + w / (2 * n), y1, x + w / (2 * n), y2,
                           fill=col, width=max(1, int(round(w / n)) + 1),
                           tags=tags or ())
            continue
        y = y1 + i * h / n
        inset = 0.0
        if r:
            if y < y1 + r:
                d = r - (y - y1)
                inset = r - max(0.0, r * r - d * d) ** 0.5
            elif y > y2 - r:
                d = r - (y2 - y)
                inset = r - max(0.0, r * r - d * d) ** 0.5
        cv.create_line(x1 + inset, y + h / (2 * n), x2 - inset, y + h / (2 * n),
                       fill=col, width=max(1, int(round(h / n)) + 1),
                       tags=tags or ())


def round_rect_grad(cv: tk.Canvas, x1, y1, x2, y2, r, c_top: str, c_bottom: str,
                    tags: str = ""):
    """圆角矩形 + 垂直渐变。`linear_gradient` 的圆角版本。"""
    linear_gradient(cv, (x1, y1, x2, y2), c_top, c_bottom, tags=tags, radius=r)


def draw_shadow(cv: tk.Canvas, x1, y1, x2, y2, r, pal: theme.Palette,
                depth: int | None = None, tags: str = ""):
    """两层偏移圆角矩形模拟柔和投影（Tk 没有高斯模糊）。

    **常规组件里只有 Toast 用它。** 正文区一律无阴影：卡片已经去掉了，
    再加阴影等于把「盒子」用另一种方式装回来。启动层是例外，那里允许。
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


def divider(parent, color: str) -> tk.Frame:
    """不占画布的 1px 分隔线。

    `Group` 的底部分隔线用它而不是 Canvas：一个 Canvas 就是一个 Tk 窗口，
    一页十个分组就是十个多余的重绘面。纯 Frame + 1px 背景几乎零成本。
    """
    return tk.Frame(parent, height=STK.rule, bg=color)


def glow(cv: tk.Canvas, cx: float, cy: float, r: float, pal: theme.Palette,
         color: str | None = None, layers: int = 4, tags: str = ""):
    """光晕：同心圆递减色阶（Tk 没有 alpha，只能靠混色近似）。

    只给启动层和「连接成功」这类品牌色状态强调用，每页 ≤2 处。
    边缘一定会有色带——这是 Tkinter 的能力边界，接受它，别硬做。
    """
    base = color or pal.brand
    for i in range(layers, 0, -1):
        rr = r * i / layers
        col = theme.mix(pal.bg_app, base, 0.05 + 0.04 * (layers - i))
        cv.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                       fill=col, outline=col, tags=tags or ())


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

    - **primary** 墨色实心，hover 时出现自上而下的线性渐变（≤36px 高）。
      不是品牌绿。理由见模块 docstring 第 2 条：彩色实心按钮是「消费级」
      最强的信号，墨色按钮读起来是专业工具。
    - **secondary** 描边，用于「测试」「重新检测」这类并列动作。
    - **danger** 浅红底 + 红字，用于破坏性操作。
    - **ghost** 无底无框，只在 hover 时浮出底色，用于行内轻量动作。

    按下时整体下移 1px。Tk 没有真正的 scale 缩放，位移是唯一的替代方案。
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

        # 主按钮：hover 时铺一层自上而下的渐变（上亮下暗，光源在上方）。
        # 两端色按 _hv 从 ink 混过去，所以「渐变的出现」本身就是那 90ms
        # 过渡的一部分，不需要单独做透明度动画（Tk 也没有逐控件 alpha）。
        # 高度必须 ≤ BTN_H(36)：这是 P1 1.4 给按钮渐变的配额上限。
        grad = (self.variant == "primary" and self._enabled
                and not self._loading and self._hv > 0.02
                and h <= S(DIM.BTN_H))
        if grad:
            c1 = theme.mix(p.ink, p.ink_top, self._hv)
            c2 = theme.mix(p.ink, p.ink_bottom, self._hv)
            if self._press:
                c1 = theme.mix(c1, p.ink_active, 0.6)
                c2 = theme.mix(c2, p.ink_active, 0.6)
            if c1 == c2:
                grad = False
            else:
                round_rect_grad(self, S(0.5), S(0.5) + dy, w - S(0.5),
                                h - S(0.5) + dy, r, c1, c2)
        if not grad:
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
            draw_icon(self, self.icon, left, cy - icon_w / 2, icon_w, fg,
                      STK.icon_thin)
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

    深色下轨道要加一圈内描边：深色背景上「浅灰轨道」和「底色」的明度差
    只有一点点，不开描边时关态开关几乎看不见边界，用户不知道那里有东西。
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
        # 深色下再补一圈内描边（向内缩 1px 的细线），把轨道从底色里拉出来
        if p.name == "dark":
            round_rect(self, S(1.5), y1 + S(1), self.w - S(1.5), y2 - S(1),
                       (y2 - y1 - S(2)) / 2, fill="",
                       outline=theme.mix(p.border_strong, track, t),
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
        anim.tween(self, "knob", duration or MOTION.quick, frame,
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
    """方形勾选框，选中时填充墨色并画 `on_ink` 色的勾。

    和 Switch 用同一套视觉语言：墨色填充、圆角、hover 底色过渡。
    区别只在形状——开关表达「持续状态」，勾选框表达「一次性确认」。
    """

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
    """左侧导航项：16px 图标 + 文字 + 选中浅底胶囊。

    **没有左侧指示条。** v1 在选中项左边画一根 2px 竖线，看起来像「书签」，
    和列表行的状态点、开关的轨道挤在一起，一屏三条竖线，谁也不是重点。
    P1 把它去掉了：选中态只靠「浅底胶囊 + 文字转粗」表达，一个信号足够。

    为什么不用品牌绿：绿色表达「系统状态」（哪条通道开着），
    当前位置不是状态，它是导航位置。两者用同一个颜色会让用户分不清
    「我在这一页」和「这个功能开着」。
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

        ic = DIM.SIDEBAR_ICON
        ix = SP.md
        iy = (self.h - ic) / 2
        draw_icon(self, self.icon_name, ix, iy, ic, fg,
                  STK.icon if self.selected else STK.icon_thin)
        self.create_text(ix + ic + SP.md, self.h / 2, anchor="w", text=self.label,
                         fill=fg, font=theme.font(self, theme.TS["body"], weight))

        if self.badge:
            bw = max(S(20), measure(self, theme.font(self, theme.TS["small"]),
                                    self.badge) + SP.sm)
            round_rect(self, w - SP.md - bw, self.h / 2 - S(9),
                       w - SP.md, self.h / 2 + S(9), S(RADIUS_PILL),
                       fill=p.brand, outline=p.brand)
            self.create_text(w - SP.md - bw / 2, self.h / 2, text=self.badge,
                             fill=p.on_brand,
                             font=theme.font(self, theme.TS["small"], "bold"))

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
                          else theme.TS["small"])
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
    """输入框，三态：默认 / 聚焦 / 错误。

    - 默认：`border_control` 1px（3.5:1，满足 WCAG 1.4.11 对控件边界的 3:1）
    - 聚焦：`border_focus` 2px + 一圈极淡外发光，90ms 过渡
    - 错误：`danger` 2px + 下方一行红字说明

    为什么默认态不用更淡的 `border`：那个色在白底只有 1.2:1，输入框的边界
    等于不存在。P1 第六节写的是「描边从 border → border_control」，但那条
    和第三节第 4 条「非文本对比度不许退步」冲突，这里以可访问性为准。

    错误态必须有**一行说明**，不能只把边框染红：颜色是辅助信号，
    色盲用户看不出红边，文字才是信息本身。
    """

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
        self._error = ""
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

        # 错误说明：常驻一个 Label，没有错误时 pack_forget。
        # 不每次都新建 Label 是因为「错误」会在每次输入时被清掉再设回来，
        # 反复创建销毁控件比隐藏一个贵一个数量级。
        self.err_lbl = tk.Label(self, text="", bg=self._bg, fg=pal.danger,
                                anchor="w", justify="left",
                                font=theme.font(self, theme.TS["small"]))

        self.cv.bind("<Configure>", self._on_conf)
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self.entry.bind("<KeyRelease>", lambda e: self._update_placeholder())
        # 点画布空白处也能聚焦到输入框
        self.cv.bind("<Button-1>", lambda e: self.entry.focus_set())
        self._update_placeholder()

    def _border(self) -> tuple[str, int]:
        """当前描边颜色与粗细。错误 > 聚焦 > 默认。"""
        p = self.pal
        if self._error:
            return p.danger, STK.ring
        t = self._focus_t
        if t <= 0.02:
            return p.border_control, STK.control
        return theme.mix(p.border_control, p.border_focus, t), (
            STK.ring if t > 0.5 else STK.control)

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
        border, bw = self._border()
        t = self._focus_t
        if t > 0.02 and not self._error:
            # 聚焦时加一圈极淡的外发光，边界更清楚
            round_rect(self.cv, -S(1.5), -S(1.5), w + S(1.5), h + S(1.5),
                       S(RADIUS_MD + 2),
                       fill=theme.mix(self.pal.bg_input, self.pal.brand_soft,
                                      t * 0.55),
                       outline="", tags="fieldbg")
        round_rect(self.cv, S(0.5), S(0.5), w - S(0.5), h - S(0.5), S(RADIUS_MD),
                   fill=self.pal.bg_input, outline=border, width=bw,
                   tags="fieldbg")
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

    # -- 错误态 ------------------------------------------------------
    def set_error(self, text: str) -> None:
        """把输入框切到错误态，并在下方显示一行说明。

        空字符串 = 清除错误。幂等：状态没变就什么都不做，因为
        「每次按键都重新校验」会一路调到这里。
        """
        text = text or ""
        if text == self._error:
            return
        self._error = text
        if text:
            self.err_lbl.configure(text=text)
            self.err_lbl.pack(fill="x", pady=(SP.xs, 0))
        else:
            self.err_lbl.pack_forget()
        self._on_conf()

    def clear_error(self) -> None:
        self.set_error("")

    @property
    def has_error(self) -> bool:
        return bool(self._error)

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
        """内容整体轻轻归位（切页动画）。

        用 yview 做位移是行不通的（内容没超出时没有滚动空间），
        所以这里移动 inner 在画布上的坐标，动画结束后归零。

        P1 第六节第 2 条：切页是**整页 8px 上移就位**，不再逐张卡片错峰。
        所以调用方传 dy=SP.sm(8)、dx=0，时长 MOTION.base(160)。
        """
        if anim.reduced():
            return
        d = S(dy if dy is not None else SP.sm)
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


# ---------------------------------------------------------------- 分组


class Group(tk.Frame):
    """分组：标题 + 内容区 + 底部 1px 分隔线。**没有边框，没有卡片底。**

    这是 P1 相对 v1 最大的结构改动，也是「去卡片化」的落点。

    为什么去卡片化：v1 每个区块套一张描边卡片，卡片里还套卡片，
    一屏下来边框比内容还多。边框本来是「这里是一组」的提示，但**当所有
    东西都有边框时，边框就不再区分任何东西**，只剩视觉噪声。分组真正的
    信号是留白：一组和一组的间距（24px）远大于组内行间距（0），
    人眼自然就切开了。

    分隔线只画在底部，不画在顶部：一页里第一个分组的上方是顶栏，
    再画一条线就是第三条横线（顶栏、分组、分组…），显得碎。

    `Section` / `Card` 是旧名，保留为别名，方便逐步替换。
    """

    def __init__(self, master, pal: theme.Palette, title: str = "",
                 desc: str = "", bg: str | None = None, pad: int | None = None,
                 gap: int | None = None, rule: bool = True,
                 inset: bool = False, **kw):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        self.title_text = title
        self.desc_text = desc
        self.inset = bool(inset)
        super().__init__(master, bg=self._bg)

        self.head = None
        self.title_lbl = None
        self.desc_lbl = None
        if title or desc:
            # 分组标题上方 24px、下方 8px（P1 4.1 页 2 的版式图）。
            # 这个 24 就是「组与组之间的距离」，是去卡片化之后唯一的分组信号，
            # 所以它比组内任何间距都大。
            self.head = tk.Frame(self, bg=self._bg)
            self.head.pack(fill="x", pady=(SP.xl if gap is None else S(gap),
                                           SP.sm))
            if title:
                self.title_lbl = tk.Label(
                    self.head, text=title, bg=self._bg, fg=pal.text_secondary,
                    anchor="w", font=theme.font(self, theme.TS["section"], "bold"))
                self.title_lbl.pack(fill="x")
            if desc:
                self.desc_lbl = tk.Label(
                    self.head, text=desc, bg=self._bg, fg=pal.text_tertiary,
                    anchor="w", justify="left", wraplength=S(540),
                    font=theme.font(self, theme.TS["small"]))
                self.desc_lbl.pack(fill="x", pady=(SP.xxs, 0))

        self.body = tk.Frame(self, bg=self._bg)
        if self.inset:
            # 强调块：唯一允许带底色的「块」。P1 1.4 的配额是 ≤320×200，
            # 这里只用在「队列为空」「这个通道不需要额外配置」这类
            # 需要和普通行区分开的短说明上。
            self.body.configure(bg=pal.bg_subtle)
            self.body.pack(fill="both", expand=True,
                           padx=S(DIM.CARD_PAD), pady=S(DIM.CARD_PAD))
        else:
            self.body.pack(fill="both", expand=True,
                           padx=S(pad) if pad else 0,
                           pady=(0, SP.lg))

        self._rule = None
        if rule:
            self._rule = divider(self, pal.border)
            self._rule.pack(fill="x")

    # -- 兼容旧 Card 的接口 -----------------------------------------
    def set_offset(self, dy: float) -> None:
        """旧接口：卡片错峰进场用。P1 取消了逐张动画，这里保留为空实现，
        免得调用方一改就炸；第 3 步重写 ui.py 后会被删掉。"""
        return

    def restyle(self) -> None:
        """调色板换了之后重刷文字与分隔线颜色。"""
        p = self.pal
        self.configure(bg=self._bg)
        if self.head is not None:
            self.head.configure(bg=self._bg)
        self.body.configure(bg=self._bg)
        if self.title_lbl is not None:
            self.title_lbl.configure(bg=self._bg, fg=p.text_secondary)
        if self.desc_lbl is not None:
            self.desc_lbl.configure(bg=self._bg, fg=p.text_tertiary)
        if self._rule is not None:
            self._rule.configure(bg=p.border)


class Section(Group):
    """兼容旧名：分节 = 分组。第三个位置参数仍是标题。"""

    def __init__(self, master, pal: theme.Palette, title: str = "",
                 desc: str = "", bg: str | None = None, gap: int | None = None,
                 **kw):
        super().__init__(master, pal, title=title, desc=desc, bg=bg, gap=gap, **kw)


# 旧名 Card 也留着：ui.py 里还有一处，第 3 步会一起换掉
Card = Group


# ---------------------------------------------------------------- 设置行


class Row(tk.Frame):
    """设置行：左边标签（可选一行说明），右边控件。**固定 52px 高。**

    52 是「专业工具」的密度基准（Linear / Raycast 都在 44–56 之间）：
    比 46 松一档，扫读时一行一个信息不串行；再高就变成消费级 App 的
    「大圆角列表」，一屏放不下几个设置。

    用 grid 而不是 pack 是为了让右侧控件在 52px 里**垂直居中**：
    pack 默认把子控件顶到上边，需要算 pady 才能居中，而 pady 只能用
    令牌档位，凑不出精确的居中。grid 的 `sticky="w"`（不带 n/s）
    本身就是垂直居中，不用算。
    """

    def __init__(self, master, pal: theme.Palette, label: str, hint: str = "",
                 bg: str | None = None, wrap: int = 420):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg, height=DIM.ROW_H)
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.left = tk.Frame(self, bg=self._bg)
        self.left.grid(row=0, column=0, sticky="nsew")
        self.left.grid_columnconfigure(0, weight=1)
        self.left.grid_rowconfigure(0, weight=1)

        self.label = tk.Label(self.left, text=label, bg=self._bg, fg=pal.text,
                              anchor="w", justify="left",
                              font=theme.font(self, theme.TS["body"]))
        self.label.grid(row=0, column=0, sticky="w")
        self.hint = None
        if hint:
            self._add_hint(hint, wrap)

        self.right = tk.Frame(self, bg=self._bg)
        self.right.grid(row=0, column=1, sticky="e", padx=(SP.lg, 0))

    def _add_hint(self, text: str, wrap: int = 420) -> None:
        self.left.grid_rowconfigure(0, weight=1)
        self.left.grid_rowconfigure(1, weight=1)
        self.label.grid_configure(row=0, sticky="sw")
        self.hint = tk.Label(self.left, text=text, bg=self._bg,
                             fg=self.pal.text_tertiary, anchor="w",
                             justify="left", wraplength=S(wrap),
                             font=theme.font(self, theme.TS["small"]))
        self.hint.grid(row=1, column=0, sticky="nw")

    def set_hint(self, text: str) -> None:
        if self.hint is None:
            if not text:
                return
            self._add_hint(text)
            return
        self.hint.configure(text=text)

    def set_label(self, text: str) -> None:
        self.label.configure(text=text)


# ---------------------------------------------------------------- 列表行


class ListRow(tk.Canvas):
    """列表行：左侧图标徽章 + 标题 + 副标题 + 右侧状态点。

    用于通道列表。选中态是浅底 + 文字转粗，**没有左侧指示条**——
    理由同 NavItem：一屏里不要有第二套「当前位置」的语言。

    默认 44px（P1 4.1 页 1 的通道项高度），比设置行 52 紧一档：
    左栏是「浏览」，右栏是「编辑」，浏览要更密。
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
        self.h = S(height if height is not None else DIM.NAV_ITEM_H)
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
        fs = theme.font(self, theme.TS["small"])
        fg = p.text if self.active else theme.mix(p.text_secondary, p.text, self._hv)
        cy = self.h / 2
        if self.subtitle:
            self.create_text(tx, cy - S(7), anchor="w", fill=fg, font=ft,
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
        self.font_size = font_size or size or theme.TS["small"]
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
    """教程页的一步：22px 序号圆点 + 标题 + 正文。

    P1 4.1 页 5：序号圆点 22px，**序号与正文左对齐**——圆点和标题、正文
    共用同一条左基线，而不是「圆点占一列、文字缩进一列」。缩进式排版
    在只有四步的短教程里会显得很空。
    """

    def __init__(self, master, pal: theme.Palette, index: int = 0, title: str = "",
                 body: str = "", bg: str | None = None, i: int | None = None,
                 last: bool = False):
        if i is not None:
            index = i
        p = pal
        self.pal = pal
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg)

        d = S(22)
        self.mark = tk.Canvas(self, width=d, height=d, bg=self._bg,
                              highlightthickness=0, bd=0)
        self.mark.pack(anchor="w", pady=(SP.xxs, SP.sm))
        round_rect(self.mark, 0.5, 0.5, d - 0.5, d - 0.5, d / 2,
                   fill=p.bg_subtle, outline=p.border, width=STK.control)
        self.mark.create_text(d / 2, d / 2, text=str(index), fill=p.text_secondary,
                              font=theme.font(self, theme.TS["small"], "bold"))

        tk.Label(self, text=title, bg=self._bg, fg=p.text, anchor="w",
                 font=theme.font(self, theme.TS["body"], "bold")).pack(fill="x")
        if body:
            Paragraph(self, p, body, self._bg, p.text_secondary,
                      size=theme.TS["small"], wrap=620).pack(fill="x",
                                                             pady=(SP.xs, 0))


# ---------------------------------------------------------------- 折叠


class Collapsible(tk.Frame):
    """可折叠的说明块。默认收起，展开是**高度渐变**（160ms）+ 箭头旋转 90°。

    高度渐变的做法：给 body 关掉 pack_propagate，手动把高度从 0 补到
    reqheight。Tk 没有 CSS 的 max-height 过渡，这是唯一能做到「内容
    被逐渐推下去」的办法。动画结束后恢复 propagate，让内容自己能长高。
    """

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
                              size=theme.TS["small"], wrap=620)
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
        rad = math.radians(self._angle)
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

        def frame(t):
            self._angle = start + (target - start) * t
            self._paint()

        anim.tween(self, "chev", MOTION.base, frame, anim.ease_out_cubic,
                   interval=MOTION.interval)
        self._animate_height()

    def _animate_height(self) -> None:
        """展开/收起的高度渐变。"""
        if self.open:
            self.body.pack(fill="x")
            self.body.update_idletasks()
            full = max(S(1), self.para.winfo_reqheight() + SP.xs)
            self.body.pack_propagate(False)
            self.body.configure(height=S(1))

            def frame(t):
                try:
                    self.body.configure(height=max(S(1), int(full * t)))
                except tk.TclError:
                    pass

            def done():
                try:
                    self.body.pack_propagate(True)
                    self.body.configure(height=0)
                except tk.TclError:
                    pass

            anim.tween(self, "height", MOTION.base, frame, anim.ease_out_cubic,
                       interval=MOTION.interval, on_done=done)
            return

        start_h = self.body.winfo_height()
        self.body.pack_propagate(False)

        def frame(t):
            try:
                self.body.configure(height=max(0, int(start_h * (1 - t))))
            except tk.TclError:
                pass

        def done():
            try:
                self.body.pack_forget()
                self.body.pack_propagate(True)
                self.body.configure(height=0)
            except tk.TclError:
                pass

        anim.tween(self, "height", MOTION.base, frame, anim.ease_out_cubic,
                   interval=MOTION.interval, on_done=done)


# ---------------------------------------------------------------- 提示条


class Toast(tk.Frame):
    """右下角浮出的提示条。**常规组件里唯一带阴影的。**

    带一条进度线：它同时表达「还剩多久消失」和「刚刚发生了一件事」，
    比单纯淡出更有信息量。

    进场用 place 的位置做动画（从右侧滑入 8px，160ms），而不是在画布内
    偏移绘制：画布内的偏移会被画布边界裁掉，滑入的那一截根本看不见。
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
    def _place(self, dx: int = 0) -> None:
        try:
            self.place(relx=1.0, rely=1.0, anchor="se",
                       x=-self._rest + dx, y=-self._rest)
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
        self._place(S(8))

        def frame(t):
            self._place(int(S(8) * (1 - t)))

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
            self._place(int(S(8) * t))

        def done():
            try:
                self.place_forget()
            except tk.TclError:
                pass

        anim.tween(self, "out", MOTION.base, frame, anim.ease_out_cubic,
                   interval=MOTION.interval, on_done=done)


# ---------------------------------------------------------------- 指标


class Metric(tk.Frame):
    """一个数字 + 一句标签。运行状态页顶部那一排，**不用卡片**，靠竖线分隔。

    数字用等宽字体：P1 1.2-C「数字右对齐、金额/计数用等宽」。等宽让
    「128」和「3」这类位数不同的数字在竖向上对齐，扫一眼就知道量级。
    """

    def __init__(self, master, pal: theme.Palette, label: str, value: str,
                 color: str | None = None, bg: str | None = None):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg)
        self.val = tk.Label(self, text=value, bg=self._bg,
                            fg=color or pal.text, anchor="w",
                            font=theme.font(self, theme.TS["title"], "bold",
                                            mono=True))
        self.val.pack(fill="x")
        tk.Label(self, text=label, bg=self._bg, fg=pal.text_tertiary, anchor="w",
                 font=theme.font(self, theme.TS["small"])).pack(
            fill="x", pady=(SP.xxs, 0))

    def set_value(self, value: str, color: str | None = None) -> None:
        self.val.configure(text=value, fg=color or self.pal.text)


# ---------------------------------------------------------------- 时间轴


class Timeline(tk.Frame):
    """运行状态页的发送记录。左侧一条竖线串起若干节点。

    每行 40px（P1 4.1 页 4），状态用**圆点颜色**表达而不是文字标签——
    「成功/失败」两个字占掉 40px 里的一大半，而颜色一眼就能扫完一列。
    代价是色盲用户读不出来，所以失败行的正文也用 danger 色，
    并且这一列永远只出现在「最近发送」里，上面还有指标的失败计数兜底。

    不带入场动画：P1 第七节禁令 3「不给列表项加逐项动画」。
    所以 `reveal()` 保留为空实现，调用方不必改签名。
    """

    ROW_H = 40

    def __init__(self, master, pal: theme.Palette, bg: str | None = None):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg)
        self.rows: list[dict] = []
        self.cv = tk.Canvas(self, bg=self._bg, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Configure>", lambda e: self._paint(self.cv))

    def set_rows(self, rows: list[dict]) -> None:
        self.rows = rows
        self._paint(self.cv)

    def _paint(self, cv: tk.Canvas) -> None:
        cv.delete("all")
        p = self.pal
        w = cv.winfo_width() or S(480)
        if w <= 1:
            w = S(480)
        rows = self.rows
        if not rows:
            cv.create_text(w / 2, S(28), text="还没有发送记录",
                           fill=p.text_tertiary,
                           font=theme.font(self, theme.TS["small"]))
            cv.configure(height=S(64))
            return

        # 字体在循环外算一次：theme.font 每次都要查族名缓存，
        # 一页二十行就是四十次无谓的字典查找。
        f_small = theme.font(self, theme.TS["small"])
        f_time = theme.font(self, theme.TS["small"], mono=True)
        f_chan = theme.font(self, theme.TS["small"], "bold")

        y = S(self.ROW_H) / 2 + S(4)
        lx = S(6)
        for i, r in enumerate(rows):
            okv = bool(r.get("ok"))
            color = p.success if okv else p.danger
            # 节点之间的连线
            if i > 0:
                cv.create_line(lx, y - S(self.ROW_H), lx, y, fill=p.border,
                               width=STK.rule)
            cv.create_oval(lx - S(3), y - S(3), lx + S(3), y + S(3),
                           fill=color, outline=color)
            cv.create_text(lx + S(14), y, anchor="w", text=r.get("time", ""),
                           fill=p.text_tertiary, font=f_time)
            tx = lx + S(62)
            chan = r.get("channel") or ""
            if chan:
                cv.create_text(tx, y, anchor="w", text=chan, fill=p.text_secondary,
                               font=f_chan)
                tx += measure(cv, f_chan, chan) + SP.md
            msg = r.get("msg") or r.get("summary") or ""
            cv.create_text(tx, y, anchor="w",
                           text=elide(cv, f_small, msg, w - tx - SP.md),
                           fill=p.text if okv else p.danger, font=f_small)
            y += S(self.ROW_H)
        cv.configure(height=y)

    def reveal(self) -> None:
        """旧接口：v1 的逐行浮现。P1 取消（第七节禁令 3），保留为空实现。"""
        return


# ---------------------------------------------------------------- 空状态


class EmptyState(tk.Frame):
    """空状态：图形 + 标题 + 一行说明 + 可选按钮。

    出口动作是硬要求：没有动作的空状态是死胡同，用户只能退回去。
    图形用「圆形浅底 + 线性图标」，不用方形卡片——方框在这个尺寸下
    会被误读成一个还没加载出来的内容块。
    """

    def __init__(self, master, pal: theme.Palette, icon: str, title: str,
                 desc: str = "", bg: str | None = None,
                 action: tuple[str, Callable] | None = None):
        self.pal = pal
        self._bg = bg or master.cget("bg")
        super().__init__(master, bg=self._bg)

        d = S(48)
        box = tk.Canvas(self, width=d, height=d, bg=self._bg,
                        highlightthickness=0, bd=0)
        box.pack(pady=(SP.xl, SP.md))
        box.create_oval(S(1), S(1), d - S(1), d - S(1),
                        fill=pal.bg_subtle, outline=pal.border,
                        width=STK.control)
        inset = (d - S(DIM.ICON_LG * 2)) / 2
        draw_icon(box, icon, inset, inset, S(DIM.ICON_LG * 2),
                  pal.text_tertiary, STK.icon_thin)

        tk.Label(self, text=title, bg=self._bg, fg=pal.text,
                 font=theme.font(self, theme.TS["section"], "bold")).pack()
        if desc:
            Paragraph(self, pal, desc, self._bg, pal.text_tertiary,
                      size=theme.TS["small"], wrap=380).pack(pady=(SP.xs, 0))
        if action:
            text, cb = action
            Button(self, text, pal, command=cb, variant="secondary",
                   bg=self._bg).pack(pady=(SP.lg, SP.xl))
