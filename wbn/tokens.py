"""尺度、描边、动效令牌。

为什么单独一层：ui.py 和 widgets.py 加起来三千七百多行，间距原来散成 61 个
不同取值，同一层级的元素在不同页面用了不同的数（侧栏 16、顶栏 14、页边 24、
卡片内 18）。这一层把三类数字收成三套令牌，改一处就是全局改。

三类要分清，混用是上一版最大的问题：

- **间距**（padding、gap、元素间留白）走 `SP`，8 档，以 4 的倍数为主
- **结构尺寸**（窗口、栏宽、控件高、图标框）走 `DIM`，按语义命名
- **描边与圆角** 走 `STK` / `theme.RADIUS_*`，每个语义只有一个值

绘制坐标（`create_oval` 的端点、`create_line` 的折点）不进令牌。它们是几何，
不是间距，也不是可复用的尺寸；把它们硬套成 token 只会让「一个圆点的直径」
这种一次性数字获得一个没人敢改的名字。

取值为逻辑像素，实际渲染要过 DPI 换算（`theme.S`），所以这里全部走
`__getattr__` 而不是模块级常量：`SP.md` 每次返回当前缩放下的整数。
"""

from __future__ import annotations

from . import theme

# ---------------------------------------------------------------- 间距

# 7 档，以 4 的倍数为主，2 是唯一的例外。
#
# 为什么留一个 2：图标与文字的贴合、描边内缩这类「贴合间距」需要比 4 更紧，
# 但又不该用 1（1 在高 DPI 下会被渲染成 2，和 2 没有区别，等于两个名字一个值）。
#
# 为什么没有 20、28：它们只是 16 和 24 的近似值。上一版同时存在 18、20、22，
# 肉眼分不出，代码里却像三种不同的意图。
_SPACE = {
    "xxs": 2,    # 贴合：图标与文字、描边内缩、状态点与标签
    "xs": 4,     # 紧凑：同一行内的相邻元素
    "sm": 8,     # 小间隔：列表行之间、标签与输入框
    "md": 12,    # 中间隔：分节内的块间距
    "lg": 16,    # 大间隔：分节之间、容器内边距
    "xl": 24,    # 页边距、左右两栏之间
    "xxl": 32,   # 大块之间
}


class _Space:
    def __getattr__(self, name: str) -> int:
        try:
            return theme.S(_SPACE[name])
        except KeyError:
            raise AttributeError(f"没有这个间距档：{name}") from None


SP = _Space()


# ---------------------------------------------------------------- 尺寸

# 结构尺寸。不参与间距尺度，因为它们是「容器多大」而不是「隔多远」。
# 命名规则：先说什么东西，再说哪个方向（BTN_H = 按钮高度）。
_DIM = {
    # 窗口
    "WIN_W": 1080,
    "WIN_H": 760,
    "WIN_MIN_W": 940,
    "WIN_MIN_H": 640,
    # 骨架
    "SIDEBAR_W": 212,
    "TOPBAR_H": 56,
    "FOOTER_H": 58,
    "NAV_RAIL_W": 24,     # 导航竖脊那一列的宽
    "NAV_ITEM_H": 38,
    "DIVIDER_V_H": 16,    # 顶栏里分隔两组的竖线高度
    # 控件
    "BTN_H": 34,
    "BTN_MIN_W": 112,
    "BTN_MIN_W_SM": 96,
    "FIELD_H": 36,
    "FIELD_H_MULTI": 76,
    "FIELD_H_TALL": 132,  # 模板正文那种大输入区
    "SEG_H": 26,          # 分段控件
    "SWITCH_W": 40,
    "SWITCH_H": 22,
    "CHECK_BOX": 18,
    # 图标与徽标
    "LOGO": 32,
    "ICON": 16,
    "ICON_LG": 18,
    "ICON_BOX": 30,
    "BADGE": 40,
    # 状态点：侧栏「已接入」和顶栏「当前链路」用的是同一个点
    "DOT_W": 10,
    "DOT_H": 16,
    # 通道页 / 模板页两栏
    "LIST_COL_W": 250,
    "LIST_COL_MIN": 232,
    "LIST_COL_MAX": 276,
    "PREVIEW_W": 252,
    "PREVIEW_MIN": 226,
    "PREVIEW_MAX": 272,
    "HINT_WRAP": 240,
    # Toast
    "TOAST_W": 272,       # 文字宽度上限，超过就折行
    "TOAST_H": 40,        # 单行时的高度
    # 列表行
    "ROW_H": 44,
}


class _Dim:
    def __getattr__(self, name: str) -> int:
        try:
            return theme.S(_DIM[name])
        except KeyError:
            raise AttributeError(f"没有这个尺寸令牌：{name}") from None


DIM = _Dim()


# ---------------------------------------------------------------- 描边

# 每个语义一个值，不许混用。
#   rule      分隔线（分节细线、竖脊、列表分隔）
#   control   控件描边（按钮、输入框、开关轨道、复选框）
#   ring      焦点环
#   icon      图标笔画默认值；icon_thin 给 16px 的小图标（再粗就糊）；
#             icon_bold 给需要强调的单笔图标（勾选的对勾）
_STROKE = {
    "rule": 1,
    "control": 1,
    "ring": 2,
    "icon": 1.8,
    "icon_thin": 1.6,
    "icon_bold": 2,
}


class _Stroke:
    def __getattr__(self, name: str) -> int:
        try:
            return theme.S(_STROKE[name])
        except KeyError:
            raise AttributeError(f"没有这个描边令牌：{name}") from None


STK = _Stroke()


# ---------------------------------------------------------------- 动效


class MOTION:
    """三档时长，一个帧间隔。

    缓动不在这里定义，直接用 `anim.ease_out_cubic` / `anim.ease_out_quint`：

    - 进出、颜色、透明度、状态切换 → ease_out_cubic
    - 位移（滑入、滚动、导航点沿脊 travel） → ease_out_quint
    - 开关滑块是唯一用 ease_out_back 的地方：它有物理感，末端要轻微过冲

    任何一处偏离这三档，注释里必须写清为什么。上一版有 120/130/150/170/180/
    200/240/260 八个时长，其中 130 和 120 是同一个意思的两种写法。
    """

    quick = 120   # 悬停、按下、焦点环这类「手指还按着」的反馈
    base = 180    # 状态切换、页面进场、滑入
    slow = 260    # 一次性的大位移（导航点沿脊滑动、时间轴展开）

    interval = 16  # 约 62fps。13ms 是 77fps，屏幕刷新跟不上，白烧 CPU
