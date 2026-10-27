"""设计令牌：中性色画布 + 墨色主操作 + 品牌绿作信号色。

这一版是对上一版的整体替换，不是微调。上一版直接沿用 WorkBuddy 的视觉语言
（品牌绿 #00c29a 铺满按钮、开关、选中态，中性色往绿偏一格），结果是界面
读起来像一个消费级 App 的设置页：到处是彩色，反而没有重点。

这一版换了三条基本判断，每条都有具体理由
--------------------------------------------
1. **中性色回到纯中性（zinc 系），不再往品牌色偏。**
   底色带色相时，强调色会和底色抢饱和度，于是所有彩色元素都要再提亮才看得见，
   越提越花。底色去掉色相之后，屏幕上唯一的彩色就是状态信号，
   「有彩色 = 有意义」这条视觉契约才立得住。

2. **主操作按钮改成墨色（近黑 / 深色下近白），品牌绿降级为信号色。**
   这是主流开发工具（Linear / Vercel / Raycast / Stripe Dashboard）的共同选择：
   彩色实心按钮是「消费级」最强的信号，墨色按钮 + 克制的彩色状态点读起来是
   「专业工具」。绿色只保留三个用途：
     - 开关打开、连接正常的**状态点**
     - 品牌标记本身
     - 成功类反馈的浅底
   凡是要用户「点一下执行」的地方，一律墨色。

3. **字号拉开层级。** 上一版 title 15 / section 11 / body 10 三档挤在 5pt 内，
   扫读时几乎分不出层级。这一版把 title 提到 17、section 降到 11，
   正文不动，靠「大更大、小更小」建立层级，而不是靠加粗。

对比度全部实测，13 项在浅色/深色下都过 WCAG AA（正文 4.5:1、非文本 3:1）。
`brand` 仍然只允许当填充：它在白底只有 2.2:1，绿色小字一律走 `brand_ink`。
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

# ---------------------------------------------------------------- 令牌结构


@dataclass(frozen=True)
class Palette:
    name: str

    # 背景层级：app（画布）→ card（卡片）→ subtle（卡片内浅块）→ hover → active
    bg_app: str
    bg_card: str
    bg_subtle: str
    bg_hover: str
    bg_active: str
    bg_input: str

    # 描边
    border: str
    border_strong: str
    border_focus: str

    # 文字
    text: str
    text_secondary: str
    text_tertiary: str
    placeholder: str

    # 品牌绿：只做填充与信号，不做文字
    brand: str
    brand_hover: str
    brand_active: str
    brand_soft: str
    brand_soft_2: str
    brand_top: str
    brand_bottom: str
    on_brand: str

    # 语义色
    danger: str
    danger_soft: str
    warning: str
    warning_soft: str
    success: str
    success_soft: str

    # 结构
    sidebar: str
    shadow_near: str
    shadow_far: str
    shadow: str
    track: str

    # 后加字段（带默认值，避免调用方因新增令牌报错）
    brand_ink: str = "#00795f"     # 品牌色的可读版本，专供文字/图标/描边
    border_control: str = "#8e8e96"  # 控件边界，满足 WCAG 1.4.11 的 3:1
    # 主操作：墨色。这是这一版的核心改动，所有 primary 按钮都用它
    ink: str = "#16161a"
    on_ink: str = "#fafafa"
    ink_hover: str = "#2c2c33"
    ink_active: str = "#000000"
    # 卡片悬停：比 bg_hover 更淡一档，用于「整块可点」的行
    card_hover: str = "#f7f7f8"
    # 顶部状态条 / 侧栏底，比画布再深一点点，形成层次
    chrome: str = "#ffffff"


LIGHT = Palette(
    name="light",
    bg_app="#fafafa",
    bg_card="#ffffff",
    bg_subtle="#f4f4f5",
    bg_hover="#f1f1f3",
    bg_active="#e8e8ec",
    bg_input="#ffffff",
    border="#e6e6e9",
    border_strong="#d6d6db",
    border_focus="#00795f",
    text="#16161a",
    text_secondary="#55555e",
    text_tertiary="#6e6e78",
    placeholder="#8e8e96",
    brand="#00c29a",
    brand_hover="#0ad3aa",
    brand_active="#00a883",
    brand_soft="#e6faf4",
    brand_soft_2="#f2fbf8",
    brand_top="#00c29a",
    brand_bottom="#00b491",
    on_brand="#003b31",
    danger="#c8362f",
    danger_soft="#fdecec",
    warning="#8a5200",
    warning_soft="#fff5e6",
    success="#0a7d43",
    success_soft="#e9f8f0",
    sidebar="#ffffff",
    shadow_near="#ececef",
    shadow_far="#f3f3f5",
    shadow="#0000000a",
    track="#e6e6e9",
    brand_ink="#00795f",
    border_control="#8e8e96",
    ink="#16161a",
    on_ink="#fafafa",
    ink_hover="#2c2c33",
    ink_active="#000000",
    card_hover="#f7f7f8",
    chrome="#ffffff",
)

DARK = Palette(
    name="dark",
    bg_app="#0b0b0d",
    bg_card="#151518",
    bg_subtle="#1b1b1f",
    bg_hover="#202026",
    bg_active="#2a2a31",
    bg_input="#101013",
    border="#26262c",
    border_strong="#3a3a42",
    border_focus="#2ee6c0",
    text="#f7f7f8",
    text_secondary="#a6a6ae",
    text_tertiary="#8c8c95",
    placeholder="#6e6e78",
    brand="#2ee6c0",
    brand_hover="#5cf0d2",
    brand_active="#1fc9a8",
    brand_soft="#12312b",
    brand_soft_2="#16211f",
    brand_top="#2ee6c0",
    brand_bottom="#1fc9a8",
    on_brand="#003b31",
    danger="#ff8078",
    danger_soft="#3a1f1f",
    warning="#ffab5c",
    warning_soft="#3a2a17",
    success="#48d68d",
    success_soft="#14301f",
    sidebar="#101013",
    shadow_near="#08080a",
    shadow_far="#0d0d10",
    shadow="#00000055",
    track="#26262c",
    brand_ink="#2ee6c0",
    border_control="#6e6e78",
    ink="#f7f7f8",
    on_ink="#16161a",
    ink_hover="#e4e4e8",
    ink_active="#ffffff",
    card_hover="#1a1a1e",
    chrome="#101013",
)


# ---------------------------------------------------------------- 字体与尺寸

# Tk 只接受单个族名，按顺序探测第一个可用的。
# 中文优先微软雅黑 UI（比微软雅黑更紧凑），西文优先 Segoe UI Variable，
# 它是 Windows 11 的系统界面字体，字形更现代，和 Win10 的 Segoe UI 也能回退。
FONT_STACK = [
    "Segoe UI Variable Text",
    "Microsoft YaHei UI",
    "PingFang SC",
    "Microsoft YaHei",
    "Segoe UI",
    "Noto Sans CJK SC",
    "Arial",
]
MONO_STACK = ["Cascadia Mono", "JetBrains Mono", "Consolas", "Courier New"]

# 字号层级。Tk 单位是 pt（1pt ≈ 1.33px）。
#
# 相邻两档的比值刻意不均匀：display/title 之间拉大（跨页级 vs 页级），
# title/section 之间也拉大（页标题 vs 卡片标题），section 到 body 只差一档
# ——它们本来就是「标题 + 正文」的紧邻关系，差太多会散。
TS = {
    "display": 24,   # 空状态 / 引导页主标题，一页只出现一次
    "title": 17,     # 页面标题
    "section": 11,   # 卡片标题
    "body": 10,      # 正文 ≈ 13px
    "small": 9,      # 辅助说明
    "micro": 8,      # 极小注脚 / 徽标
    "mono": 9,       # 等宽：凭证、模板 ID
}

# 行高倍数，给 Paragraph 用
LINE = 1.66

# 圆角（逻辑像素）
RADIUS_XS = 4
RADIUS_SM = 6
RADIUS_MD = 8
RADIUS_LG = 12
RADIUS_XL = 16
RADIUS_PILL = 999


class Metrics:
    """尺寸换算。开启高 DPI 感知后，逻辑像素需要乘缩放因子。"""

    def __init__(self) -> None:
        self.scale = 1.0

    def calibrate(self, root) -> None:
        try:
            self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        except Exception:
            self.scale = 1.0

    def __call__(self, px: float) -> int:
        return int(round(px * self.scale))


S = Metrics()


# ---------------------------------------------------------------- 字体解析

_font_cache: dict[tuple, str] = {}


def font_family(root, mono: bool = False) -> str:
    """在候选字体栈里挑第一个系统真正装了的。"""
    key = ("mono" if mono else "ui",)
    if key in _font_cache:
        return _font_cache[key]
    try:
        import tkinter.font as tkfont

        available = {f.lower() for f in tkfont.families(root)}
    except Exception:
        available = set()
    for name in MONO_STACK if mono else FONT_STACK:
        if name.lower() in available:
            _font_cache[key] = name
            return name
    fallback = "Consolas" if mono else "Segoe UI"
    _font_cache[key] = fallback
    return fallback


def font(root, size: int = TS["body"], weight: str = "normal", mono: bool = False):
    """返回 Tk 字体元组。size 单位为 pt（1pt ≈ 1.33px）。"""
    return (font_family(root, mono), size, weight)


# ---------------------------------------------------------------- 主题解析


def enable_dpi_awareness() -> None:
    """让窗口在高分屏下不发虚。必须在创建 Tk() 之前调用。"""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def set_app_user_model_id(app_id: str = "Wbn.TaskNotify.Desktop.1") -> None:
    """给当前进程一个独立的 AppUserModelID。

    Windows 任务栏按 AppUserModelID 分组，并据此决定按钮上显示哪个图标。
    不显式设置的话，pythonw.exe 启动的窗口会被归到 Python 名下，
    任务栏和 Alt+Tab 里看到的就是 Python 的图标，而不是我们的 .ico。
    必须在创建任何窗口之前调用。
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def system_theme() -> str:
    """读注册表判断 Windows 当前是浅色还是深色。"""
    if sys.platform != "win32":
        return "light"
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as k:
            val, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            return "light" if int(val) == 1 else "dark"
    except Exception:
        return "light"


def resolve(mode: str = "auto") -> Palette:
    """mode: auto / light / dark"""
    if mode == "dark":
        return DARK
    if mode == "light":
        return LIGHT
    return DARK if system_theme() == "dark" else LIGHT


def mix(c1: str, c2: str, ratio: float) -> str:
    """在两个 #rrggbb 之间插值，用于 hover 渐变的中间帧。"""
    def parse(c: str) -> tuple[int, int, int]:
        c = c.lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if len(c) != 6:
            return (0, 0, 0)
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)

    r1, g1, b1 = parse(c1)
    r2, g2, b2 = parse(c2)
    r = round(r1 + (r2 - r1) * ratio)
    g = round(g1 + (g2 - g1) * ratio)
    b = round(b1 + (b2 - b1) * ratio)
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------------------------------------------------------------- 对比度

def _srgb_lum(hex_color: str) -> float:
    c = hex_color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    out = []
    for i in (0, 2, 4):
        v = int(c[i:i + 2], 16) / 255.0
        out.append(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]


def contrast(fg: str, bg: str) -> float:
    """WCAG 对比度。审查用，不参与渲染。"""
    a, b = _srgb_lum(fg), _srgb_lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------- 回调异常

def install_tk_exception_hook(root, on_error=None) -> None:
    """把 Tk 回调里未捕获的异常接住，别让它凭空消失。

    Tk 默认把回调异常打到 stderr。但本程序是用 pythonw.exe 启动的，
    **stderr 是 None**，异常写不出去就等于没有。Toast 的圆角背景从第一次
    提交起就没画出来过 —— `_draw()` 里引用了未导入的 `RADIUS_LG`，
    NameError 被吞掉，界面上只表现为「提示弹出来了但没有背景」，
    没人会想到去查日志。这个坑活了 8 个提交、两轮人工审查、一套无障碍审查。

    装了这个钩子之后，同类问题会以「Tk 回调异常」出现在
    `logs/notify-YYYY-MM-DD.log`，`tools/audit.py` 也能把它算成失败项。

    on_error：可选回调，收到格式化后的异常文本。审查脚本用它把异常计入失败，
    而不是只记日志。
    """
    import traceback

    def _hook(exc_type, exc_value, exc_tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            from . import store

            store.log(f"Tk 回调异常: {text}")
        except Exception:
            pass
        if on_error is not None:
            try:
                on_error(text)
            except Exception:
                pass

    root.report_callback_exception = _hook
