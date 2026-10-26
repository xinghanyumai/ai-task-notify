"""设置中心。

设计说明
--------
这个产品真正的内容不是「一堆设置项」，而是一条链路：

    事件 → 过滤 → 渲染 → 通道 → 手机

所以整套界面的语汇都围绕这条链路建立，而不是围绕表单：

- **左侧导航是一根竖脊**，五个页面是脊上的节点。切换页面时有一个
  点沿着脊滑过去——这是全界面唯一「讲故事」的动效，其余一律克制。
  教程页不在链路上，用一段虚线隔开。
- **通道是链路上的出口**，每一行带一个通 / 堵状态点。
- **运行状态是一根时间轴**，记录这条链路上真实发生过的事。
- **顶栏不是页面标题，而是一句陈述**：你现在这套配置的实际后果是什么。
  这句话在任何页面都可见，因为它才是用户始终关心的那个问题。

结构上刻意「去卡片化」：主内容直接铺在页面底色上，用分节标题 + 一条
向右延伸的细线区分层级。只有设备预览、日志这类真有边界的东西才用卡片。
"""

from __future__ import annotations

import copy
import gc
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path

from wbn import anim, channels, config, events, pipeline, render, store, theme
from wbn import widgets as W
from wbn.theme import TS, S
from wbn.tokens import DIM, MOTION, SP, STK

APP_TITLE = "AI 任务通知"
APP_VER = "个人版 v1.2"

# (key, 导航名, 图标, 顶栏说明, 是否与上一项断开)
PAGES = [
    ("channels", "推送通道", "bell", "选择把消息送到哪里", False),
    ("triggers", "触发条件", "bolt", "决定什么情况下才打扰你", False),
    ("template", "消息模板", "doc", "自定义标题与正文", False),
    ("status", "运行状态", "chart", "发送记录与排查", False),
    ("guide", "使用教程", "book", "从零到收到第一条通知", True),
]

THEME_OPTIONS = [("auto", "跟随"), ("light", "浅色"), ("dark", "深色")]

SAMPLE_PAYLOAD = {
    "hook_event_name": "Stop",
    "session_id": "preview",
    "cwd": "D:/Code/demo-project",
    "last_assistant_message": "重构完成：推送通道已拆成可插拔模块，新增微信测试号通道。",
    # 预览里把耗时、工具名这些也补上，否则模板里的占位符会渲染成「—」
    "_duration": 192,
    "tool_name": "Bash",
    "permission_mode": "acceptEdits",
}

# emoji 的码位区间。预览画布上的字体是微软雅黑，它没有 emoji 字形，
# 直接画会变成「豆腐块」；所以把标题开头的 emoji 拆出来单独用
# Segoe UI Emoji 画，剩下的文字仍用正文字体。
_EMOJI_RANGES = (
    (0x2190, 0x21FF), (0x2300, 0x23FF), (0x25A0, 0x27BF),
    (0x2B00, 0x2BFF), (0xFE00, 0xFE0F), (0x1F000, 0x1FAFF),
)


def _split_emoji(text: str) -> tuple[str, str]:
    """把开头连续的 emoji 与后面的文字分开。"""
    i = 0
    for ch in text:
        o = ord(ch)
        if any(a <= o <= b for a, b in _EMOJI_RANGES):
            i += 1
        elif ch == " " and i > 0:
            i += 1
        else:
            break
    return text[:i].strip(), text[i:].lstrip()


PLACEHOLDERS = [
    ("{app}", "宿主名"), ("{project}", "项目名"), ("{duration}", "耗时"),
    ("{time}", "时间"), ("{date}", "日期"), ("{summary}", "回复摘要"),
    ("{error}", "错误信息"), ("{tool}", "工具名"), ("{host}", "主机名"),
    ("{kind}", "事件类型"), ("{session}", "会话 ID"),
]

# 通道分组：自建 / 第三方 / 本地
CHANNEL_GROUPS = [
    ("自建推送", ["wxmp", "wecom_app"]),
    ("第三方", ["bark", "ntfy", "wecom_bot", "dingtalk_bot",
                "feishu_bot", "smtp", "webhook"]),
    ("本机", ["log"]),
]

# 触发条件的中文说明。放在这里而不是 events.py，因为这是界面文案。
KIND_DESC = {
    "task_done": "一轮任务正常跑完",
    "task_failed": "明确报错或中断",
    "need_input": "助手在等你回答",
    "tool_failed": "工具连续失败（噪音较大）",
    "session_start": "开始新会话",
    "session_end": "会话关闭",
}


def _to_int(text: str, default: int) -> int:
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return default


def _split_list(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,，]", text or "") if x.strip()]


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

        # 骨架引用
        self.page_sc = None
        self.nav_items: dict = {}
        self.nav_rail = None
        self.content = None
        self.chain_dot = None
        self.chain_lbl = None
        self.master_lbl = None
        self.master_switch = None
        self.dirty_dot = None
        self.dirty_lbl = None

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
        """默认打开哪个通道：优先「已启用且配好」的那条，否则第一个自建通道。"""
        for ch in self.cfg.get("channels", []):
            t = ch.get("type")
            if ch.get("enabled") and channels.is_ready(t, ch):
                return t
        for ch in self.cfg.get("channels", []):
            if channels.is_ready(ch.get("type"), ch):
                return ch["type"]
        return "wxmp"

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
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes

            self.root.update_idletasks()
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
        self.content = tk.Frame(self.main, bg=p.bg_app)
        self.content.pack(fill="both", expand=True)
        self.content.bind("<Configure>", self._on_content_conf)
        self._build_footer()

        self.toast = W.Toast(self.root, p)
        self._show_page(self.current_page)
        self.root.after_idle(self._apply_titlebar)
        self.root.after(120, self._apply_titlebar)

    def _build_sidebar(self) -> None:
        p = self.pal
        side = tk.Frame(self.root, bg=p.sidebar, width=DIM.SIDEBAR_W)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(self.root, bg=p.border, width=STK.rule).place(
            x=DIM.SIDEBAR_W - STK.rule, y=0, relheight=1)

        # -- 品牌区
        brand = tk.Frame(side, bg=p.sidebar)
        brand.pack(fill="x", padx=SP.lg, pady=(SP.lg, SP.lg))
        mark = tk.Canvas(brand, width=DIM.LOGO, height=DIM.LOGO, bg=p.sidebar,
                         highlightthickness=0, bd=0)
        mark.pack(side="left")
        W.round_rect_grad(mark, 0, 0, DIM.LOGO, DIM.LOGO, S(10),
                          p.brand_top, p.brand_bottom)
        W.draw_icon(mark, "bell", SP.sm, SP.sm, DIM.ICON, p.on_brand, STK.icon)

        titles = tk.Frame(brand, bg=p.sidebar)
        titles.pack(side="left", padx=(SP.sm, 0))
        tk.Label(titles, text=APP_TITLE, bg=p.sidebar, fg=p.text,
                 font=theme.font(self.root, TS["section"], "bold")).pack(anchor="w")
        tk.Label(titles, text=APP_VER, bg=p.sidebar, fg=p.text_tertiary,
                 font=theme.font(self.root, TS["micro"])).pack(
            anchor="w", pady=(SP.xxs, 0))

        tk.Frame(side, bg=p.border, height=STK.rule).pack(fill="x", padx=SP.lg)

        # -- 导航：竖脊 + 节点
        navwrap = tk.Frame(side, bg=p.sidebar)
        navwrap.pack(fill="x", pady=(SP.sm, 0))
        self.nav_rail = W.NavRail(navwrap, p, bg=p.sidebar, width=DIM.NAV_RAIL_W)
        self.nav_rail.pack(side="left", fill="y")

        nav = tk.Frame(navwrap, bg=p.sidebar)
        nav.pack(side="left", fill="both", expand=True, padx=(0, SP.sm))
        for key, label, icon, _desc, gap in PAGES:
            item = W.NavItem(nav, p, key, label, icon, self._show_page,
                             bg=p.sidebar, height=DIM.NAV_ITEM_H)
            item.pack(fill="x", pady=SP.xxs)
            self.nav_items[key] = item
            self.nav_rail.add(item, gap_before=gap)
        anim.later(side, 40, self.nav_rail.repaint)

        # -- 底部：接入状态
        foot = tk.Frame(side, bg=p.sidebar)
        foot.pack(side="bottom", fill="x", padx=SP.lg, pady=(0, SP.lg))
        tk.Frame(foot, bg=p.border, height=STK.rule).pack(fill="x", pady=(0, SP.sm))

        installed = self._hooks_installed()
        state = tk.Frame(foot, bg=p.sidebar)
        state.pack(fill="x")
        dot = tk.Canvas(state, width=DIM.DOT_W, height=DIM.DOT_H, bg=p.sidebar,
                        highlightthickness=0, bd=0)
        dot.pack(side="left")
        color = p.success if installed else p.warning
        dot.create_oval(S(1), S(4), S(7), S(10), fill=color, outline=color)
        tk.Label(state, text="已接入宿主" if installed else "未接入宿主",
                 bg=p.sidebar, fg=p.text_secondary,
                 font=theme.font(self.root, TS["small"])).pack(
            side="left", padx=(SP.xxs, 0))

        tip = ("任务结束时自动推送" if installed
               else "运行 install.py 完成接入")
        tk.Label(foot, text=tip, bg=p.sidebar, fg=p.text_tertiary,
                 font=theme.font(self.root, TS["micro"])).pack(
            anchor="w", pady=(SP.xs, 0))

    def _build_topbar(self) -> None:
        """顶栏 = 一句关于当前配置的陈述句，不是页面标题。

        用户在任何页面都想知道「我现在到底会不会收到通知」，所以这一行
        放全局状态；页面自身的身份由左侧高亮的节点和页面第一个分节说明。
        """
        p = self.pal
        bar = tk.Frame(self.main, bg=p.bg_app, height=DIM.TOPBAR_H)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        right = tk.Frame(bar, bg=p.bg_app)
        right.pack(side="right", padx=(0, SP.xl))
        W.Segmented(right, p, THEME_OPTIONS, self.theme_mode,
                    command=self._switch_theme, bg=p.bg_app,
                    height=DIM.SEG_H, font_size=TS["micro"]).pack(side="left")

        tk.Frame(right, bg=p.border, width=STK.rule,
                 height=DIM.DIVIDER_V_H).pack(
            side="left", padx=SP.lg)
        self.master_switch = W.Switch(right, p, bool(self.cfg.get("enabled", True)),
                                      command=self._on_master, bg=p.bg_app)
        self.master_switch.pack(side="right")
        self.master_lbl = tk.Label(right, text="", bg=p.bg_app,
                                   font=theme.font(self.root, TS["small"]))
        self.master_lbl.pack(side="right", padx=(0, SP.sm))

        # 左侧必须 expand，宽度由 packer 决定；否则宽度会跟着文字长度变，
        # 省略计算就会陷入「越省越窄、越窄越省」的死循环。
        left = tk.Frame(bar, bg=p.bg_app)
        left.pack(side="left", fill="both", expand=True, padx=(SP.xl, SP.md))
        self.chain_dot = tk.Canvas(left, width=DIM.DOT_W, height=DIM.DOT_H,
                                   bg=p.bg_app,
                                   highlightthickness=0, bd=0)
        self.chain_dot.pack(side="left")
        self.chain_lbl = tk.Label(left, text="", bg=p.bg_app, fg=p.text,
                                  font=theme.font(self.root, TS["body"]),
                                  anchor="w", justify="left")
        self.chain_lbl.pack(side="left", padx=(SP.xxs, 0))
        left.bind("<Configure>", self._fit_chain)

        self._paint_master()
        self._paint_chain()

    def _chain_sentence(self) -> tuple[str, str]:
        """把当前配置翻译成一句人话：(陈述句, 状态色)。"""
        p = self.pal
        cfg = self.cfg
        if not cfg.get("enabled", True):
            return "已静音，任何事件都不会发出通知", p.warning

        on = [c.get("type") for c in config.enabled_channels(cfg) if c.get("type")]
        if not on:
            return "没有可用通道，通知发不出去", p.danger

        kinds = [k for k, v in (cfg.get("triggers") or {}).items() if v]
        if not kinds:
            return "没有勾选任何事件，不会触发通知", p.warning
        pair = set(kinds)
        if {"task_done", "task_failed"} <= pair:
            when = "任务完成或失败时"
        elif pair == {"task_done"}:
            when = "任务完成时"
        elif pair == {"task_failed"}:
            when = "任务失败时"
        else:
            when = "、".join(events.KIND_LABELS.get(k, k) for k in kinds[:2])
            if len(kinds) > 2:
                when += f"等 {len(kinds)} 类事件时"

        # log 通道只写本地文件，不能算「送到手机」——混进这句话就是骗人
        remote = [t for t in on if t != "log"]
        if not remote:
            return "只写入本地日志，手机上收不到", p.warning

        names = "、".join(channels.short_label(t) for t in remote[:2])
        if len(remote) > 2:
            names += f"等 {len(remote)} 条通道"
        return f"{when}，经{names}，送到你手机", p.success

    def _paint_chain(self) -> None:
        if self.chain_lbl is None or not self.chain_lbl.winfo_exists():
            return
        text, color = self._chain_sentence()
        self._chain_text = text
        self.chain_dot.delete("all")
        self.chain_dot.create_oval(S(1), S(5), S(7), S(11),
                                   fill=color, outline=color)
        self._fit_chain()

    def _fit_chain(self, _e=None) -> None:
        """按实测可用宽度省略链路陈述句。

        不能用「超过 N 个字就截断」这种固定阈值：字体、DPI、窗口宽度
        三者任何一个变了，阈值就不准。
        """
        if self.chain_lbl is None or not self.chain_lbl.winfo_exists():
            return
        text = getattr(self, "_chain_text", "")
        if not text:
            return
        try:
            avail = (self.chain_lbl.master.winfo_width()
                     - self.chain_dot.winfo_width() - S(6))
        except tk.TclError:
            return
        if avail <= 20:
            return
        f = theme.font(self.root, TS["body"])
        shown = W.elide(self.chain_lbl, f, text, avail)
        if shown != self.chain_lbl.cget("text"):
            self.chain_lbl.configure(text=shown)

    def _paint_master(self) -> None:
        if self.master_lbl is None:
            return
        on = bool(self.cfg.get("enabled", True))
        self.master_lbl.configure(
            text="通知已开启" if on else "通知已静音",
            fg=self.pal.text_secondary if on else self.pal.text_tertiary)

    def _build_footer(self) -> None:
        p = self.pal
        foot = tk.Frame(self.main, bg=p.bg_card, height=DIM.FOOTER_H)
        foot.pack(fill="x", side="bottom")
        foot.pack_propagate(False)
        tk.Frame(foot, bg=p.border, height=STK.rule).pack(side="top", fill="x")

        self.save_btn = W.Button(foot, "保存配置", p, command=self.on_save,
                                 variant="primary", min_width=DIM.BTN_MIN_W)
        self.save_btn.pack(side="right", padx=(SP.sm, SP.xl), pady=SP.md)
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
        try:
            import json

            for path in (Path.home() / ".workbuddy-ai" / "settings.json",
                         Path.home() / ".codebuddy" / "settings.json"):
                if not path.exists():
                    continue
                d = json.loads(path.read_text(encoding="utf-8"))
                if "hook.py" in json.dumps(d.get("hooks", {}), ensure_ascii=False):
                    return True
        except Exception:
            pass
        return False

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
        self.current_page = page
        self.current_channel = chan
        self._dirty = dirty
        self._build_all()

    def _reset_view_state(self, include_shell: bool = False) -> None:
        """清空视图引用。

        `include_shell=False` 时保留侧栏 / 顶栏 / 底栏的引用——切页时这些
        骨架并没有重建，把它们置空会让后续刷新摸到 None。
        """
        self.ch_vars, self.tr_vars, self.filt_vars = {}, {}, {}
        self.title_field = None
        self.content_field = None
        self.preview_cv = None
        self.q_check = None
        self.ch_list_inner = None
        self.ch_list_sc = None
        self.ch_form_host = None
        self.ch_form_sc = None
        self.ch_rows = {}
        self.ch_count_pill = None
        self.left_col = None
        self.page_sc = None
        if include_shell:
            self.nav_items = {}
            self.nav_rail = None
            self.content = None
            self.chain_dot = None
            self.chain_lbl = None
            self.master_lbl = None
            self.dirty_dot = None
            self.dirty_lbl = None

    def _show_page(self, key: str) -> None:
        if self.content is None:
            return
        if key == self.current_page and self.content.winfo_children():
            # 已经在当前页：只刷新高亮，不重建（避免无谓的重绘）
            for k, item in self.nav_items.items():
                item.set_selected(k == key)
            return

        self.cfg = self._collect()
        self.current_page = key
        for w in self.content.winfo_children():
            w.destroy()
        self._reset_view_state()
        self.current_page = key

        builder = {
            "channels": self._page_channels,
            "triggers": self._page_triggers,
            "template": self._page_template,
            "status": self._page_status,
            "guide": self._page_guide,
        }.get(key, self._page_channels)
        builder(self.content)

        for k, item in self.nav_items.items():
            item.set_selected(k == key)
        if self.nav_rail is not None:
            anim.later(self.nav_rail, 16, lambda: self.nav_rail.set_selected(key))

        self._paint_chain()
        self._enter_anim()

    def _enter_anim(self) -> None:
        """页面进场：内容从下方轻推到位。

        幅度很小（8px / 180ms）——它的作用是让人知道「换了个地方」，
        不是表演。大幅 fade-and-slide 是模板感最强的信号之一。
        """
        if self.current_page == "channels":
            if self.ch_list_sc is not None:
                self.ch_list_sc.slide_in(dy=SP.sm, duration=MOTION.base)
            if self.ch_form_sc is not None:
                self.ch_form_sc.slide_in(dx=SP.md, dy=SP.xxs, duration=MOTION.base)
        elif self.page_sc is not None:
            self.page_sc.slide_in(dy=SP.sm, duration=MOTION.base)

    def _on_content_conf(self, e) -> None:
        """窄窗口下的尺寸适配。桌面程序没有断点，只有「别撑破」。"""
        if e.width <= 1:
            return
        if self.current_page == "channels" and self.left_col is not None:
            w = max(DIM.LIST_COL_MIN, min(DIM.LIST_COL_MAX, int(e.width * 0.30)))
            if abs(self.left_col.winfo_width() - w) > S(6):
                self.left_col.configure(width=w)
        elif self.current_page == "template" and self.preview_cv is not None:
            w = max(DIM.PREVIEW_MIN, min(DIM.PREVIEW_MAX, int(e.width * 0.34)))
            if abs(w - self._preview_w) > S(6):
                self._preview_w = w
                try:
                    self.preview_cv.configure(width=w, height=int(w * 1.72))
                except tk.TclError:
                    return
                self._update_preview()

    # ------------------------------------------------------------ 通用小组件

    def _scroll_area(self, parent) -> tk.Frame:
        sc = W.ScrollFrame(parent, self.pal, bg=self.pal.bg_app)
        sc.pack(fill="both", expand=True)
        self.page_sc = sc
        return sc.inner

    def _section(self, parent, text: str, bg: str) -> None:
        """分节内部的小标题：文字 + 一条向右延伸的细线。

        比 W.Section 轻一档，用来在同一个分节里再分小组。
        """
        p = self.pal
        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", pady=(SP.sm, SP.sm))
        tk.Label(row, text=text, bg=bg, fg=p.text_tertiary,
                 font=theme.font(self.root, TS["micro"])).pack(side="left")
        tk.Frame(row, bg=p.border, height=STK.rule).pack(
            side="left", fill="x", expand=True, padx=(SP.sm, 0))

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
                     font=theme.font(self.root, TS["micro"]),
                     justify="left", anchor="w",
                     wraplength=DIM.HINT_WRAP).pack(anchor="w", pady=(SP.xs, 0))
        return fld

    # ------------------------------------------------------------ 页：推送通道

    def _page_channels(self, parent) -> None:
        p = self.pal
        wrap = tk.Frame(parent, bg=p.bg_app)
        wrap.pack(fill="both", expand=True, padx=SP.xl, pady=(0, SP.md))

        # -- 左：出口列表。不做成卡片，靠一条竖线与右侧详情分开。
        left = tk.Frame(wrap, bg=p.bg_app, width=DIM.LIST_COL_W)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.left_col = left

        head = tk.Frame(left, bg=p.bg_app)
        head.pack(fill="x", pady=(SP.xxs, SP.sm))
        tk.Label(head, text="全部通道", bg=p.bg_app, fg=p.text,
                 font=theme.font(self.root, TS["section"], "bold")).pack(side="left")
        self.ch_count_pill = W.Pill(head, p, "", bg=p.bg_app, size=TS["micro"])
        self.ch_count_pill.pack(side="right")

        sc = W.ScrollFrame(left, p, bg=p.bg_app)
        sc.pack(fill="both", expand=True)
        self.ch_list_inner = sc.inner
        self.ch_list_sc = sc

        tk.Frame(wrap, bg=p.border, width=STK.rule).pack(
            side="left", fill="y", padx=(SP.md, 0))

        # -- 右：出口详情（独立滚动）
        right = tk.Frame(wrap, bg=p.bg_app)
        right.pack(side="left", fill="both", expand=True, padx=(SP.lg, 0))
        self.ch_form_host = right

        self._build_channel_list()
        self._build_channel_form(animate=False)
        self._sync_channel_rows()

    def _row_state(self, ctype: str) -> tuple[str, str, bool]:
        """列表行的 (副标题, 状态点颜色, 是否点亮)。"""
        p = self.pal
        ch = config.get_channel(self.cfg, ctype) or {}
        if ch.get("enabled"):
            return "已启用", p.brand, True
        miss = channels.missing_keys(ctype, ch)
        if not miss:
            return "已配置，未启用", p.warning, False
        label = channels.FIELD_LABELS.get(ctype, {}).get(miss[0], miss[0])
        more = f" 等 {len(miss)} 项" if len(miss) > 1 else ""
        return f"待配置：缺 {label}{more}", p.border_strong, False

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
                     font=theme.font(self.root, TS["micro"])).pack(side="left")
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
        self._channel_form(sc.inner)

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
               size=TS["micro"]).pack(side="left", padx=(SP.sm, 0))
        if ctype == "wxmp":
            W.Pill(title_row, p, "推荐", bg=p.bg_app,
                   size=TS["micro"]).pack(side="left", padx=(SP.xs, 0))

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
                     font=theme.font(self.root, TS["micro"])).pack(
                anchor="w", pady=(SP.xxs, 0))

        # -- 配置
        sec = W.Section(parent, p, "配置", bg=p.bg_app, gap=SP.sm)
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
                                 font=theme.font(self.root, TS["micro"])).pack(
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
                                   font=theme.font(self.root, TS["micro"]))
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
        appid = (ch.get("appid") or "").strip()
        secret = (ch.get("secret") or "").strip()
        if not appid or not secret:
            self.toast.show("先填 appID 和 appsecret", "warn")
            return

        def work():
            return channels.wx_followers(appid, secret)

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
        ch = config.get_channel(cfg, ctype)
        if not ch:
            return
        was = ch.get("enabled")
        ch["enabled"] = True

        def work():
            try:
                return pipeline.send_test(cfg, [ctype])
            finally:
                ch["enabled"] = was

        def done(value, error):
            if error is not None:
                self.toast.show(f"发送异常：{error}", "error", 4200)
                return
            if not value:
                self.toast.show("没有可用的目标通道", "warn")
                return
            r = value[0]
            if r.get("ok"):
                self.toast.show(f"发送成功：{r.get('msg', '')}", "ok")
            else:
                self.toast.show(f"失败：{r.get('msg', '')}", "error", 5000)

        self._run_bg(work, done, button=self.ch_test_btn, busy="发送中…")

    # ------------------------------------------------------------ 页：触发条件

    def _page_triggers(self, parent) -> None:
        p = self.pal
        inner = self._scroll_area(parent)

        sec = W.Section(inner, p, "通知哪些事件",
                        "勾选后才会推送对应类型的消息。不确定就只留前两项。",
                        bg=p.bg_app)
        sec.pack(fill="x", padx=SP.xl, pady=(SP.xs, 0))

        grid = tk.Frame(sec.body, bg=p.bg_app)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1, uniform="t")
        grid.columnconfigure(1, weight=1, uniform="t")

        kinds = ["task_done", "task_failed", "need_input", "tool_failed",
                 "session_start", "session_end"]
        for i, kind in enumerate(kinds):
            cell = tk.Frame(grid, bg=p.bg_app)
            cell.grid(row=i // 2, column=i % 2, sticky="w",
                      padx=SP.xxs, pady=SP.sm)
            cb = W.Checkbox(cell, p, events.KIND_LABELS.get(kind, kind),
                            bool(self.cfg["triggers"].get(kind)), bg=p.bg_app,
                            font_size=TS["body"],
                            command=lambda v: self._mark_dirty())
            cb.pack(anchor="w")
            tk.Label(cell, text=KIND_DESC.get(kind, ""), bg=p.bg_app,
                     fg=p.text_tertiary,
                     font=theme.font(self.root, TS["micro"])).pack(
                anchor="w", padx=SP.xxl)
            self.tr_vars[kind] = cb

        sec2 = W.Section(inner, p, "过滤与限流",
                         "用这些规则压掉噪音，避免手机被刷屏", bg=p.bg_app)
        sec2.pack(fill="x", padx=SP.xl, pady=(SP.xs, 0))

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
        p = self.pal
        inner = self._scroll_area(parent)

        bar = tk.Frame(inner, bg=p.bg_app)
        bar.pack(fill="x", padx=SP.xl, pady=(SP.xs, SP.lg))
        opts = [(k, events.KIND_LABELS.get(k, k))
                for k in ("task_done", "task_failed", "need_input",
                          "tool_failed", "test")]
        W.Segmented(bar, p, opts, self.tpl_kind, command=self._on_tpl_kind,
                    bg=p.bg_app, height=DIM.SEG_H,
                    font_size=TS["micro"]).pack(side="left")

        cols = tk.Frame(inner, bg=p.bg_app)
        cols.pack(fill="both", expand=True, padx=SP.xl, pady=(0, SP.lg))
        cols.columnconfigure(0, weight=1)
        cols.columnconfigure(1, weight=0)

        ed = tk.Frame(cols, bg=p.bg_app)
        ed.grid(row=0, column=0, sticky="nsew")

        s1 = W.Section(ed, p, "标题与正文",
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

        s2 = W.Section(ed, p, "可用占位符",
                       "发送前会被替换成真实内容", bg=p.bg_app, gap=SP.xs)
        s2.pack(fill="x", pady=(SP.sm, 0))
        chips = tk.Frame(s2.body, bg=p.bg_app)
        chips.pack(fill="x")
        for i, (token, desc) in enumerate(PLACEHOLDERS):
            chip = tk.Frame(chips, bg=p.bg_subtle)
            chip.grid(row=i // 3, column=i % 3, sticky="w",
                      padx=(0, SP.sm), pady=SP.xs)
            tk.Label(chip, text=token, bg=p.bg_subtle, fg=p.brand_ink,
                     font=theme.font(self.root, TS["micro"], mono=True)).pack(
                side="left", padx=(SP.sm, SP.xs), pady=SP.xs)
            tk.Label(chip, text=desc, bg=p.bg_subtle, fg=p.text_tertiary,
                     font=theme.font(self.root, TS["micro"])).pack(
                side="left", padx=(0, SP.sm), pady=SP.xs)

        prev = tk.Frame(cols, bg=p.bg_app)
        prev.grid(row=0, column=1, sticky="n", padx=(SP.xl, 0))
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

        snapshot = copy.deepcopy(self.cfg)
        snapshot["titles"][self.tpl_kind] = self.title_field.get()
        snapshot["templates"][self.tpl_kind] = self.content_field.get()
        try:
            title, content, _ = render.render_message(
                SAMPLE_PAYLOAD, self.tpl_kind, snapshot,
                snapshot.get("app_name", "WorkBuddy"))
        except Exception:
            title, content = "（模板有误）", ""
        # 微信模板消息是纯文本，发出去前会去掉 markdown 记号，预览也要一致
        content = channels.plain_text("", content)

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
                       font=theme.font(self.root, TS["micro"], "bold"))
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
        emoji, rest = _split_emoji(title)
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
                           font=theme.font(self.root, TS["micro"]))
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
                       fill=sub, font=theme.font(self.root, TS["micro"]))
        for i in range(3):
            cx = W_ - S(40) + i * S(8)
            cv.create_oval(cx, iy + S(11), cx + S(6), iy + S(17),
                           fill=sub, outline=sub)

    # ------------------------------------------------------------ 页：运行状态

    def _page_status(self, parent) -> None:
        p = self.pal
        inner = self._scroll_area(parent)
        stats = self._stats()

        # -- 指标：数字 + 标签，竖线分隔。不做卡片。
        mrow = tk.Frame(inner, bg=p.bg_app)
        mrow.pack(fill="x", padx=SP.xl, pady=(SP.sm, SP.xl))
        metrics = [
            ("今日通知", str(stats["today"]), p.text),
            ("送达成功", str(stats["ok"]), p.success),
            ("发送失败", str(stats["fail"]),
             p.danger if stats["fail"] else p.text_tertiary),
            ("启用通道", f'{stats["channels"]}/{len(channels.CHANNEL_META)}',
             p.brand_ink),
        ]
        for i, (label, value, color) in enumerate(metrics):
            if i:
                tk.Frame(mrow, bg=p.border, width=STK.rule).pack(
                    side="left", fill="y", padx=SP.xl)
            W.Metric(mrow, p, label, value, color, bg=p.bg_app).pack(side="left")

        # -- 通道状态：只列已启用的。
        # 把 10 条全列出来会把下面的「最近通知」挤到屏幕外，而且未启用的
        # 通道在这里没有信息量——它们不在这条链路上。
        sec = W.Section(inner, p, "通道状态",
                        "右侧的点表示这条出口现在通不通", bg=p.bg_app)
        sec.pack(fill="x", padx=SP.xl)
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
                p.bg_app, p.text_tertiary, size=TS["micro"],
            ).pack(fill="x", pady=(SP.sm, 0))

        # -- 最近通知：时间轴
        sec2 = W.Section(inner, p, "最近通知",
                         "来自 logs/notifications.log，最新的在上面。"
                         "红点表示这次是失败类事件", bg=p.bg_app)
        sec2.pack(fill="x", padx=SP.xl)
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

        # -- 运行日志
        sec3 = W.Section(inner, p, "运行日志",
                         "最近 12 行，排查问题用", bg=p.bg_app, gap=SP.sm)
        sec3.pack(fill="x", padx=SP.xl)
        box3 = tk.Frame(sec3.body, bg=p.bg_subtle)
        box3.pack(fill="x")
        W.Paragraph(box3, p, store.tail_log(12) or "（暂无）", p.bg_subtle,
                    p.text_secondary, size=TS["micro"], mono=True).pack(
            fill="x", padx=SP.md, pady=SP.sm)

    def _stats(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        sent, ok, fail = 0, 0, 0
        rows: list[dict] = []

        np = config.LOG_DIR / "notifications.log"
        if np.exists():
            try:
                for line in np.read_text(encoding="utf-8",
                                         errors="replace").splitlines():
                    m = re.match(
                        r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) \[([^\]]+)\] (\S+) \| (.*)$",
                        line)
                    if not m:
                        continue
                    if m.group(1) == today:
                        sent += 1
                    kind = m.group(4)
                    # 日志行格式是「时间 [宿主] 事件类型 | 标题」。
                    # 标题本身已经渲染了宿主名和类型，再拼一遍就是重复信息，
                    # 所以这一行只显示时间 + 结果点 + 标题。
                    rows.append({
                        "time": m.group(2),
                        "channel": "",
                        "summary": m.group(5)[:52],
                        "ok": kind not in ("task_failed", "tool_failed"),
                    })
            except Exception:
                pass

        lp = config.LOG_DIR / f"notify-{today}.log"
        if lp.exists():
            try:
                for line in lp.read_text(encoding="utf-8",
                                         errors="replace").splitlines():
                    if "ok=True" in line:
                        ok += 1
                    elif "ok=False" in line:
                        fail += 1
            except Exception:
                pass

        return {
            "today": sent, "ok": ok, "fail": fail,
            "channels": len(config.enabled_channels(self.cfg)),
            "recent": list(reversed(rows[-8:])),
        }

    # ------------------------------------------------------------ 页：使用教程

    def _page_guide(self, parent) -> None:
        p = self.pal
        inner = self._scroll_area(parent)

        # -- 开场：一句话说清目标与代价。不用渐变横幅，用排版。
        head = tk.Frame(inner, bg=p.bg_app)
        head.pack(fill="x", padx=SP.xl, pady=(SP.sm, SP.xl))
        tk.Label(head, text="从零到收到第一条通知", bg=p.bg_app, fg=p.text,
                 font=theme.font(self.root, TS["display"], "bold")).pack(anchor="w")
        # 拆成两句而不是一整段：一句是承诺，一句是推荐路线的理由。
        # 一整段时长句会在中间的空格处折得很丑。
        W.Paragraph(
            head, p, "四步走完，全程免费，第一次配置大约三分钟。",
            p.bg_app, p.text, size=TS["body"], spacing=SP.xxs,
        ).pack(fill="x", pady=(SP.sm, 0))
        W.Paragraph(
            head, p,
            "推荐路线是微信测试号：微信官方的开发者测试号，扫码即得凭证，"
            "不用实名、不用付费、不限条数。",
            p.bg_app, p.text_secondary, size=TS["small"], spacing=SP.xxs,
        ).pack(fill="x", pady=(SP.xs, 0))

        # -- 四步
        sec = W.Section(inner, p, "四步走通", bg=p.bg_app)
        sec.pack(fill="x", padx=SP.xl)
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
        sec2 = W.Section(
            inner, p, "手机弹窗怎么开",
            "通道配好只保证「消息发出去了」，能不能弹出来由系统通知权限决定",
            bg=p.bg_app)
        sec2.pack(fill="x", padx=SP.xl)
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
        sec3 = W.Section(
            inner, p, "电脑上也要弹窗",
            "本程序本身不弹窗（宿主已有自己的提示）。想让 Windows 也弹，"
            "配一个带桌面客户端的通道即可。", bg=p.bg_app)
        sec3.pack(fill="x", padx=SP.xl)
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
        sec4 = W.Section(inner, p, "收不到？按这个顺序查", bg=p.bg_app, gap=SP.sm)
        sec4.pack(fill="x", padx=SP.xl)
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
                            font=theme.font(self.root, TS["micro"], "bold"))
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

        for key, fld in (getattr(self, "filt_vars", None) or {}).items():
            raw = fld.get().strip()
            if key in ("projects_include", "projects_exclude", "keywords_include"):
                cfg["filters"][key] = _split_list(raw)
            elif key == "quiet_hours":
                cfg["filters"][key] = raw
            else:
                default = config.DEFAULT_CONFIG["filters"].get(key, 0)
                cfg["filters"][key] = _to_int(raw, default)

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
        cfg = self._collect()
        # 保存时再按完整度维护一次：用户可能刚把凭证填齐
        turned_on, turned_off = channels.auto_enable(cfg)
        config.save(cfg)
        self.cfg = cfg
        self._sync_channel_rows()
        self._dirty = False
        self._paint_dirty()
        self._paint_chain()

        if turned_on:
            self.toast.show("已保存，自动启用 " + "、".join(turned_on), "ok", 3200)
        elif not config.enabled_channels(cfg):
            self.toast.show("已保存，但还没有启用任何通道", "warn", 3200)
        else:
            self.toast.show("配置已保存", "ok")

        # 表单里的字段可能被自动启用逻辑改动，重建一次详情页保持一致
        if self.current_page == "channels":
            self._build_channel_form(animate=False)

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
        try:
            if sys.platform == "win32":
                os.startfile(target)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as e:
            self.toast.show(f"打开失败：{e}", "error")

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


def main() -> None:
    theme.enable_dpi_awareness()
    theme.set_app_user_model_id()      # 必须在建窗口之前，否则任务栏认不到我们的图标
    config.save_default_if_missing()
    root = tk.Tk()
    # pythonw.exe 下 stderr 是 None，Tk 回调里的异常会彻底消失。
    # 装上钩子后至少能在 logs/notify-*.log 里查到。
    theme.install_tk_exception_hook(root)
    app = App(root)
    root.bind("<Control-s>", lambda e: app.on_save())
    root.bind("<Control-t>", lambda e: app.on_test())
    root.mainloop()


if __name__ == "__main__":
    main()
