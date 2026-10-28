"""设置中心。

设计说明（P1 · v2）
------------------
v1 的实质是「换了颜色值 + 立了令牌体系」，但组件形态和排版一行没动，
所以用户看到的东西没变多少。v2 把三块一起换：配色、组件、排版。

四条基本判断
------------

1. **去卡片化。** v1 每个区块套一张描边卡片，卡片里还套卡片，一屏下来
   边框比内容还多。**当所有东西都有边框时，边框就不再区分任何东西。**
   v2 全站卡片数量 = 0（启动层除外）：分组靠「标题 + 留白 + 底部 1px
   分隔线」组织，组间距 24px 远大于组内行距 0，人眼自然就切开了。

2. **顶栏只有页面名。** v1 顶栏是「标题 + 描述 + 一条分割线」，再加上每页
   自己的页头，一屏出现两次页面名。v2 把页面名收进顶栏（17px 加粗），
   删掉页头，顶栏内一条线都不画。右侧只放全局动作（主题、总开关）。
   v1 那句「任务完成时，经微信测试号，送到你手机」的链路陈述句移到侧栏
   底部——它回答的是「这条链路通不通」，本来就属于侧栏的语义。

3. **正文列定宽 660 居中。** 窗口拉到 1360 之后如果正文跟着铺满，一行字
   横跨 1100px，眼睛要在两端来回跳。一行中文超过 34 个字就读不下去，
   660 约 33 字。窗口变宽只影响两侧留白，不影响行宽。

4. **切页是整页 8px 上移就位，不是逐张卡片错峰。** v1 每张卡片依次从下方
   10px 升到位、间隔 24ms，五张卡片光是等动画就要 96ms，而用户切页是为了
   看内容，不是为了看动画。v2 整页一次性上移 8px，160ms `ease_out_expo`。

页面缓存
--------
每页只建一次，切页就是 `pack_forget` + `pack`，实测 2–4ms。
缓存最容易坏的地方是「失效判断」：v1 的 `_rebuild()` 销毁了全部控件却
没清 `_pages`，字典里留着已销毁的 frame，切主题之后 `_show_page` 永远
提前返回，内容区一片空白（D1）。v2 的判断带**存活检查**，就算有人以后
又忘了清字典，也不会再出现空白页。
"""

from __future__ import annotations

import copy
import gc
import sys
import threading
import tkinter as tk

from wbn import anim, bridge, channels, config, events, insight, pipeline, store, theme
from wbn import splash as splash_mod
from wbn import widgets as W
from wbn.channels import CHANNEL_GROUPS
from wbn.theme import TS, S
from wbn.tokens import DIM, MOTION, SP, STK

APP_TITLE = "AI 任务通知"
APP_VER = "个人版 v1.2"

# (key, 导航名, 图标, 是否与上一项断开)
PAGES = [
    ("channels", "推送通道", "bell", False),
    ("triggers", "触发条件", "bolt", False),
    ("template", "消息模板", "doc", False),
    ("status", "运行状态", "chart", False),
    ("guide", "使用教程", "book", True),
]

THEME_OPTIONS = [("auto", "跟随"), ("light", "浅色"), ("dark", "深色")]

# 切页时要存/取的视图引用。
#
# 页面缓存之后，控件不会随切页销毁，但「当前页是哪个」这件事由这些属性
# 表达。切走时存一份、切回来时取一份，就不会出现「回到通道页但
# self.ch_rows 是 None」这种状态错位。
_VIEW_ATTRS = (
    "page_sc", "ch_list_inner", "ch_list_sc", "ch_form_host", "ch_form_sc",
    "ch_rows", "ch_count_pill", "left_col", "title_field", "content_field",
    "preview_cv", "q_check", "ch_test_btn", "ch_openid_btn", "ch_why_lbl",
    "tr_vars", "filt_vars", "ch_vars", "timeline", "stat_metrics",
)
# 这些属性是容器，清空时给空字典而不是 None
_DICT_VIEWS = ("ch_rows", "tr_vars", "filt_vars", "ch_vars", "stat_metrics")

# 预览样例、emoji 拆分、数字/列表转换都搬到了 `wbn/insight.py`（W0）：
# 那些是「数据怎么来」的问题，不是「怎么画」的问题，webview 版要用同一份。
# 这里只留 UI 文案。


PLACEHOLDERS = [
    ("{app}", "宿主名"), ("{project}", "项目名"), ("{duration}", "耗时"),
    ("{time}", "时间"), ("{date}", "日期"), ("{summary}", "回复摘要"),
    ("{error}", "错误信息"), ("{tool}", "工具名"), ("{host}", "主机名"),
    ("{kind}", "事件类型"), ("{session}", "会话 ID"),
]

# 通道分组（自建 / 第三方 / 本地）已搬到 wbn/channels.py —— 它是通道元数据，
# 不是布局。webview 版不该为了拿一份常量去 import 这个文件。

# 触发条件的中文说明。放在这里而不是 events.py，因为这是界面文案。
KIND_DESC = {
    "task_done": "一轮任务正常跑完",
    "task_failed": "明确报错或中断",
    "need_input": "助手在等你回答",
    "tool_failed": "工具连续失败（噪音较大）",
    "session_start": "开始新会话",
    "session_end": "会话关闭",
}


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        theme.S.calibrate(root)

        # 系统级无障碍：用户关掉了「在 Windows 中显示动画」就全程不动画
        anim.set_reduced(anim.detect_reduced_motion())

        self.cfg = config.load()
        # 配好凭证却忘了拨开关，是最常见的「配了收不到」。
        # 启动时先按完整度自动维护一次启用状态，再落盘。
        self._auto_on, self._auto_off = channels.auto_enable(self.cfg)
        if self._auto_on or self._auto_off:
            config.save(self.cfg)

        self.theme_mode = (self.cfg.get("ui") or {}).get("theme", "auto")
        self.pal = theme.resolve(self.theme_mode)

        self.current_page = "channels"
        self.current_channel = self._pick_channel()
        self.tpl_kind = "task_done"

        # 视图状态（每次切页重建）
        self.ch_vars: dict = {}
        self.tr_vars: dict = {}
        self.filt_vars: dict = {}
        self.title_field = None
        self.content_field = None
        self.preview_cv = None
        self.q_check = None
        self._preview_w = DIM.PREVIEW_W
        self._dirty = False

        # 通道页的三个滚动宿主，切页后必须置空
        self.ch_list_inner = None
        self.ch_list_sc = None
        self.ch_form_host = None
        self.ch_form_sc = None
        self.ch_rows: dict = {}
        self.ch_count_pill = None
        self.left_col = None

        # 页面缓存：key -> 该页的根 Frame，只建一次
        self._pages: dict = {}
        self._view: dict = {}
        self.timeline = None
        self.stat_metrics: dict = {}

        # 骨架引用
        self.page_sc = None
        self.nav_items: dict = {}
        self.content = None
        self.page_title_lbl = None
        self.chain_dot = None
        self.chain_lbl = None
        self._installed = False
        self.master_lbl = None
        self.master_switch = None
        self.dirty_dot = None
        self.dirty_lbl = None
        self.reset_btn = None

        # 网络动作的按钮引用：加载态要落在「刚被点的那个按钮」上
        self.ch_test_btn = None
        self.ch_openid_btn = None
        self.ch_why_lbl = None
        self.test_btn = None
        self._bg_busy = False

        self._setup_window()
        self._build_all()
        self._announce_auto()
        self._relax_gc()

    @staticmethod
    def _relax_gc() -> None:
        """放宽分代回收阈值。

        实测：界面建好之后什么都不做，主线程仍会在启动约 300ms 后停顿一次
        275~310ms。关掉 GC 做对照，同样场景最大停顿从 290ms 掉到 25ms，
        所以这笔开销是回收，不是 Tk。这一版界面有上万个 Python 对象
        （每个自绘控件都带画布、字体、闭包），第二代一到期就要全扫一遍。

        这里只放宽阈值，不做 `gc.freeze()`：冻结能把启动那一次消掉，但之后
        每次切页新建的那批控件照样会被扫，而且冻结之后启动页的控件再也不会
        被回收（有界泄漏）。放宽阈值是零成本的——对象存活时间本来就长，
        频繁扫第二代只会白烧 CPU。
        """
        gc.set_threshold(20000, 50, 200)

    def _pick_channel(self) -> str:
        """默认打开哪个通道。判定在 `insight.pick_channel`，两边共用。"""
        return insight.pick_channel(self.cfg)

    # ------------------------------------------------------------ 窗口

    def _setup_window(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry(f"{DIM.WIN_W}x{DIM.WIN_H}")
        self.root.minsize(DIM.WIN_MIN_W, DIM.WIN_MIN_H)
        self.root.configure(bg=self.pal.bg_app)
        self._set_icon()

    def _set_icon(self) -> None:
        """设置窗口 / 任务栏图标。

        Windows 上优先用 iconbitmap 加载多尺寸 .ico —— 系统会自己挑
        16 / 32 / 48 / 256 里最合适的那一帧，标题栏和任务栏都清晰。
        iconphoto 只在 iconbitmap 不可用时兜底（它塞的是单一位图，
        缩放后在高 DPI 下会发虚）。位图必须留引用，否则被 GC 回收后图标变白。
        """
        ico = config.ROOT / "assets" / "icon.ico"
        if ico.exists():
            try:
                self.root.iconbitmap(default=str(ico))
                return
            except Exception:
                pass

        try:
            from PIL import Image, ImageTk

            png = ico.with_name("icon_256.png")
            src = png if png.exists() else ico
            if not src.exists():
                return
            self._icon_photo = ImageTk.PhotoImage(Image.open(src).convert("RGBA"))
            self.root.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    def _apply_titlebar(self) -> None:
        """让系统标题栏跟随深色 / 浅色主题，避免上黑下白的割裂感。

        时机很关键：窗口还没被映射（mapped）时 wm_frame() 拿不到真正的
        外层句柄，DwmSetWindowAttribute 会「成功返回但什么也没改」，
        表现就是标题栏一直保持系统强调色。所以必须等窗口真的出现后再设。

        **这里不能调 `update_idletasks()`。** 原来有这一句（想让窗口先被
        realized），但它在「定时回调里再跑一轮 idle」这件事上会死循环：
        P1 的启动层把 5 页全部预建好再一次性 deiconify，一屏的 `<Configure>`
        → `pack_configure` → 又一轮 `<Configure>` 全挤在 idle 队列里，
        `update_idletasks()` 永远排不空，整个事件循环卡死（实测交接后
        界面完全无响应）。改成「窗口没映射就直接放弃」，等 `reveal()` 之后
        那次调用再设——那一次窗口已经映射，`wm_frame()` 本来就是有效的。
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes

            if not self.root.winfo_ismapped():
                return
            hwnd = int(self.root.wm_frame(), 16)
            if not hwnd:
                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                return

            dark = 1 if self.pal.name == "dark" else 0
            for attr in (20, 19):  # 20: Win10 20H1+ / 19: 旧版
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(ctypes.c_int(dark)),
                    ctypes.sizeof(ctypes.c_int),
                )
            try:
                # DWM 的颜色参数是 0x00BBGGRR，不是 0xRRGGBB
                color = 0x001B1C19 if dark else 0x00F5F6F4
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 35, ctypes.byref(ctypes.c_int(color)),
                    ctypes.sizeof(ctypes.c_int),
                )
            except Exception:
                pass
        except Exception:
            pass

    # ------------------------------------------------------------ 骨架

    def _build_all(self) -> None:
        p = self.pal
        self.root.configure(bg=p.bg_app)
        self._build_sidebar()

        self.main = tk.Frame(self.root, bg=p.bg_app)
        self.main.pack(side="left", fill="both", expand=True)
        self._build_topbar()

        # 内容区：每页建一次留着，切页只换 pack，不重建
        self.content = tk.Frame(self.main, bg=p.bg_app)
        self.content.pack(fill="both", expand=True)

        self._build_footer()
        self.toast = W.Toast(self.root, p)
        self._show_page(self.current_page)
        self.root.after_idle(self._apply_titlebar)
        self.root.after(120, self._apply_titlebar)

    def _build_sidebar(self) -> None:
        """侧栏 236px：顶部品牌、中部导航、底部链路状态。

        导航项**不再有「设置」分组标签**。一个只有五个页面的设置工具，
        在导航上方写「设置」等于在标题栏写「窗口」——零信息，还占掉 26px
        和一次视觉停顿。去掉之后导航直接从品牌区下面开始，视线起点更清楚。
        """
        p = self.pal
        side = tk.Frame(self.root, bg=p.sidebar, width=DIM.SIDEBAR_W)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(self.root, bg=p.border, width=STK.rule).place(
            x=DIM.SIDEBAR_W - STK.rule, y=0, relheight=1)

        # -- 品牌区
        brand = tk.Frame(side, bg=p.sidebar)
        brand.pack(fill="x", padx=SP.lg, pady=(SP.xl, SP.lg))
        mark = tk.Canvas(brand, width=DIM.LOGO, height=DIM.LOGO, bg=p.sidebar,
                         highlightthickness=0, bd=0)
        mark.pack(side="left")
        W.round_rect_grad(mark, 0, 0, DIM.LOGO, DIM.LOGO, S(9),
                          p.brand_top, p.brand_bottom)
        W.draw_icon(mark, "bell", SP.sm - S(1), SP.sm - S(1), DIM.ICON - S(1),
                    p.on_brand, STK.icon)

        titles = tk.Frame(brand, bg=p.sidebar)
        titles.pack(side="left", padx=(SP.md, 0))
        tk.Label(titles, text=APP_TITLE, bg=p.sidebar, fg=p.text,
                 font=theme.font(self.root, TS["section"], "bold")).pack(anchor="w")
        tk.Label(titles, text=APP_VER, bg=p.sidebar, fg=p.text_tertiary,
                 font=theme.font(self.root, TS["small"])).pack(
            anchor="w", pady=(SP.xxs, 0))

        # -- 导航
        nav = tk.Frame(side, bg=p.sidebar)
        nav.pack(fill="x", padx=SP.sm)
        for key, label, icon, gap in PAGES:
            if gap:
                tk.Frame(nav, bg=p.sidebar, height=SP.sm).pack(fill="x")
                tk.Frame(nav, bg=p.border, height=STK.rule).pack(
                    fill="x", padx=SP.sm, pady=(0, SP.sm))
            item = W.NavItem(nav, p, key, label, icon, self._show_page,
                             bg=p.sidebar, height=DIM.NAV_ITEM_H)
            item.pack(fill="x", pady=SP.xxs)
            self.nav_items[key] = item

        # -- 底部：接入状态 + 链路陈述
        #
        # 链路陈述句从顶栏搬到这里（P1 4.2 顶栏只留页面名）。它回答的是
        # 「这条链路现在通不通」，和「已接入宿主」是同一个问题的两半，
        # 放在一起才读得通。用 Paragraph 折行而不是 elide：侧栏只有 204px，
        # 省略号会把这句话裁到只剩主谓宾，信息就没了。
        foot = tk.Frame(side, bg=p.sidebar)
        foot.pack(side="bottom", fill="x", padx=SP.lg, pady=(0, SP.lg))
        tk.Frame(foot, bg=p.border, height=STK.rule).pack(fill="x", pady=(0, SP.md))

        installed = self._hooks_installed()
        self._installed = installed
        state = tk.Frame(foot, bg=p.sidebar)
        state.pack(fill="x")
        self.chain_dot = tk.Canvas(state, width=DIM.DOT_W, height=DIM.DOT_H,
                                   bg=p.sidebar, highlightthickness=0, bd=0)
        self.chain_dot.pack(side="left")
        tk.Label(state, text="已接入宿主" if installed else "未接入宿主",
                 bg=p.sidebar, fg=p.text_secondary,
                 font=theme.font(self.root, TS["small"])).pack(
            side="left", padx=(SP.xs, 0))

        self.chain_lbl = W.Paragraph(foot, p, "", p.sidebar, p.text_tertiary,
                                     size=TS["small"],
                                     wrap=DIM.SIDEBAR_W - SP.xl * 2)
        self.chain_lbl.pack(fill="x", pady=(SP.xs, 0))

        if not installed:
            tk.Label(foot, text="运行 install.py 完成接入", bg=p.sidebar,
                     fg=p.text_tertiary,
                     font=theme.font(self.root, TS["small"])).pack(
                anchor="w", pady=(SP.xxs, 0))

        self._paint_chain()

    def _build_topbar(self) -> None:
        """顶栏 58px：左侧页面名，右侧全局动作。**一条线都不画。**

        v1 这里是「标题 + 一句链路描述 + 右侧主题/开关，中间还夹一条竖分隔线」，
        加上每页自己的页头，一屏出现两次页面名、三种分割方式。
        v2 只留一个页面名（17px 加粗，全站最大字号），右侧动作靠间距分组，
        不靠竖线——间距本身就是分组，多一条线只是多一条噪声。
        """
        p = self.pal
        bar = tk.Frame(self.main, bg=p.bg_app, height=DIM.TOPBAR_H)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        # 右侧：主题分段 + 总开关。顺序是「先全局设置，再总闸」。
        right = tk.Frame(bar, bg=p.bg_app)
        right.pack(side="right", padx=(0, SP.xl))
        self.master_switch = W.Switch(right, p, bool(self.cfg.get("enabled", True)),
                                      command=self._on_master, bg=p.bg_app)
        self.master_switch.pack(side="right")
        self.master_lbl = tk.Label(right, text="", bg=p.bg_app,
                                   font=theme.font(self.root, TS["small"]))
        self.master_lbl.pack(side="right", padx=(0, SP.sm))

        W.Segmented(right, p, THEME_OPTIONS, self.theme_mode,
                    command=self._switch_theme, bg=p.bg_app,
                    height=DIM.SEG_H, font_size=TS["small"]).pack(
            side="right", padx=(0, SP.lg))

        # 左侧：页面名。宽度交给 packer，文字用 Label 的自然宽度，
        # 不需要省略——五个页面名最长四个字，236px 侧栏之外的空间永远够。
        left = tk.Frame(bar, bg=p.bg_app)
        left.pack(side="left", fill="both", expand=True, padx=(SP.xl, 0))
        self.page_title_lbl = tk.Label(
            left, text="", bg=p.bg_app, fg=p.text, anchor="w",
            font=theme.font(self.root, TS["title"], "bold"))
        self.page_title_lbl.pack(side="left")

        self._paint_master()

    def _paint_page_title(self) -> None:
        if self.page_title_lbl is None or not self.page_title_lbl.winfo_exists():
            return
        name = next((n for k, n, _i, _g in PAGES if k == self.current_page), "")
        if str(self.page_title_lbl.cget("text")) != name:
            self.page_title_lbl.configure(text=name)

    def _chain_sentence(self) -> tuple[str, str]:
        """把当前配置翻译成一句人话：(陈述句, 语义等级)。

        等级 → 颜色由 `_level_color` 映射。业务层不该知道 `brand` 是什么颜色。
        """
        return insight.chain_sentence(self.cfg, bridge._last_send_times())

    def _level_color(self, level: str, ok_color: str | None = None) -> str:
        """语义等级 → 具体颜色。**颜色只在 UI 层出现。**

        `ok_color` 给调用点覆盖「ok」用哪个绿：侧栏链路陈述用 `success`
        （正文色的绿），通道列表的状态点用 `brand`（品牌绿，更亮）。
        这是纯视觉选择，业务层不该知道。
        """
        p = self.pal
        return {
            insight.LEVEL_OK: ok_color or p.success,
            insight.LEVEL_WARN: p.warning,
            insight.LEVEL_DANGER: p.danger,
            insight.LEVEL_MUTED: p.border_strong,
        }.get(level, p.text_secondary)

    def _paint_chain(self) -> None:
        """刷新侧栏底部的链路陈述。

        状态点表达「宿主接没接上」（静态事实），文字颜色表达「这条链路
        现在通不通」（随配置变化）。两者分开，因为它们会不一致：
        宿主接了但一条通道都没开，就是「接了但发不出去」。
        """
        if self.chain_lbl is None or not self.chain_lbl.winfo_exists():
            return
        text, level = self._chain_sentence()
        self._chain_text = text
        if self.chain_dot is not None and self.chain_dot.winfo_exists():
            dotc = self.pal.success if self._installed else self.pal.warning
            self.chain_dot.delete("all")
            self.chain_dot.create_oval(S(1), S(5), S(7), S(11),
                                       fill=dotc, outline=dotc)
        if str(self.chain_lbl.cget("text")) != text:
            self.chain_lbl.configure(text=text)
        self.chain_lbl.set_fg(self._level_color(level))

    def _paint_master(self) -> None:
        if self.master_lbl is None:
            return
        on = bool(self.cfg.get("enabled", True))
        self.master_lbl.configure(
            text="通知已开启" if on else "通知已静音",
            fg=self.pal.text_secondary if on else self.pal.text_tertiary)

    def _build_footer(self) -> None:
        """底栏 64px：左侧保存状态，右侧 [发送测试] [重置] [保存配置]。

        三个按钮都是「对整份配置生效」的动作，所以放在同一个位置、
        用同一套优先级：主操作（保存）墨色实心且在最右，重置是描边的
        次操作，发送测试也是描边——它虽然常用，但不改变配置。
        日志 / 配置目录是轻量的跳转，放在左边贴着保存状态，因为它们
        和「这份配置存到哪了」是同一件事。
        """
        p = self.pal
        foot = tk.Frame(self.main, bg=p.bg_card, height=DIM.FOOTER_H)
        foot.pack(fill="x", side="bottom")
        foot.pack_propagate(False)
        tk.Frame(foot, bg=p.border, height=STK.rule).pack(side="top", fill="x")

        self.save_btn = W.Button(foot, "保存配置", p, command=self.on_save,
                                 variant="primary", min_width=DIM.BTN_MIN_W)
        self.save_btn.pack(side="right", padx=(SP.sm, SP.xl), pady=SP.md)
        self.reset_btn = W.Button(foot, "重置", p, command=self.on_reset,
                                  variant="secondary",
                                  min_width=DIM.BTN_MIN_W_SM)
        self.reset_btn.pack(side="right", padx=SP.sm, pady=SP.md)
        self.test_btn = W.Button(foot, "发送测试", p, command=self.on_test,
                                 variant="secondary", min_width=DIM.BTN_MIN_W_SM)
        self.test_btn.pack(side="right", pady=SP.md)

        # 左：保存状态。这是真实信息（改动落盘没有），不是装饰。
        box = tk.Frame(foot, bg=p.bg_card)
        box.pack(side="left", padx=(SP.xl, 0), pady=SP.md)
        self.dirty_dot = tk.Canvas(box, width=SP.sm, height=DIM.DOT_H, bg=p.bg_card,
                                   highlightthickness=0, bd=0)
        self.dirty_dot.pack(side="left")
        self.dirty_lbl = tk.Label(box, text="", bg=p.bg_card,
                                  fg=p.text_tertiary,
                                  font=theme.font(self.root, TS["small"]))
        self.dirty_lbl.pack(side="left", padx=(SP.xs, 0))

        W.Button(foot, "日志", p, command=self.on_open_logs,
                 variant="ghost").pack(
            side="left", padx=(SP.lg, 0), pady=SP.lg)
        W.Button(foot, "配置目录", p, command=self.on_open_root,
                 variant="ghost").pack(
            side="left", padx=SP.xxs, pady=SP.lg)
        self._paint_dirty()

    def _mark_dirty(self) -> None:
        if not self._dirty:
            self._dirty = True
            self._paint_dirty()

    def _paint_dirty(self) -> None:
        if self.dirty_lbl is None or not self.dirty_lbl.winfo_exists():
            return
        p = self.pal
        self.dirty_dot.delete("all")
        if self._dirty:
            self.dirty_dot.create_oval(S(1), S(5), S(7), S(11),
                                       fill=p.warning, outline=p.warning)
            self.dirty_lbl.configure(text="有未保存的改动", fg=p.text_secondary)
        else:
            self.dirty_dot.create_oval(S(1), S(5), S(7), S(11),
                                       fill=p.success, outline=p.success)
            self.dirty_lbl.configure(text="已保存", fg=p.text_tertiary)

    def _hooks_installed(self) -> bool:
        """WorkBuddy 的 hook 装了没。读宿主 settings.json，实现在 insight。"""
        return insight.hooks_installed()

    # ------------------------------------------------------------ 主题与切页

    def _switch_theme(self, mode: str) -> None:
        if mode == self.theme_mode:
            return
        self.cfg = self._collect()
        self.theme_mode = mode
        self.cfg.setdefault("ui", {})["theme"] = mode
        self.pal = theme.resolve(mode)
        config.save(self.cfg)
        self._rebuild()

    def _rebuild(self) -> None:
        page, chan = self.current_page, self.current_channel
        dirty = self._dirty
        for w in self.root.winfo_children():
            w.destroy()
        # 旧控件已销毁，必须丢掉全部视图引用，否则 _collect 会摸到僵尸控件
        self._reset_view_state(include_shell=True)
        self._reset_pages()
        self.current_page = page
        self.current_channel = chan
        self._dirty = dirty
        self._build_all()

    def _reset_pages(self) -> None:
        """清空页面缓存。**销毁容器的同一处必须调它。**

        D1 的根因就是这里：`_rebuild()` 销毁了 root 的全部子控件，却把
        `_pages` 里的 frame 引用留着。之后 `_show_page` 看到「这一页在
        字典里」就直接返回，而那个 frame 早就没有 Tk 对象了——内容区
        一片空白。

        v2 用两道锁：① `_show_page` 的判断带 `winfo_exists()` 存活检查，
        就算字典没清也不会空白；② 这里仍然统一清一次，因为留着死引用
        会让 `_view` 里的控件引用也一起烂掉，`_collect()` 每次都要
        try/except 兜一圈。两道锁都不贵，少任何一道都会在某个路径上翻车。
        """
        self._pages.clear()
        self._view.clear()

    def _reset_view_state(self, include_shell: bool = False) -> None:
        """清空视图引用。

        `include_shell=False` 时保留侧栏 / 顶栏 / 底栏的引用——切页时这些
        骨架并没有重建，把它们置空会让后续刷新摸到 None。
        """
        for name in _VIEW_ATTRS:
            setattr(self, name, {} if name in _DICT_VIEWS else None)
        self._preview_w = DIM.PREVIEW_W
        if include_shell:
            self.nav_items = {}
            self.content = None
            self.chain_dot = None
            self.chain_lbl = None
            self.master_lbl = None
            self.dirty_dot = None
            self.dirty_lbl = None

    def _save_view(self, key: str) -> None:
        """把当前页的控件引用存起来，切回来时能接着用。"""
        self._view[key] = {n: getattr(self, n, None) for n in _VIEW_ATTRS}
        self._view[key]["_preview_w"] = self._preview_w

    def _restore_view(self, key: str) -> None:
        saved = self._view.get(key) or {}
        for name in _VIEW_ATTRS:
            setattr(self, name, saved.get(name))
        self._preview_w = saved.get("_preview_w", DIM.PREVIEW_W)

    def _builders(self) -> dict:
        return {
            "channels": self._page_channels,
            "triggers": self._page_triggers,
            "template": self._page_template,
            "status": self._page_status,
            "guide": self._page_guide,
        }

    def _show_page(self, key: str) -> None:
        """切换页面。

        每页只建一次，之后切页就是 pack_forget + pack，实测 2–4ms。
        失效判断必须带**存活检查**：`_rebuild()` 之后 `_pages` 里可能
        留着已销毁的 frame，只看「键在不在」会让页面永远空白（D1）。
        """
        if self.content is None:
            return
        builders = self._builders()
        if key not in builders:
            key = "channels"

        if key == self.current_page:
            frame = self._pages.get(key)
            if frame is not None and frame.winfo_exists():
                for k, item in self.nav_items.items():
                    item.set_selected(k == key)
                self._paint_page_title()
                return

        # 先把当前页上的编辑读进 cfg，否则换页会丢掉未保存的输入
        self.cfg = self._collect()
        prev = self.current_page
        if prev in self._pages:
            self._save_view(prev)

        self.current_page = key
        frame = self._pages.get(key)
        if frame is not None and not frame.winfo_exists():
            # 字典里是个僵尸，丢掉重来（D1 的兜底）
            self._pages.pop(key, None)
            frame = None
        if frame is None:
            frame = tk.Frame(self.content, bg=self.pal.bg_app)
            builders[key](frame)
            self._pages[key] = frame
            self._save_view(key)
        else:
            self._restore_view(key)
            self._refresh_page(key)

        for k, f in self._pages.items():
            if k != key and f.winfo_exists() and f.winfo_ismapped():
                f.pack_forget()
        frame.pack(fill="both", expand=True)

        for k, item in self.nav_items.items():
            item.set_selected(k == key)

        self._paint_page_title()
        self._paint_chain()
        self._enter_anim(frame)

    def _refresh_page(self, key: str) -> None:
        """页面已经在内存里，只补那些会过期的内容。

        控件和输入值都还活着，所以这里不做重建；只有「随时间或外部状态
        变化」的部分需要重新算一遍。
        """
        try:
            if key == "channels":
                self._sync_channel_rows()
            elif key == "status":
                self._refresh_status()
            elif key == "template":
                self._update_preview()
        except Exception:
            return

    def _drop_page(self, key: str) -> None:
        """销毁某一页并把它从缓存里摘掉（重置、强制刷新时用）。"""
        frame = self._pages.pop(key, None)
        self._view.pop(key, None)
        if frame is not None:
            try:
                frame.destroy()
            except tk.TclError:
                pass

    def prebuild(self, key: str) -> None:
        """启动层 T1 预热一页：**只建，不显示**。

        故意绕开 `_show_page`。那条路会读当前页的编辑、存视图、切导航选中、
        跑进场动画——预热期间这些副作用一件都不该发生：用户还没看到主窗口，
        当前页也不该变。这里只做一件事：把 frame 建出来塞进缓存，让用户
        真点进去时走「缓存命中」那条 2~10ms 的路。

        建完立刻 `_save_view`：页面里的控件引用要跟着 frame 一起存下来，
        否则之后 `_show_page` 走 `_restore_view` 时拿到的是一堆指向别的页的
        旧引用。
        """
        if self.content is None:
            return
        if key in self._pages and self._pages[key].winfo_exists():
            return
        build = self._builders().get(key)
        if build is None:
            return
        frame = tk.Frame(self.content, bg=self.pal.bg_app)
        # **先给一个确定宽度，再建内容。**
        #
        # 未 pack 的容器宽度是 0/1，而页里有 grid 的 `uniform` 等宽列
        # （触发条件页的两列网格）。uniform 的列宽分配要拿容器宽度当参照，
        # 参照是 1 的时候算不出来，Tk 会在 idle 里反复迭代——每轮都派发一批
        # `<Configure>`，永远不收敛。实测触发条件页预建后 3 秒内产生
        # 24578 次 Configure，占全界面 97.6%，把事件队列塞满，用户看到的就是
        # 「窗口无响应」。
        #
        # `place` 在屏幕外并给出宽度：不参与 pack 布局（不影响当前页），
        # 但内部控件拿得到真实宽度，一次就收敛。建完立刻 `place_forget`，
        # 回到「未管理」状态，和原来一样。
        cw = self.content.winfo_width()
        if cw <= 1:
            # 启动期 root 还是 withdraw 状态，`winfo_width()` 对未映射的
            # 窗口返回 1。用初始窗口尺寸推算内容区宽度，预建页才有一致的
            # 布局参照。
            cw = max(S(DIM.WIN_W) - S(DIM.SIDEBAR_W), S(640))
        if cw > 1:
            frame.place(x=-20000, y=0, width=cw)
            # 让 place 的宽度真正下发到 frame 自身，之后 build 出来的子控件
            # 才从第一轮就拿到真实宽度。`update_idletasks()` 只跑空闲任务、
            # 不派发 `<Configure>`，所以不会把下面那个震荡循环引出来。
            try:
                frame.update_idletasks()
            except tk.TclError:
                pass
        try:
            build(frame)
        finally:
            if cw > 1:
                frame.place_forget()
        self._pages[key] = frame
        self._save_view(key)

    def reveal(self) -> None:
        """T2：主窗口露面。

        三件事按顺序做：把视图引用拨回当前页（预热期间 `self.*` 被最后
        建的那一页改写过）、deiconify、再跑一次首屏进场动画。标题栏的
        DWM 属性必须在**映射之后**设，否则系统静默忽略。
        """
        self._restore_view(self.current_page)
        self.root.deiconify()
        try:
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass
        frame = self._pages.get(self.current_page)
        if frame is not None and frame.winfo_exists():
            self._enter_anim(frame)
        self.root.after_idle(self._apply_titlebar)
        self.root.after(120, self._apply_titlebar)

    def _enter_anim(self, frame) -> None:
        """切页进场：整页 8px 上移就位，160ms `ease_out_expo`。

        **不做逐张卡片的错峰动画。** v1 每张卡片依次从下方 10px 升到位、
        间隔 24ms，五张卡片光等动画就要 96ms；而用户切页是为了看内容，
        不是为了看动画。整页一次性位移 8px，前 100ms 走完八成，剩下的
        收尾——观感是「弹到位」，比逐张更像一个整体。

        Tkinter 没有逐控件透明度，所以这里只能用位移做「进场」，
        不能用淡入（P1 1.5 的能力边界）。
        """
        if anim.reduced():
            return
        # 有整页滚动容器的页面，滑它；通道页没有整页滚动容器
        # （左右两栏各自滚），滑右侧详情面板。
        for sc in (self.page_sc, self.ch_form_sc):
            if sc is not None and sc.winfo_exists():
                sc.slide_in(dy=SP.sm, duration=MOTION.base)
                return

    # ------------------------------------------------------------ 通用小组件

    def _center_col(self, host, maxw: int | None = None) -> tk.Frame:
        """在 host 里挂一个「定宽居中」的列，返回这个列。

        P1 1.3 的判据是「1360px 下正文列宽 660±2」。做法是只调列的左右
        padx、不改 inner 宽度——改 inner 宽度会反过来再触发一次
        <Configure>，形成「越算越窄」的循环。

        通道页右栏和模板页左栏都要这个，所以抽出来复用。
        """
        p = self.pal
        col = tk.Frame(host, bg=p.bg_app)
        col.pack(fill="x")
        # 注意：DIM.* 返回的**已经是**设备像素（内部走过 theme.S），
        # 这里再加一次 S() 就是二次换算——100% 缩放下看不出问题，
        # 200% 缩放下 660 会变成 2636，正文列永远压不住。
        limit = maxw or DIM.CONTENT_MAX
        col._pad = -1

        def _fit(e):
            pad = max(0, (e.width - limit) // 2)
            if pad != col._pad:
                col._pad = pad
                col.pack_configure(padx=(pad, pad))

        host.bind("<Configure>", _fit, add="+")
        return col

    def _scroll_area(self, parent) -> tk.Frame:
        """可滚动的正文区，返回一个居中的定宽列。

        窗口拉到 1360 之后如果正文跟着铺满，一行字会横跨 1100px，
        眼睛要在两端来回跳。Linear / Stripe 的做法是正文列封顶、多出来的
        宽度全部变成两侧留白——窗口变宽只影响留白，不影响行宽。
        """
        sc = W.ScrollFrame(parent, self.pal, bg=self.pal.bg_app)
        sc.pack(fill="both", expand=True)
        self.page_sc = sc
        return self._center_col(sc.inner)

    def _section(self, parent, text: str, bg: str) -> None:
        """分组内的小标题。比分组标题轻一档，用来在组里再分小组。"""
        p = self.pal
        tk.Label(parent, text=text, bg=bg, fg=p.text_tertiary, anchor="w",
                 font=theme.font(self.root, TS["small"], "bold")).pack(
            fill="x", pady=(SP.md, SP.sm))

    def _para(self, parent, text: str, bg: str, fg: str, size: int | None = None,
              weight: str = "normal", pady: int = 0, mono: bool = False):
        txt = W.Paragraph(parent, self.pal, text, bg, fg,
                          size=size or TS["small"], mono=mono)
        txt.pack(fill="x", pady=pady)
        return txt

    def _bullet(self, parent, text: str, bg: str) -> None:
        p = self.pal
        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", pady=SP.xs)
        dot = tk.Canvas(row, width=S(12), height=S(18), bg=bg,
                        highlightthickness=0, bd=0)
        dot.pack(side="left", anchor="n")
        dot.create_oval(S(3), S(7), S(7), S(11),
                        fill=p.text_tertiary, outline=p.text_tertiary)
        W.Paragraph(row, p, text, bg, p.text_secondary,
                    size=TS["small"], spacing=SP.xxs).pack(
            side="left", fill="x", expand=True)

    def _form_field(self, parent, label: str, value: str, hint: str = "",
                    show: str | None = None, multiline: bool = False,
                    required: bool = False, bg: str | None = None) -> W.Field:
        p = self.pal
        ground = bg or p.bg_app
        cell = tk.Frame(parent, bg=ground)
        head = tk.Frame(cell, bg=ground)
        head.pack(fill="x", pady=(0, SP.xs))
        tk.Label(head, text=label, bg=ground, fg=p.text_secondary,
                 font=theme.font(self.root, TS["small"])).pack(side="left")
        if required:
            # 用星号而不是「必填」两个字：一整排「必填」会把标签行压得很吵，
            # 而星号是约定俗成的，几乎不占视觉重量。
            tk.Label(head, text="*", bg=ground, fg=p.danger,
                     font=theme.font(self.root, TS["small"], "bold")).pack(
                side="left", padx=(SP.xs, 0))
        fld = W.Field(cell, p, value=value, show=show, multiline=multiline,
                      height=(DIM.FIELD_H_MULTI if multiline
                              else DIM.FIELD_H))
        fld.pack(fill="x")
        fld.bind_change(self._mark_dirty)
        if hint:
            tk.Label(cell, text=hint, bg=ground, fg=p.text_tertiary,
                     font=theme.font(self.root, TS["small"]),
                     justify="left", anchor="w",
                     wraplength=DIM.HINT_WRAP).pack(anchor="w", pady=(SP.xs, 0))
        return fld

    # ------------------------------------------------------------ 页：推送通道

    def _page_channels(self, parent) -> None:
        """页 1 · 推送通道：左 240 列表 + 1px 分隔线 + 右 660 详情。

        左栏**没有边框**，靠右侧一条 1px 竖线与详情分开。这一条竖线是
        整页唯一的分割元素——如果再给左栏套一个圆角底、给右栏套一张卡片，
        屏幕上就会出现三层边界，而实际上只有「列表」和「详情」两件事。
        """
        p = self.pal

        wrap = tk.Frame(parent, bg=p.bg_app)
        wrap.pack(fill="both", expand=True, padx=(SP.xl, 0), pady=(SP.sm, 0))

        # -- 左：出口列表
        left = tk.Frame(wrap, bg=p.bg_app, width=DIM.LIST_COL_W)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.left_col = left

        head = tk.Frame(left, bg=p.bg_app)
        head.pack(fill="x", pady=(SP.xxs, SP.sm))
        tk.Label(head, text="全部通道", bg=p.bg_app, fg=p.text,
                 font=theme.font(self.root, TS["section"], "bold")).pack(side="left")
        self.ch_count_pill = W.Pill(head, p, "", bg=p.bg_app, size=TS["small"])
        self.ch_count_pill.pack(side="right")

        sc = W.ScrollFrame(left, p, bg=p.bg_app)
        sc.pack(fill="both", expand=True)
        self.ch_list_inner = sc.inner
        self.ch_list_sc = sc

        tk.Frame(wrap, bg=p.border, width=STK.rule).pack(
            side="left", fill="y", padx=(SP.md, 0))

        # -- 右：出口详情（独立滚动，正文列 660 居中）
        right = tk.Frame(wrap, bg=p.bg_app)
        right.pack(side="left", fill="both", expand=True, padx=(SP.xl, 0))
        self.ch_form_host = right

        self._build_channel_list()
        self._build_channel_form(animate=False)
        self._sync_channel_rows()

    def _row_state(self, ctype: str) -> tuple[str, str, bool]:
        """列表行的 (副标题, 状态点颜色, 是否点亮)。

        副标题与等级来自 `insight.channel_state`（语义），这里只把等级翻成颜色。
        """
        ch = config.get_channel(self.cfg, ctype) or {}
        subtitle, level, lit = insight.channel_state(ctype, ch)
        # 状态点用品牌绿：它比 success 更亮，在 44px 行里更醒目
        return subtitle, self._level_color(level, ok_color=self.pal.brand), lit

    def _build_channel_list(self) -> None:
        p = self.pal
        host = self.ch_list_inner
        for w in host.winfo_children():
            w.destroy()
        self.ch_rows = {}

        for group, types in CHANNEL_GROUPS:
            row = tk.Frame(host, bg=p.bg_app)
            row.pack(fill="x", padx=SP.xs, pady=(SP.sm, SP.xs))
            tk.Label(row, text=group, bg=p.bg_app, fg=p.text_tertiary,
                     font=theme.font(self.root, TS["small"])).pack(side="left")
            tk.Frame(row, bg=p.border, height=STK.rule).pack(
                side="left", fill="x", expand=True, padx=(SP.sm, 0))
            for ctype in types:
                meta = channels.CHANNEL_META.get(ctype)
                if not meta:
                    continue
                subtitle, dot, lit = self._row_state(ctype)
                item = W.ListRow(
                    host, p, channels.short_label(ctype),
                    subtitle=subtitle, dot=dot, icon_on=lit,
                    active=ctype == self.current_channel,
                    command=lambda c=ctype: self._select_channel(c),
                    bg=p.bg_app, icon=meta.get("icon", "bell"),
                )
                item.pack(fill="x")
                self.ch_rows[ctype] = item
        tk.Frame(host, bg=p.bg_app, height=SP.sm).pack(fill="x")

    def _sync_channel_rows(self) -> None:
        """就地刷新左栏每一行的状态，不重建控件（重建会闪）。"""
        for ctype, row in self.ch_rows.items():
            if not row.winfo_exists():
                continue
            subtitle, dot, lit = self._row_state(ctype)
            row.update_state(subtitle=subtitle, dot=dot, icon_on=lit)
        self._sync_counts()

    def _sync_counts(self) -> None:
        p = self.pal
        enabled = len(config.enabled_channels(self.cfg))
        total = len(channels.CHANNEL_META)
        if self.ch_count_pill is not None and self.ch_count_pill.winfo_exists():
            self.ch_count_pill.set_text(
                f"{enabled}/{total}",
                fg=p.success if enabled else p.warning,
                fill=p.success_soft if enabled else p.warning_soft,
            )
        item = self.nav_items.get("channels")
        if item is not None and item.winfo_exists():
            item.set_badge(str(enabled) if enabled else "")
        self._paint_chain()

    def _select_channel(self, ctype: str) -> None:
        """切换通道：只换右栏，左栏仅更新选中态。"""
        if ctype == self.current_channel or ctype not in channels.CHANNEL_META:
            return
        self.cfg = self._collect()
        self.current_channel = ctype
        self.ch_vars = {}
        for k, row in self.ch_rows.items():
            row.set_active(k == ctype)
        row = self.ch_rows.get(ctype)
        if self.ch_list_sc is not None and row is not None:
            anim.later(self.ch_list_sc, 20, lambda: self.ch_list_sc.see(row))
        self._build_channel_form(animate=True)

    def _build_channel_form(self, animate: bool = True) -> None:
        """重建右栏表单。

        新面板先把内容全部建好，再销毁旧面板——中间不 yield 给 Tk，
        所以不会出现「先空白一帧再画出内容」的闪烁。
        """
        host = self.ch_form_host
        if host is None or not host.winfo_exists():
            return
        old = host.winfo_children()
        # 表单重建后旧按钮已经销毁，引用必须先失效，否则后台任务结束时
        # 会去操作一个不存在的控件
        self.ch_test_btn = None
        self.ch_openid_btn = None
        self.ch_why_lbl = None

        sc = W.ScrollFrame(host, self.pal, bg=self.pal.bg_app)
        sc.place(x=0, y=0, relwidth=1, relheight=1)
        # 表单内容走 660 居中列：通道详情是长表单，1360px 下如果铺满，
        # 「密钥」和右边的「测试」按钮会隔着一整个屏幕，读不成一行。
        self._channel_form(self._center_col(sc.inner))

        for w in old:
            w.destroy()

        self.ch_form_sc = sc
        if animate:
            sc.slide_in(dx=SP.md, dy=SP.xxs, duration=MOTION.base)

    def _channel_form(self, parent) -> None:
        p = self.pal
        ctype = self.current_channel
        meta = channels.CHANNEL_META.get(ctype)
        if not meta:
            return
        ch = config.get_channel(self.cfg, ctype) or {}
        miss = channels.missing_keys(ctype, ch)
        on = bool(ch.get("enabled"))

        # -- 头部：出口的身份 + 开关
        head = tk.Frame(parent, bg=p.bg_app)
        head.pack(fill="x", pady=(SP.xxs, 0))

        sw = W.Switch(head, p, on, bg=p.bg_app,
                      command=lambda v: self._on_channel_switch(ctype, v))
        sw.pack(side="right", padx=(SP.md, 0))
        self.ch_vars.setdefault(ctype, {})["__enabled__"] = sw

        badge = tk.Canvas(head, width=DIM.BADGE, height=DIM.BADGE, bg=p.bg_app,
                          highlightthickness=0, bd=0)
        badge.pack(side="left")
        W.draw_badge(badge, 0, 0, DIM.BADGE, meta.get("icon", "bell"),
                     p.brand if on else p.bg_subtle,
                     p.on_brand if on else p.text_tertiary,
                     icon_scale=0.5)

        info = tk.Frame(head, bg=p.bg_app)
        info.pack(side="left", fill="x", expand=True, padx=(SP.md, SP.sm))
        title_row = tk.Frame(info, bg=p.bg_app)
        title_row.pack(fill="x")
        # 用短名做标题：完整 label 里带着「（自建，推荐）」，会和右边的
        # 状态胶囊重复，窄窗口下还会把胶囊挤出可视区。
        tk.Label(title_row, text=channels.short_label(ctype), bg=p.bg_app,
                 fg=p.text,
                 font=theme.font(self.root, TS["title"], "bold")).pack(side="left")
        if on:
            state, fg, fill = "已启用", p.success, p.success_soft
        elif miss:
            state, fg, fill = "缺配置", p.warning, p.warning_soft
        else:
            state, fg, fill = "未启用", p.text_tertiary, p.bg_subtle
        W.Pill(title_row, p, state, fg=fg, fill=fill, bg=p.bg_app,
               size=TS["small"]).pack(side="left", padx=(SP.sm, 0))
        if ctype == "wxmp":
            W.Pill(title_row, p, "推荐", bg=p.bg_app,
                   size=TS["small"]).pack(side="left", padx=(SP.xs, 0))

        W.Paragraph(info, p, meta["summary"], p.bg_app, p.text_tertiary,
                    size=TS["small"], spacing=SP.xxs).pack(fill="x", pady=(SP.xs, 0))

        # -- 缺配置：一条色带，不是卡片。左边 3px 竖条就是「这里有阻塞」。
        if miss:
            band = tk.Frame(parent, bg=p.warning_soft)
            band.pack(fill="x", pady=(SP.md, 0))
            tk.Frame(band, bg=p.warning, width=S(3)).pack(side="left", fill="y")
            inner = tk.Frame(band, bg=p.warning_soft)
            inner.pack(side="left", fill="x", expand=True,
                       padx=SP.sm, pady=SP.sm)
            labels = channels.FIELD_LABELS.get(ctype, {})
            tk.Label(inner, text="还差：" + "、".join(
                labels.get(k, k) for k in miss),
                bg=p.warning_soft, fg=p.warning,
                font=theme.font(self.root, TS["small"])).pack(anchor="w")
            tk.Label(inner, text="填齐后开关会自动打开，不用手动拨。",
                     bg=p.warning_soft, fg=p.warning,
                     font=theme.font(self.root, TS["small"])).pack(
                anchor="w", pady=(SP.xxs, 0))

        # -- 配置
        sec = W.Group(parent, p, "配置", bg=p.bg_app, gap=SP.sm)
        sec.pack(fill="x", pady=(SP.lg, 0))

        fields = meta.get("fields") or []
        if fields:
            required = set(channels.REQUIRED.get(ctype, ()))
            grid = tk.Frame(sec.body, bg=p.bg_app)
            grid.pack(fill="x")
            grid.columnconfigure(0, weight=1, uniform="f")
            grid.columnconfigure(1, weight=1, uniform="f")
            row = col = 0
            for i, (key, label, kind, default, hint) in enumerate(fields):
                value = ch.get(key, default)
                if kind == "bool":
                    # 开关独占一行：和输入框挤在同一格里，看起来会像是那个
                    # 输入框的附属选项，容易误读
                    if col:
                        row, col = row + 1, 0
                    cell = tk.Frame(grid, bg=p.bg_app)
                    cell.grid(row=row, column=0, columnspan=2, sticky="ew",
                              padx=SP.xs, pady=(SP.xs, SP.lg))
                    var = W.Switch(cell, p,
                                   str(value).lower() in ("1", "true", "yes"),
                                   bg=p.bg_app, command=lambda v: self._mark_dirty())
                    var.pack(anchor="w")
                    tk.Label(cell, text=label, bg=p.bg_app, fg=p.text_secondary,
                             font=theme.font(self.root, TS["small"])).pack(
                        anchor="w", pady=(SP.xs, 0))
                    if hint:
                        tk.Label(cell, text=hint, bg=p.bg_app, fg=p.text_tertiary,
                                 font=theme.font(self.root, TS["small"])).pack(
                            anchor="w", pady=(SP.xxs, 0))
                    self.ch_vars.setdefault(ctype, {})[key] = var
                    row, col = row + 1, 0
                    continue
                span = 2 if (col == 0 and i == len(fields) - 1) else 1
                fld = self._form_field(
                    grid, label, "" if value is None else str(value), hint,
                    show="•" if kind == "password" else None,
                    multiline=(kind == "textarea"),
                    required=key in required,
                )
                fld.master.grid(row=row, column=col, columnspan=span,
                                sticky="ew", padx=SP.xs, pady=(0, SP.lg))
                self.ch_vars.setdefault(ctype, {})[key] = fld
                col += span
                if col >= 2:
                    row, col = row + 1, 0
        else:
            tk.Label(sec.body, text="这个通道不需要额外配置，开关打开即可使用。",
                     bg=p.bg_app, fg=p.text_tertiary,
                     font=theme.font(self.root, TS["small"])).pack(
                anchor="w", pady=(SP.xxs, SP.xs))

        # -- 操作
        ops = tk.Frame(parent, bg=p.bg_app)
        ops.pack(fill="x", pady=(SP.xs, SP.sm))
        self.ch_test_btn = W.Button(ops, "测试此通道", p,
                                    command=lambda: self._test_channel(ctype),
                                    variant="secondary")
        self.ch_test_btn.pack(side="left")
        # 原因说明紧跟在按钮后面：禁用的按钮必须当场说明为什么灰着，
        # 让人去猜、或者靠弹一条会消失的提示，都是把成本推给用户。
        self.ch_why_lbl = tk.Label(ops, text="", bg=p.bg_app, fg=p.text_tertiary,
                                   font=theme.font(self.root, TS["small"]))
        self.ch_why_lbl.pack(side="left", padx=(SP.sm, 0))
        if ctype == "wxmp":
            self.ch_openid_btn = W.Button(ops, "自动获取 openid", p,
                                          command=self._fetch_openid,
                                          variant="ghost")
            self.ch_openid_btn.pack(side="right", padx=(SP.sm, 0))
            W.Button(ops, "打开测试号后台", p,
                     command=lambda: self._open(
                         "https://mp.weixin.qq.com/debug/cgi-bin/sandbox?t=sandbox/login"),
                     variant="ghost").pack(
                side="right")
        elif ctype == "wecom_app":
            W.Button(ops, "打开企业微信后台", p,
                     command=lambda: self._open("https://work.weixin.qq.com/"),
                     variant="ghost").pack(
                side="right")

        # -- 帮助：渐进披露。没配好默认展开（第一次用需要引导）。
        col = W.Collapsible(parent, p, "怎么配置", bg=p.bg_app,
                            expanded=bool(miss), icon="book")
        col.pack(fill="x", pady=(SP.sm, SP.lg))
        self._para(col.body, meta.get("help", ""), p.bg_app,
                   p.text_secondary, size=TS["small"], pady=SP.xs)

        # 表单里任何一个值变了，都要重新判断两个操作按钮能不能点
        for fld in (self.ch_vars.get(ctype) or {}).values():
            if hasattr(fld, "bind_change"):
                fld.bind_change(self._sync_action_state)
        self._sync_action_state()

    def _sync_action_state(self) -> None:
        """按当前表单内容决定操作按钮可不可用，并把原因写在按钮旁边。

        「测试此通道」在必填项没填完时必然失败（后端直接返回缺字段），
        「自动获取 openid」在没 appID/secret 时也一样。与其让人点一下、
        等一条 1.4 秒就消失的 toast，不如让按钮当场灰掉并写明缺什么。
        """
        if self.current_page != "channels" or self.ch_form_host is None:
            return
        ctype = self.current_channel
        ch = config.get_channel(self._collect(), ctype) or {}
        ready = channels.is_ready(ctype, ch)
        why = ""
        if not ready:
            missing = channels.missing_fields(ctype, ch)
            why = ("还需要填写：" + "、".join(missing)) if missing else "先把配置填完"

        if self.ch_test_btn is not None:
            self.ch_test_btn.set_enabled(ready)
        # 只有文字真的变了才 configure：改 Label 的文字会让它重新量宽度，
        # 进而触发一整轮几何计算。这个方法每敲一个键都会跑一次，
        # 无条件写同一个值等于每次输入都白算一轮布局。
        why = "" if ready else why
        if self.ch_why_lbl is not None:
            try:
                if str(self.ch_why_lbl.cget("text")) != why:
                    self.ch_why_lbl.configure(text=why)
            except tk.TclError:
                return

        if self.ch_openid_btn is not None:
            appid = (ch.get("appid") or "").strip()
            secret = (ch.get("secret") or "").strip()
            ok_wx = bool(appid and secret)
            self.ch_openid_btn.set_enabled(ok_wx)
            if not ok_wx and not why and self.ch_why_lbl is not None:
                hint = "填好 appID 和 appsecret 才能自动获取"
                try:
                    if str(self.ch_why_lbl.cget("text")) != hint:
                        self.ch_why_lbl.configure(text=hint)
                except tk.TclError:
                    pass

    # -- 通道开关 ------------------------------------------------------

    def _on_channel_switch(self, ctype: str, value: bool) -> None:
        """通道开关。

        以前这个开关只改了画布颜色，`enabled` 要等点「保存配置」才落盘，
        左栏的「已启用」也不会跟着变——看着就像点了没反应。
        现在立刻写盘并同步所有显示位置。
        """
        cfg = self._collect()
        ch = config.get_channel(cfg, ctype)
        if not ch:
            return
        sw = (self.ch_vars.get(ctype) or {}).get("__enabled__")

        if value:
            miss = channels.missing_keys(ctype, ch)
            if miss:
                if sw is not None:
                    sw.set_value(False, animate=True)
                labels = channels.FIELD_LABELS.get(ctype, {})
                self.toast.show(
                    "还差 " + "、".join(labels.get(k, k) for k in miss) + " 才能启用",
                    "warn", 3600)
                self._focus_field(ctype, miss[0])
                return
            ch["enabled"] = True
            ch.pop("manual_off", None)
        else:
            ch["enabled"] = False
            # 记下「是用户手动关的」，自动启用逻辑就不再插手
            ch["manual_off"] = True

        config.save(cfg)
        self.cfg = cfg
        self._sync_channel_rows()
        self._refresh_detail_state()

        if value:
            self.toast.show(f"{channels.short_label(ctype)} 已启用", "ok")
        else:
            self.toast.show(f"{channels.short_label(ctype)} 已关闭", "info")

    def _focus_field(self, ctype: str, key: str) -> None:
        fld = (self.ch_vars.get(ctype) or {}).get(key)
        if hasattr(fld, "focus_input"):
            try:
                fld.focus_input()
            except Exception:
                pass

    def _refresh_detail_state(self) -> None:
        """启用状态变化后，详情页顶部的图标底色也要跟着变。"""
        if self.current_page != "channels" or self.ch_form_host is None:
            return
        self._build_channel_form(animate=False)

    # ------------------------------------------------------------ 后台任务

    def _run_bg(self, work, done, *, button=None, busy: str | None = None) -> None:
        """把会阻塞的调用挪到后台线程，结果回主线程处理。

        为什么必须这样做：Tk 只有主线程，回调里同步发 HTTP 会让窗口在这段
        时间里完全停止响应。微信模板消息是「取 access_token（10s 超时）+
        发消息（8s 超时）」，最坏情况主线程要冻十几秒，Windows 直接把标题栏
        改成「未响应」；而「正在发送…」的 toast 1.4 秒就消失了，用户看到的
        是「窗口卡死 + 提示已消失」。

        线程规则（写死，别绕过）：
        - 子线程里只跑 `work()`，一个 Tk 调用都不做，连 toast 都不行
        - 主线程用 `after` 轮询线程是否结束，所有界面更新都在主线程里做
        - 同一时刻只允许一个后台动作，避免重复点按钮发起并发请求
        """
        if self._bg_busy:
            self.toast.show("上一个操作还在进行中", "info")
            return
        self._bg_busy = True
        if button is not None:
            button.set_loading(True, busy)

        box: dict = {}

        def runner() -> None:
            try:
                box["value"] = work()
            except Exception as e:  # 异常带回主线程再报，子线程里不许碰界面
                box["error"] = e

        th = threading.Thread(target=runner, daemon=True, name="wbn-bg")
        th.start()

        def poll() -> None:
            try:
                if th.is_alive():
                    self.root.after(60, poll)
                    return
                self._bg_busy = False
                if button is not None:
                    button.set_loading(False)
                done(box.get("value"), box.get("error"))
            except tk.TclError:
                # 窗口在请求飞行途中被关掉了，没什么可更新的
                self._bg_busy = False

        self.root.after(60, poll)

    def _fetch_openid(self) -> None:
        cfg = self._collect()
        ch = config.get_channel(cfg, "wxmp") or {}
        if not (ch.get("appid") or "").strip() or not (ch.get("secret") or "").strip():
            self.toast.show("先填 appID 和 appsecret", "warn")
            return
        fields = {"appid": ch.get("appid"), "secret": ch.get("secret")}

        def work():
            return insight.fetch_openid(fields)

        def done(value, error):
            if error is not None:
                self.toast.show(f"获取失败：{error}", "error", 4200)
                return
            ids, err = value
            if not ids:
                self.toast.show(f"获取失败：{err}", "error", 4200)
                return
            fld = (self.ch_vars.get("wxmp") or {}).get("openid")
            if fld:
                fld.set(ids[0])
            self.toast.show(f"已填入 openid（共 {len(ids)} 位关注者）", "ok")

        self._run_bg(work, done, button=self.ch_openid_btn, busy="查询中…")

    def _test_channel(self, ctype: str) -> None:
        cfg = self._collect()
        if not config.get_channel(cfg, ctype):
            return

        def work():
            # 临时置 enabled 再发送的语义在 insight.test_channel 里
            return insight.test_channel(cfg, ctype)

        def done(value, error):
            if error is not None:
                self.toast.show(f"发送异常：{error}", "error", 4200)
                return
            ok, msg = value
            if ok:
                self.toast.show(f"发送成功：{msg}", "ok")
            else:
                self.toast.show(f"失败：{msg}", "error", 5000)

        self._run_bg(work, done, button=self.ch_test_btn, busy="发送中…")

    # ------------------------------------------------------------ 页：触发条件

    def _page_triggers(self, parent) -> None:
        """页 2 · 触发条件：分组标题 + 52px 设置行 + 底部 1px 分隔线。

        **没有卡片。** 六个开关如果套在描边卡片里，读者要先认出「这是一组」
        再认出「这里有六个选项」，多一层解码；分组标题 + 留白直接给出结论。
        """
        p = self.pal
        inner = self._scroll_area(parent)

        sec = W.Group(inner, p, "推送哪些消息",
                      "选得越多越吵，多数人只需要前两项。", bg=p.bg_app)
        sec.pack(fill="x")

        kinds = ["task_done", "task_failed", "need_input", "tool_failed",
                 "session_start", "session_end"]
        for kind in kinds:
            row = W.Row(sec.body, p, events.KIND_LABELS.get(kind, kind),
                        KIND_DESC.get(kind, ""), bg=p.bg_app)
            row.pack(fill="x")
            sw = W.Switch(row.right, p, bool(self.cfg["triggers"].get(kind)),
                          bg=p.bg_app,
                          command=lambda v: self._mark_dirty())
            sw.pack()
            self.tr_vars[kind] = sw

        sec2 = W.Group(inner, p, "过滤与限流",
                       "用这些规则压掉噪音，避免手机被刷屏", bg=p.bg_app)
        sec2.pack(fill="x")

        self._section(sec2.body, "阈值", p.bg_app)
        f = self.cfg.get("filters", {})
        grid2 = tk.Frame(sec2.body, bg=p.bg_app)
        grid2.pack(fill="x")
        grid2.columnconfigure(0, weight=1, uniform="g")
        grid2.columnconfigure(1, weight=1, uniform="g")

        rows = [
            ("min_duration_seconds", "最短耗时（秒）",
             "低于这个时长不通知，避免「你好」也弹窗"),
            ("quiet_hours", "静默时段", "如 23:00-08:00，多段用逗号分隔"),
            ("dedup_seconds", "去重窗口（秒）", "相同内容在该时间内只推一次"),
            ("max_per_minute", "每分钟上限", "防止突发刷屏"),
            ("max_per_hour", "每小时上限", "长任务密集时可调高"),
            ("tool_fail_threshold", "工具连续失败阈值", "连续失败多少次才通知"),
        ]
        for i, (key, label, hint) in enumerate(rows):
            fld = self._form_field(grid2, label, str(f.get(key, "")), hint)
            fld.master.grid(row=i // 2, column=i % 2, sticky="ew",
                            padx=SP.xs, pady=(0, SP.lg))
            self.filt_vars[key] = fld

        self._section(sec2.body, "范围", p.bg_app)
        lists = [
            ("projects_include", "只通知这些项目",
             "按路径子串匹配，逗号分隔；留空表示全部"),
            ("projects_exclude", "排除这些项目", "逗号分隔"),
            ("keywords_include", "关键词命中",
             "摘要里包含任一词才通知；留空不限制"),
        ]
        grid3 = tk.Frame(sec2.body, bg=p.bg_app)
        grid3.pack(fill="x")
        grid3.columnconfigure(0, weight=1, uniform="g")
        grid3.columnconfigure(1, weight=1, uniform="g")
        for i, (key, label, hint) in enumerate(lists):
            val = f.get(key) or []
            text = ", ".join(str(x) for x in val) if isinstance(val, list) else str(val)
            fld = self._form_field(grid3, label, text, hint)
            span = 2 if (i == len(lists) - 1 and len(lists) % 2 == 1) else 1
            fld.master.grid(row=i // 2, column=i % 2, columnspan=span,
                            sticky="ew", padx=SP.xs, pady=(0, SP.lg))
            self.filt_vars[key] = fld

        qcell = tk.Frame(sec2.body, bg=p.bg_app)
        qcell.pack(fill="x", pady=(SP.xxs, 0))
        self.q_check = W.Checkbox(
            qcell, p, "助手最后一句是问句时，归类为「等你确认」而不是「已完成」",
            bool(f.get("question_as_need_input", True)), bg=p.bg_app,
            font_size=TS["body"], command=lambda v: self._mark_dirty(),
        )
        self.q_check.pack(anchor="w")

    # ------------------------------------------------------------ 页：消息模板

    def _page_template(self, parent) -> None:
        """页 3 · 消息模板：左编辑区吃剩余宽度，右预览固定 320。

        这一页**不吃 660 的正文列上限**：660 是「一段文字读起来多宽」的上限，
        而这里是两栏编辑器——编辑区被压到 316px 的话，一段四行的模板会
        每行折一次，比铺宽更难读。所以列宽上限取
        `660 + 320 + 24`，1360 窗口下编辑区正好 660，和正文列同宽。
        """
        p = self.pal
        sc = W.ScrollFrame(parent, p, bg=p.bg_app)
        sc.pack(fill="both", expand=True)
        self.page_sc = sc
        inner = self._center_col(
            sc.inner, DIM.CONTENT_MAX + DIM.PREVIEW_W + SP.xl)

        bar = tk.Frame(inner, bg=p.bg_app)
        bar.pack(fill="x", pady=(SP.sm, SP.lg))
        opts = [(k, events.KIND_LABELS.get(k, k))
                for k in ("task_done", "task_failed", "need_input",
                          "tool_failed", "test")]
        W.Segmented(bar, p, opts, self.tpl_kind, command=self._on_tpl_kind,
                    bg=p.bg_app, height=DIM.SEG_H,
                    font_size=TS["small"]).pack(side="left")

        cols = tk.Frame(inner, bg=p.bg_app)
        cols.pack(fill="both", expand=True, pady=(0, SP.lg))
        cols.columnconfigure(0, weight=1)
        cols.columnconfigure(1, weight=0)

        ed = tk.Frame(cols, bg=p.bg_app)
        ed.grid(row=0, column=0, sticky="nsew")

        s1 = W.Group(ed, p, "标题与正文",
                     "改动即时反映到右侧预览", bg=p.bg_app, gap=SP.lg)
        s1.pack(fill="x")

        tk.Label(s1.body, text="标题", bg=p.bg_app, fg=p.text_secondary,
                 font=theme.font(self.root, TS["small"])).pack(
            anchor="w", pady=(0, SP.xs))
        self.title_field = W.Field(s1.body, p,
                                   value=self.cfg["titles"].get(self.tpl_kind, ""))
        self.title_field.pack(fill="x")
        self.title_field.bind_change(self._on_preview_change)

        tk.Label(s1.body, text="正文", bg=p.bg_app, fg=p.text_secondary,
                 font=theme.font(self.root, TS["small"])).pack(
            anchor="w", pady=(SP.md, SP.xs))
        self.content_field = W.Field(
            s1.body, p, value=self.cfg["templates"].get(self.tpl_kind, ""),
            multiline=True, height=DIM.FIELD_H_TALL,
        )
        self.content_field.pack(fill="x")
        self.content_field.bind_change(self._on_preview_change)

        s2 = W.Group(ed, p, "可用占位符",
                     "发送前会被替换成真实内容", bg=p.bg_app, gap=SP.xs)
        s2.pack(fill="x", pady=(SP.sm, 0))
        chips = tk.Frame(s2.body, bg=p.bg_app)
        chips.pack(fill="x")
        # 两列而不是三列：D2 的根因就是「按固定三列排，列宽不随容器收缩」。
        # 两列 + 说明文字固定折行宽度（96px）之后，单元格最宽约 172px，
        # 两列 344px，960 最小窗口下编辑区还有 380px，永远装得下。
        chips.columnconfigure(0, weight=1, uniform="chip")
        chips.columnconfigure(1, weight=1, uniform="chip")
        for i, (token, desc) in enumerate(PLACEHOLDERS):
            chip = tk.Frame(chips, bg=p.bg_subtle)
            chip.grid(row=i // 2, column=i % 2, sticky="w",
                      padx=(0, SP.sm), pady=SP.xxs)
            tk.Label(chip, text=token, bg=p.bg_subtle, fg=p.brand_ink,
                     font=theme.font(self.root, TS["small"], mono=True)).pack(
                side="left", padx=(SP.sm, SP.xs), pady=SP.xxs)
            tk.Label(chip, text=desc, bg=p.bg_subtle, fg=p.text_tertiary,
                     font=theme.font(self.root, TS["small"]),
                     anchor="w", justify="left",
                     wraplength=S(96)).pack(
                side="left", padx=(0, SP.sm), pady=SP.xxs)

        prev = tk.Frame(cols, bg=p.bg_app)
        prev.grid(row=0, column=1, sticky="n", padx=(SP.xl, 0))
        self._preview_w = DIM.PREVIEW_W
        self.preview_cv = tk.Canvas(
            prev, width=self._preview_w, height=int(self._preview_w * 1.72),
            bg=p.bg_app, highlightthickness=0, bd=0)
        self.preview_cv.pack()
        self._update_preview()

    def _on_preview_change(self) -> None:
        self._mark_dirty()
        self._update_preview()

    def _on_tpl_kind(self, kind: str) -> None:
        if kind == self.tpl_kind:
            return
        if self.title_field and self.content_field:
            self.cfg["titles"][self.tpl_kind] = self.title_field.get()
            self.cfg["templates"][self.tpl_kind] = self.content_field.get()
        self.tpl_kind = kind
        self.title_field.set(self.cfg["titles"].get(kind, ""))
        self.content_field.set(self.cfg["templates"].get(kind, ""))
        self._update_preview()

    def _update_preview(self) -> None:
        if not self.preview_cv or not self.title_field:
            return
        p = self.pal
        cv = self.preview_cv
        cv.delete("all")

        # 渲染（模板 → 标题/正文、剥 markdown、占位符分析）在 insight 里，
        # 和 webview 版共用一份；这里只管把结果画到手机框里。
        rendered = insight.preview(
            self.cfg, self.tpl_kind,
            self.title_field.get(), self.content_field.get())
        title, content = rendered["title"], rendered["body"]

        dark = p.name == "dark"
        # 手机预览要跟界面同一个明暗，否则深色界面里嵌一块白屏，
        # 像贴了张纸。这里的取值是「微信自身在深/浅色下的配色」。
        if dark:
            bezel, screen = "#0b0d0c", "#151817"
            card_bg, ink, sub = "#242827", "#e8ecea", "#8b9390"
        else:
            bezel, screen = "#d8d8d8", "#ededed"
            card_bg, ink, sub = "#ffffff", "#111111", "#9a9a9a"
        W_, H_ = self._preview_w, int(self._preview_w * 1.72)

        # 机身 + 屏幕
        W.draw_shadow(cv, 0, 0, W_, H_ - S(6), S(28), p, depth=S(8))
        W.round_rect(cv, 0, 0, W_, H_ - S(6), S(28), fill=bezel, outline=bezel)
        W.round_rect(cv, S(7), S(7), W_ - S(7), H_ - S(13), S(22),
                     fill=screen, outline=screen)

        # 灵动岛
        W.round_rect(cv, W_ / 2 - S(24), S(15), W_ / 2 + S(24), S(28), S(7),
                     fill="#000000", outline="#000000")

        # 状态栏
        cv.create_text(S(24), S(21), anchor="w", text="9:41", fill=ink,
                       font=theme.font(self.root, TS["small"], "bold"))
        for i in range(4):
            bh = S(3) + i * S(1.4)
            cv.create_rectangle(W_ - S(50) + i * S(3.4), S(23) - bh,
                                W_ - S(48) + i * S(3.4), S(23),
                                fill=ink, outline=ink)
        W.round_rect(cv, W_ - S(32), S(16), W_ - S(20), S(24), S(2),
                     fill="", outline=sub, width=STK.rule)
        cv.create_rectangle(W_ - S(30), S(18), W_ - S(24), S(22),
                            fill=ink, outline=ink)

        # 导航栏
        ny = S(44)
        W.draw_icon(cv, "chevron", S(16), ny, S(14), ink, STK.icon)
        cv.create_text(W_ / 2, ny + S(7), text="微信测试号", fill=ink,
                       font=theme.font(self.root, TS["small"], "bold"))
        for i in range(3):
            cv.create_oval(W_ - S(38) + i * S(5), ny + S(4),
                           W_ - S(35) + i * S(5), ny + S(7),
                           fill=ink, outline=ink)
        cv.create_line(S(10), ny + S(20), W_ - S(10), ny + S(20),
                       fill=p.border if not dark else "#333333", width=1)

        # 消息气泡
        x0, x1 = S(16), W_ - S(16)
        pad = S(12)
        top = S(82)
        inner_w = x1 - x0 - pad * 2
        emoji, rest = insight.split_emoji(title)
        title_x = x0 + pad
        title_w = inner_w
        first_text = None
        if emoji:
            em = cv.create_text(title_x, top + pad, anchor="nw", text=emoji,
                                fill=ink, font=("Segoe UI Emoji", TS["small"]))
            first_text = em
            eb = cv.bbox(em)
            adv = (eb[2] - eb[0]) if eb else S(14)
            title_x += adv + S(4)
            title_w = max(S(40), inner_w - adv - S(4))
        t = cv.create_text(title_x, top + pad, anchor="nw", text=rest or title,
                           width=title_w, fill=ink,
                           font=theme.font(self.root, TS["small"], "bold"))
        first_text = first_text or t
        tb = cv.bbox(t)
        body_top = (tb[3] if tb else top) + S(7)
        c = cv.create_text(x0 + pad, body_top, anchor="nw",
                           text=content or "（正文为空）",
                           width=inner_w, fill=ink,
                           font=theme.font(self.root, TS["small"]))
        cb = cv.bbox(c)
        bottom = (cb[3] if cb else body_top) + pad
        bottom = max(bottom, body_top + S(26))
        bottom = min(bottom, H_ - S(92))
        bg = W.round_rect(cv, x0, top, x1, bottom, S(10),
                          fill=card_bg, outline=card_bg)
        # 气泡背景要垫在文字下面，但不能沉到手机外框/屏幕之下。
        # 注意要垫在「最前面那个文字项」下面——标题前面还有 emoji 时，
        # 只把 bg 压到标题下面会把 emoji 盖住。
        cv.tag_lower(bg, first_text)

        # 输入栏
        iy = H_ - S(78)
        W.round_rect(cv, S(14), iy, W_ - S(14), iy + S(28), S(9),
                     fill=card_bg, outline=card_bg)
        cv.create_text(S(26), iy + S(14), anchor="w", text="输入消息",
                       fill=sub, font=theme.font(self.root, TS["small"]))
        for i in range(3):
            cx = W_ - S(40) + i * S(8)
            cv.create_oval(cx, iy + S(11), cx + S(6), iy + S(17),
                           fill=sub, outline=sub)

    # ------------------------------------------------------------ 页：运行状态

    def _page_status(self, parent) -> None:
        """页 4 · 运行状态：一行指标 + 时间线，全程不用卡片。

        指标不用卡片、靠竖线分隔：三个数字是「同一个东西的三个切面」，
        套三个盒子会让它们看起来是三件不相关的事。时间线每行 40px，
        状态用圆点颜色而不是「成功/失败」两个字——两个字占掉 40px 里
        一大半，而颜色一眼能扫完一列。
        """
        p = self.pal
        inner = self._scroll_area(parent)
        stats = self._stats()

        # -- 状态行：左侧一句结论，右侧重新检测
        head = tk.Frame(inner, bg=p.bg_app)
        head.pack(fill="x", pady=(SP.sm, SP.md))
        ok_all = stats["today"] > 0
        tk.Label(head, text="运行中" if ok_all else "今日还没有发出通知",
                 bg=p.bg_app, fg=p.text,
                 font=theme.font(self.root, TS["section"], "bold")).pack(side="left")
        W.Button(head, "重新检测", p, command=self._recheck,
                 variant="secondary").pack(side="right")

        # -- 指标：数字 + 标签，竖线分隔。不做卡片。
        mrow = tk.Frame(inner, bg=p.bg_app)
        mrow.pack(fill="x", pady=(0, SP.lg))
        metrics = [
            ("今日通知", str(stats["today"]), p.text),
            # 这两个是「通道级」的次数，不是「条」数：一条通知会同时
            # 投给所有启用的通道，2 个通道就是 2 次。所以标签写「通道」
            # 不写「送达」，否则读者会拿它和「今日通知」相减。
            ("通道成功", str(stats["ok"]), p.success),
            ("通道失败", str(stats["fail"]),
             p.danger if stats["fail"] else p.text_tertiary),
            ("启用通道", f'{stats["channels"]}/{len(channels.CHANNEL_META)}',
             p.brand_ink),
        ]
        self.stat_metrics = {}
        for i, (label, value, color) in enumerate(metrics):
            if i:
                tk.Frame(mrow, bg=p.border, width=STK.rule).pack(
                    side="left", fill="y", padx=SP.xl)
            m = W.Metric(mrow, p, label, value, color, bg=p.bg_app)
            m.pack(side="left")
            self.stat_metrics[label] = m

        # -- 通道状态：只列已启用的。
        # 把 10 条全列出来会把下面的「最近通知」挤到屏幕外，而且未启用的
        # 通道在这里没有信息量——它们不在这条链路上。
        sec = W.Group(inner, p, "通道状态",
                      "右侧的点表示这条出口现在通不通", bg=p.bg_app)
        sec.pack(fill="x")
        live = [c for c in self.cfg.get("channels", []) if c.get("enabled")]
        idle = len(channels.CHANNEL_META) - len(live)
        for ch in live:
            ctype = ch.get("type")
            meta = channels.CHANNEL_META.get(ctype, {})
            if not meta:
                continue
            W.ListRow(sec.body, p, channels.short_label(ctype),
                      subtitle=meta["summary"],
                      dot=p.brand, icon_on=True, bg=p.bg_app,
                      icon=meta.get("icon", "bell")).pack(fill="x")
        if not live:
            W.EmptyState(
                sec.body, p, "bell", "没有任何通道在跑",
                "去「推送通道」里打开一条，通知才有出口。",
                bg=p.bg_app,
                action=("去配置通道", lambda: self._show_page("channels")),
            ).pack(fill="x")
        elif idle:
            W.Paragraph(
                sec.body, p,
                f"另有 {idle} 条通道未启用，它们不在这条链路上，"
                "需要时去「推送通道」打开。",
                p.bg_app, p.text_tertiary, size=TS["small"],
            ).pack(fill="x", pady=(SP.sm, 0))

        # -- 最近通知：时间轴
        # 副标题说清两件事：来源（今天的通知日志，和「今日通知」同一个文件）、
        # 红点的含义（失败类事件，不是「没发出去」）。
        sec2 = W.Group(inner, p, "最近通知",
                       "最近两天真的发出去的通知，最新的在上面。"
                       "红点表示这次是失败类事件", bg=p.bg_app)
        sec2.pack(fill="x")
        recent = stats["recent"]
        if not recent:
            W.EmptyState(
                sec2.body, p, "bell", "还没有通知记录",
                "装好之后跑一次任务就会出现在这里。想马上验证，点右下角「发送测试」。",
                bg=p.bg_app,
                action=("发送测试", self.on_test),
            ).pack(fill="x")
        else:
            tl = W.Timeline(sec2.body, p, bg=p.bg_app)
            tl.pack(fill="x")
            tl.set_rows(recent)
            self.timeline = tl

        # -- 运行日志：强调块（唯一允许带底色的块）
        sec3 = W.Group(inner, p, "运行日志",
                       "最近 12 行，排查问题用", bg=p.bg_app, gap=SP.sm,
                       inset=True)
        sec3.pack(fill="x")
        W.Paragraph(sec3.body, p, store.tail_log(12) or "（暂无）", p.bg_subtle,
                    p.text_secondary, size=TS["small"], mono=True).pack(
            fill="x")

    def _recheck(self) -> None:
        """重新读日志并刷新这一页。"""
        self.cfg = self._collect()
        self._refresh_status()
        self.toast.show("已重新读取发送记录", "info")

    def _refresh_status(self) -> None:
        """重进运行状态页时刷新数字与时间轴，不重建控件。

        这一页的内容全部来自日志文件，会随时间变化；控件和布局不变，
        所以只需要把值写回去。
        """
        stats = self._stats()
        p = self.pal
        values = {
            "今日通知": (str(stats["today"]), p.text),
            "通道成功": (str(stats["ok"]), p.success),
            "通道失败": (str(stats["fail"]),
                     p.danger if stats["fail"] else p.text_tertiary),
            "启用通道": (f'{stats["channels"]}/{len(channels.CHANNEL_META)}',
                     p.brand_ink),
        }
        for label, (value, color) in values.items():
            m = self.stat_metrics.get(label)
            if m is not None and m.winfo_exists():
                m.set_value(value, color)
        if self.timeline is not None and self.timeline.winfo_exists():
            self.timeline.set_rows(stats["recent"])
            self.timeline.reveal()

    def _stats(self) -> dict:
        """今日统计 + 最近 8 条。解析逻辑在 `insight.stats`，两边共用。

        两处过滤（只数 `channel=` 行、剥标题 emoji）都在那边，别在这里重写。
        """
        return insight.stats(self.cfg)

    # ------------------------------------------------------------ 页：使用教程

    def _page_guide(self, parent) -> None:
        """页 5 · 从零到第一条通知：单栏长文，正文列 660。

        这一页不需要页头标题（顶栏已经有了），但需要一段「开场承诺」：
        告诉读者要花多久、推荐哪条路线。它不是标题，是正文的第一段。
        """
        p = self.pal
        inner = self._scroll_area(parent)

        # -- 开场：一段承诺
        W.Paragraph(
            inner, p,
            "四步走完，全程免费，第一次配置大约三分钟。推荐路线是微信测试号："
            "微信官方的开发者测试号，扫码即得凭证，不用实名、不用付费、不限条数。",
            p.bg_app, p.text_secondary, size=TS["body"],
        ).pack(fill="x", pady=(SP.sm, 0))

        # -- 四步
        sec = W.Group(inner, p, "四步走通", bg=p.bg_app)
        sec.pack(fill="x")
        steps = [
            ("选一条通道",
             "打开「推送通道」，左边选「微信测试号」。它是微信官方的开发者测试号，"
             "扫码即得 appID / appsecret，不用实名、不用付费、不限条数。"),
            ("填三个值并保存",
             "appID、appsecret、模板 ID。模板内容照抄这四行，每行前面的「标签：」不能省，"
             "省了那一行就是空白：状态：{{title.DATA}} ／ 项目：{{project.DATA}} ／ "
             "详情：{{detail.DATA}} ／ 时间：{{time.DATA}}。"
             "想让详情多占几行，就再加「补充：{{detail2.DATA}}」这样的行，一个变量占一行。"
             "openid 留空，点「自动获取 openid」程序会自己拉。"
             "填齐后开关会自动打开——不用手动拨。"),
            ("让手机真的弹出来",
             "见下面「手机弹窗怎么开」一节。微信本身的通知权限、系统的横幅与锁屏权限，"
             "两个都开才会在锁屏上看到。"),
            ("验证一次",
             "点右下角「发送测试」。微信聊天列表里应该立刻出现一条消息；"
             "没出现就去「运行状态」页看日志里的具体错误码。"),
        ]
        for i, (title, body) in enumerate(steps):
            W.Step(sec.body, p, i + 1, title, body, bg=p.bg_app,
                   last=(i == len(steps) - 1)).pack(fill="x")

        # -- 手机弹窗
        sec2 = W.Group(
            inner, p, "手机弹窗怎么开",
            "通道配好只保证「消息发出去了」，能不能弹出来由系统通知权限决定",
            bg=p.bg_app)
        sec2.pack(fill="x")
        blocks = [
            ("微信内", [
                "我 → 设置 → 新消息通知：打开「接收新消息通知」「通知显示消息详情」",
                "找到「微信测试号」这条会话，确认没有开启「消息免打扰」",
                "长按会话 → 置顶，避免被折叠到列表底部",
            ]),
            ("iPhone", [
                "设置 → 通知 → 微信：允许通知 + 横幅 + 声音 + 在锁定屏幕上显示",
                "控制中心里确认「专注模式」没有拦掉微信",
                "设置 → 微信 → 后台 App 刷新：打开（关掉会延迟几分钟）",
            ]),
            ("安卓", [
                "设置 → 应用 → 微信 → 通知管理：允许通知 + 横幅/浮动通知 + 锁屏通知",
                "设置 → 电池 → 应用耗电管理：微信设为「不受限制」",
                "小米 / 华为 / OPPO 还需要在「自启动管理」里放行微信，否则息屏后收不到",
            ]),
        ]
        for name, items in blocks:
            self._section(sec2.body, name, p.bg_app)
            for text in items:
                self._bullet(sec2.body, text, p.bg_app)

        # -- 电脑弹窗
        sec3 = W.Group(
            inner, p, "电脑上也要弹窗",
            "本程序本身不弹窗（宿主已有自己的提示）。想让 Windows 也弹，"
            "配一个带桌面客户端的通道即可。", bg=p.bg_app)
        sec3.pack(fill="x")
        plans = [
            ("方案 A · ntfy（最轻）",
             "通道选「ntfy」，起一个别人猜不到的 topic 填进去。电脑装 ntfy 桌面客户端"
             "（Microsoft Store 搜 ntfy，或 GitHub binwiederhier/ntfy 的 Windows 版），"
             "订阅同一个 topic。之后每条通知都是原生 Windows 通知横幅，还会进操作中心。"
             "完全免费、无需注册。"),
            ("方案 B · 邮件",
             "通道选「邮件（SMTP）」，用 QQ 邮箱的授权码当密码。桌面端用 Outlook、"
             "网易邮箱大师之类的客户端收信，开启它的桌面通知。"
             "好处是手机上也会同时收到邮件推送。"),
            ("方案 C · 企业微信 / 钉钉 / 飞书",
             "已经在用这三个之一的话，配对应的自建应用或群机器人。桌面客户端收到消息时"
             "本来就会弹窗，而且手机上同一个 App 也能收到，等于一箭双雕。"),
        ]
        for name, body in plans:
            self._section(sec3.body, name, p.bg_app)
            self._para(sec3.body, body, p.bg_app, p.text_secondary, pady=SP.xxs)

        # -- 排查：这里的内容确实是「按顺序」，所以编号是有意义的
        sec4 = W.Group(inner, p, "收不到？按这个顺序查", bg=p.bg_app, gap=SP.sm)
        # 全页最后一个分组，底部留 32 而不是 16：再往下就是底栏，
        # 最后一段正文和一条 1px 线之间需要比「组间距」更大的一档留白，
        # 否则滚到底时文字像贴在底栏上。
        sec4.pack(fill="x", pady=(0, SP.xxl))
        checks = [
            ("先看「运行状态」页的「今日通知」",
             "是 0 → 说明根本没发出去，问题在触发条件或限流（比如最短耗时 20 秒、静默时段）。"),
            ("有计数但手机没动静",
             "说明已经发出去了，纯粹是通知权限问题，回到上面「手机弹窗怎么开」逐条对。"),
            ("点「发送测试」报错",
             "错误信息会直接说明原因：40125 是 appsecret 填错，40037 是模板 ID 不对，"
             "43004 是没关注测试号，45009 是调用太频繁（等 10 分钟）。"),
            ("想看完整请求记录",
             "点左下角「日志」，logs/notify-日期.log 里每条都有 ok=True/False 和原始返回。"),
        ]
        for i, (title, body) in enumerate(checks):
            row = tk.Frame(sec4.body, bg=p.bg_app)
            row.pack(fill="x", pady=SP.xs)
            num = tk.Canvas(row, width=S(20), height=S(18), bg=p.bg_app,
                            highlightthickness=0, bd=0)
            num.pack(side="left", anchor="n")
            W.round_rect(num, 0, S(1), S(16), S(17), S(5),
                         fill=p.bg_subtle, outline=p.bg_subtle)
            num.create_text(S(8), S(9), text=str(i + 1), fill=p.text_secondary,
                            font=theme.font(self.root, TS["small"], "bold"))
            cell = tk.Frame(row, bg=p.bg_app)
            cell.pack(side="left", fill="x", expand=True, padx=(SP.sm, 0))
            tk.Label(cell, text=title, bg=p.bg_app, fg=p.text,
                     font=theme.font(self.root, TS["small"], "bold")).pack(anchor="w")
            self._para(cell, body, p.bg_app, p.text_tertiary, pady=SP.xxs)

    # ------------------------------------------------------------ 数据读写

    def _collect(self) -> dict:
        cfg = copy.deepcopy(self.cfg)
        try:
            self._collect_into(cfg)
        except Exception:
            # 控件可能刚被销毁（切主题 / 切页），此时保留上一次的配置即可
            pass
        cfg.setdefault("ui", {})["theme"] = self.theme_mode
        return cfg

    def _collect_into(self, cfg: dict) -> None:
        for ctype, fields in (getattr(self, "ch_vars", None) or {}).items():
            ch = config.get_channel(cfg, ctype)
            if not ch:
                continue
            for key, widget in fields.items():
                # 单个控件读失败（刚被销毁）不应该连累其他字段
                try:
                    if key == "__enabled__":
                        ch["enabled"] = bool(getattr(widget, "value", False))
                    elif hasattr(widget, "get"):
                        ch[key] = widget.get().strip()
                except Exception:
                    continue

        for kind, cb in (getattr(self, "tr_vars", None) or {}).items():
            cfg.setdefault("triggers", {})[kind] = bool(cb.value)

        if getattr(self, "q_check", None) is not None:
            cfg.setdefault("filters", {})["question_as_need_input"] = bool(self.q_check.value)

        # 表单字符串 → config 类型（逗号切列表、数值转 int、布尔化）。
        # 转换规则在 `insight.normalize_filters`，webview 版要用同一套。
        if self.filt_vars:
            raw = {k: fld.get().strip() for k, fld in self.filt_vars.items()}
            cfg.setdefault("filters", {}).update(insight.normalize_filters(raw))

        if getattr(self, "title_field", None) is not None:
            cfg["titles"][self.tpl_kind] = self.title_field.get()
        if getattr(self, "content_field", None) is not None:
            cfg["templates"][self.tpl_kind] = self.content_field.get()

    def _on_master(self, value: bool) -> None:
        self.cfg["enabled"] = value
        config.save(self._collect())
        self._paint_master()
        self._paint_chain()
        self.toast.show("通知已开启" if value else "通知已静音",
                        "ok" if value else "info")

    # ------------------------------------------------------------ 动作

    def on_save(self) -> None:
        """保存：**读-改-写**，不是「拿内存覆盖磁盘」。

        UI 打开期间 hook 进程可能改过 config（比如自动启用某个通道），
        整份覆盖会把那些改动抹掉。`insight.merge_save` 先 load 再逐字段
        合并，两边行为一致。
        """
        res = insight.merge_save(self._collect())
        if not res.get("ok"):
            self.toast.show(f"保存失败：{res.get('error', '')}", "error", 4200)
            return
        self.cfg = res["cfg"]
        turned_on = res.get("turned_on") or []
        self._sync_channel_rows()
        self._dirty = False
        self._paint_dirty()
        self._paint_chain()

        if turned_on:
            self.toast.show("已保存，自动启用 " + "、".join(turned_on), "ok", 3200)
        elif not config.enabled_channels(self.cfg):
            self.toast.show("已保存，但还没有启用任何通道", "warn", 3200)
        else:
            self.toast.show("配置已保存", "ok")

        # 表单里的字段可能被自动启用逻辑改动，重建一次详情页保持一致
        if self.current_page == "channels":
            self._build_channel_form(animate=False)

    def on_reset(self) -> None:
        """丢弃未保存的改动，回到磁盘上的那份配置。

        为什么不弹确认框：这个动作只会丢掉「还没保存的编辑」，而磁盘上
        的那份一直在；反过来，如果用户是想撤销一个刚打的错字，弹框反而
        多一次点击。真正危险的是「覆盖磁盘」，那个动作是「保存」。
        """
        if not self._dirty:
            self.toast.show("没有未保存的改动", "info")
            return
        self.cfg = config.load()
        self._dirty = False
        self.current_channel = self._pick_channel()
        # 先清视图引用再丢页面：否则 _collect 会摸到已销毁的控件
        self._reset_view_state()
        self._drop_page(self.current_page)
        self._show_page(self.current_page)
        if self.master_switch is not None and self.master_switch.winfo_exists():
            self.master_switch.set_value(bool(self.cfg.get("enabled", True)),
                                         animate=True)
        self._paint_dirty()
        self._paint_master()
        self.toast.show("已还原为上次保存的配置", "info")

    def on_test(self) -> None:
        self.on_save()

        def work():
            return pipeline.send_test(config.load())

        def done(value, error):
            if error is not None:
                self.toast.show(f"发送异常：{error}", "error", 4200)
                return
            if not value:
                self.toast.show("没有启用任何通道，先在「推送通道」里打开一个",
                                "warn", 4200)
                return
            ok = [r for r in value if r.get("ok")]
            if ok:
                self.toast.show(
                    f"已发送（{len(ok)}/{len(value)} 成功）：{ok[0].get('msg', '')}",
                    "ok")
            else:
                self.toast.show(f"全部失败：{value[0].get('msg', '')}",
                                "error", 5000)

        self._run_bg(work, done, button=self.test_btn, busy="发送中…")

    def on_open_logs(self) -> None:
        config.ensure_dirs()
        self._open(str(config.LOG_DIR))

    def on_open_root(self) -> None:
        self._open(str(config.ROOT))

    def _open(self, target: str) -> None:
        ok, err = insight.open_path(target)
        if not ok:
            self.toast.show(f"打开失败：{err}", "error")

    # ------------------------------------------------------------ 启动提示

    def _announce_auto(self) -> None:
        """启动时告诉用户「我替你打开了什么」，否则会显得莫名其妙。"""
        if self._auto_on:
            self.toast.show("已自动启用：" + "、".join(self._auto_on), "ok", 3600)
        elif self._auto_off:
            self.toast.show("凭证不全，已关闭：" + "、".join(self._auto_off), "warn", 4200)
        else:
            enabled = len(config.enabled_channels(self.cfg))
            if not enabled:
                self.toast.show("还没有启用任何通道，点左边选一个开始配置",
                                "warn", 4200)


def _boot(root: tk.Tk, app: App, splash) -> None:
    """T1：逐页预热，进度线按页推进（P1 5.2）。

    三条不能破的规矩：

    - **每建一页让出一次事件循环**（`after(60)`）。不让出的话进度线在整个
      T1 期间一帧都画不出来，用户看到的是「启动层卡住了」。
    - **每次回调先查 `splash.alive`**。交接一旦开始，后面的预热必须立刻停：
      这就是 5.3 第 6 条「预热绝不越过 T2」，也是 `check_responsive` 里那个
      「发送期间停顿 390ms」的根治办法。
    - **用户一有输入就放弃剩余预热**（5.3 第 5 条）。他要开始操作了，
      再抢主线程建页就是跟他抢机器。
    """
    keys = ("triggers", "template", "status", "guide")
    state = {"i": 0, "stop": False}

    def step() -> None:
        if not splash.alive:
            return
        if state["stop"] or state["i"] >= len(keys):
            splash.finish()
            return
        try:
            app.prebuild(keys[state["i"]])
        except Exception:
            # 预热失败不是致命错误：用户点进去时 `_show_page` 会现建。
            pass
        state["i"] += 1
        splash.animate_to(splash_mod.progress_for(state["i"], len(keys)))
        root.after(60, step)

    def cancel() -> None:
        state["stop"] = True

    splash.on_user_input(cancel)
    # 等淡入走到「视觉上已经到位」再开始建页：`MOTION.base`(160ms) 时整层
    # alpha 已经到 0.98，再等下去只是让用户多盯一会儿静止的进度线。
    # 建页期间事件循环不转，所以淡入的最后几十毫秒会丢帧——这也是为什么
    # 不把第一页排在 0ms：那样淡入会被 25ms 的建页切成两段。
    root.after(MOTION.base, step)


def main() -> None:
    theme.enable_dpi_awareness()
    theme.set_app_user_model_id()      # 必须在建窗口之前，否则任务栏认不到我们的图标
    config.save_default_if_missing()
    root = tk.Tk()
    # pythonw.exe 下 stderr 是 None，Tk 回调里的异常会彻底消失。
    # 装上钩子后至少能在 logs/notify-*.log 里查到。
    theme.install_tk_exception_hook(root)
    # 启动层要先用 S() 算尺寸，标定必须在它之前。主窗口全程 withdraw，
    # 由启动层独占屏幕，交接时才 deiconify——主窗口不 withdrawn 的话，
    # 用户会看到「空窗口 + 启动层」叠在一起。
    root.withdraw()
    theme.S.calibrate(root)

    pal = theme.resolve((config.load().get("ui") or {}).get("theme", "auto"))
    holder: dict = {}

    def handoff() -> None:
        app = holder.get("app")
        if app is not None:
            app.reveal()

    splash = splash_mod.Splash(root, pal, APP_TITLE, handoff)
    splash.show()
    # 先把启动层真正画到屏幕上再建主窗口：接下来那 200 多毫秒是同步建壳，
    # 期间事件循环不转，启动层只能是静止的。先画出来，用户看到的是
    # 「启动层出现 → 停一下 → 开始动」，而不是「什么都没有 → 突然出现」。
    root.update()

    app = App(root)
    holder["app"] = app
    splash.fade_in()
    _boot(root, app, splash)

    root.bind("<Control-s>", lambda e: app.on_save())
    root.bind("<Control-t>", lambda e: app.on_test())
    root.mainloop()



if __name__ == "__main__":
    main()
