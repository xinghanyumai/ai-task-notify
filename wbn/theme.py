"""设计令牌：对齐 WorkBuddy 的视觉语言。

品牌来源
--------
数值不是拍脑袋定的，是从 WorkBuddy 安装目录里的设计令牌文件
`app.asar.unpacked/renderer/assets/tokens-Df-i-zsO.css` 提取出来的：

- 品牌色 #00c29a（浅色）/ #4cf0ce（深色）
- 圆角 8px（控件）/ 12~16px（卡片）/ 999px（胶囊）
- 字体 PingFang SC → Segoe UI → 微软雅黑

在此基础上做了三处有意的偏离，每一处都有理由
------------------------------------------------
1. **中性色往品牌绿偏一格**。页面底从 #f2f2f2 改成 #f4f6f5，深色底从
   #1f1f1f 改成 #191c1b。肉眼几乎看不出，但整体会读作「绿调」，品牌绿
   于是像信号灯而不是贴上去的装饰色。

2. **品牌绿拆成两个 token**。实测 #00c29a 在白底上的对比度只有 2.29:1，
   远低于 WCAG AA 要求的 4.5:1，直接拿它当文字色 / 图标色是不合格的。
   所以：
     brand      填充色（按钮底、开关轨道、状态点）—— 允许低对比度
     brand_ink  文字 / 描边 / 图标色（#007a63，白底 5.3:1）—— 合格
   凡是「绿色的小字」，都必须用 brand_ink。

3. **字号建立比例**，取代原来到处 8 / 9 的平铺。见 TS。
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

# ---------------------------------------------------------------- 令牌结构


@dataclass(frozen=True)
class Palette:
    name: str

    # 背景层级：app（最底）→ card（卡片）→ subtle（卡片内浅块）→ hover
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

    # 品牌
    brand: str
    brand_hover: str
    brand_active: str
    brand_soft: str
    brand_soft_2: str          # 比 brand_soft 更淡一档，用于大面积铺底
    brand_top: str             # 渐变上端
    brand_bottom: str          # 渐变下端
    on_brand: str

    # 语义色
    danger: str
    danger_soft: str
    warning: str
    warning_soft: str
    success: str
    success_soft: str

    # 其他
    sidebar: str               # 左侧导航栏底色（比内容区亮/暗一档）
    shadow_near: str           # 投影内层
    shadow_far: str            # 投影外层
    shadow: str
    track: str

    # 后加的字段（带默认值，避免调用方因为新增令牌而报错）
    brand_ink: str = "#007a63"  # 品牌色的「可读版本」，用于文字/图标/描边
    border_control: str = "#848f89"  # 控件边界：白底 3.1:1，满足 WCAG 1.4.11
    rail: str = "#d8dedb"       # 导航竖脊线
    grid: str = "#eef1f0"       # 极淡的分隔底纹


LIGHT = Palette(
    name="light",
    bg_app="#f4f6f5",
    bg_card="#ffffff",
    bg_subtle="#f7f9f8",
    bg_hover="#eef1f0",
    bg_active="#e6eae8",
    bg_input="#ffffff",
    border="#e3e7e5",
    border_strong="#cfd6d3",
    border_focus="#009578",
    text="#0d0f0e",
    text_secondary="#3a423f",
    text_tertiary="#69736d",
    placeholder="#69736d",
    brand="#00c29a",
    brand_hover="#1ad3ae",
    brand_active="#009273",
    brand_soft="#dff7f2",
    brand_soft_2="#eefaf7",
    brand_top="#12cfa6",
    brand_bottom="#00b491",
    on_brand="#003a31",
    danger="#c8342f",
    danger_soft="#fdecec",
    warning="#9a5600",
    warning_soft="#fff4e5",
    success="#067f3f",
    success_soft="#e7f8f0",
    sidebar="#ffffff",
    shadow_near="#e6eae8",
    shadow_far="#edf0ef",
    shadow="#0000000f",
    track="#e3e7e5",
    brand_ink="#007a63",
    border_control="#848f89",
    rail="#d8dedb",
    grid="#eef1f0",
)

DARK = Palette(
    name="dark",
    bg_app="#191c1b",
    bg_card="#212524",
    bg_subtle="#1e2221",
    bg_hover="#272c2a",
    bg_active="#2e3432",
    bg_input="#1e2221",
    border="#313634",
    border_strong="#424846",
    border_focus="#4cf0ce",
    text="#f2f5f3",
    text_secondary="#b9c1bd",
    text_tertiary="#8d9691",
    placeholder="#8d9691",
    brand="#4cf0ce",
    brand_hover="#6ef5d8",
    brand_active="#2fd4b4",
    brand_soft="#17342f",
    brand_soft_2="#1a2b28",
    brand_top="#63f3d4",
    brand_bottom="#33dcbb",
    on_brand="#003a31",
    danger="#ff7a7e",
    danger_soft="#3a1f20",
    warning="#ffa057",
    warning_soft="#3a2a18",
    success="#3dd68c",
    success_soft="#16321f",
    sidebar="#212524",
    shadow_near="#101312",
    shadow_far="#141716",
    shadow="#00000040",
    track="#313634",
    brand_ink="#4cf0ce",
    border_control="#636d68",
    rail="#39403d",
    grid="#232827",
)


# ---------------------------------------------------------------- 字体与尺寸

# 与 WorkBuddy 一致的字体栈；Tk 只接受单个族名，按顺序探测第一个可用的
FONT_STACK = [
    "PingFang SC",
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Segoe UI",
    "Noto Sans CJK SC",
    "Arial",
]
MONO_STACK = ["Cascadia Mono", "Consolas", "JetBrains Mono", "Courier New"]

# 字号层级，比例约 1.27。Tk 的单位是 pt（1pt ≈ 1.33px）。
# 用 TS[...] 取值，不要在各处硬编码数字。
TS = {
    "display": 22,   # 一页只允许出现一次，给空状态 / 教程页大标题
    "title": 15,     # 页面级标题
    "section": 11,   # 节标题
    "body": 10,      # 正文 ≈ 13px
    "small": 9,      # 辅助说明
    "micro": 8,      # 极小注脚
    "mono": 9,       # 等宽数字 / 凭证
}

# 行高倍数，给 Paragraph 用
LINE = 1.62

# 圆角（px，逻辑像素）
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
    `logs/notify-YYYY-MM-DD.log`，`audit.py` 也能把它算成失败项。

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

