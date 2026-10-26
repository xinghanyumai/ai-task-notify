# -*- coding: utf-8 -*-
"""审查脚本：可访问性 / 交互 / 动画 / 响应式。

不是单元测试，是「用程序把这几条人工很难穷举的东西验一遍」：
  A 可访问性 —— 对比度、键盘可达、焦点环是否真的画出来
  B 交互     —— 渐进披露、主按钮唯一性、空状态
  C 动画     —— 减弱动效开关是否真的短路、补间是否可被接管
  D 响应式   —— 最窄 / 默认 / 更宽三档下有无溢出

用法：python audit.py
"""
from __future__ import annotations

import sys
import tkinter as tk

sys.path.insert(0, ".")

from wbn import anim, channels, theme          # noqa: E402
from wbn import widgets as W                   # noqa: E402

FAIL: list[str] = []
WARN: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"  \u2713 {label}")
    else:
        print(f"  \u2717 {label}  {detail}")
        FAIL.append(label)


def warn(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"  \u2713 {label}")
    else:
        print(f"  ! {label}  {detail}")
        WARN.append(label)


# ---------------------------------------------------------------- A 可访问性

def audit_contrast() -> None:
    print("\n[A1] 文字与背景对比度（WCAG AA 正文需 >= 4.5，非文本需 >= 3）")
    for name in ("LIGHT", "DARK"):
        p = getattr(theme, name)
        pairs = [
            ("主文字 / 页面底", p.text, p.bg_app, 4.5),
            ("次级文字 / 页面底", p.text_secondary, p.bg_app, 4.5),
            ("三级文字 / 页面底", p.text_tertiary, p.bg_app, 4.5),
            ("主文字 / 卡片", p.text, p.bg_card, 4.5),
            ("三级文字 / 卡片", p.text_tertiary, p.bg_card, 4.5),
            ("品牌文字 / 页面底", p.brand_ink, p.bg_app, 4.5),
            ("品牌文字 / 品牌浅底", p.brand_ink, p.brand_soft, 4.5),
            ("危险 / 页面底", p.danger, p.bg_app, 4.5),
            ("警告 / 警告浅底", p.warning, p.warning_soft, 4.5),
            ("成功 / 成功浅底", p.success, p.success_soft, 4.5),
            ("按钮字 / 品牌填充", p.on_brand, p.brand, 4.5),
            ("控件边界 / 页面底", p.border_control, p.bg_app, 3.0),
            ("焦点环 / 页面底", p.border_focus, p.bg_app, 3.0),
        ]
        bad = [(lbl, round(theme.contrast(a, b), 2))
               for lbl, a, b, need in pairs
               if theme.contrast(a, b) < need]
        ok(not bad, f"{name} 全部 {len(pairs)} 项达标",
           "不达标：" + "，".join(f"{l}={c}" for l, c in bad))

    # 品牌填充色绝不能被当成文字色用
    c = theme.contrast(theme.LIGHT.brand, theme.LIGHT.bg_app)
    warn(c < 4.5, f"品牌填充色在白底只有 {c:.2f}:1，已拆分出 brand_ink 专供文字")


def audit_keyboard(app) -> None:
    print("\n[A2] 键盘可达（Tab 能到达多少个可操作控件）")
    for key in ("channels", "triggers", "template", "status", "guide"):
        app._show_page(key)
        app.root.update_idletasks()
        focusable = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    tf = str(c.cget("takefocus") or "")
                    if isinstance(c, (tk.Entry, tk.Text)) or tf not in ("", "0"):
                        focusable.append(c)
                except Exception:
                    pass
                walk(c)

        walk(app.content)
        # status / guide 是只读页，没有可操作控件是可接受的
        need = 0 if key in ("status", "guide") else 3
        warn(len(focusable) >= need,
             f"{key} 页有 {len(focusable)} 个可聚焦控件"
             + ("（只读页，不要求）" if need == 0 else ""),
             "自绘控件全部不可聚焦，键盘用户无法操作")

    # Tab 真的能走一圈，并且焦点环被画出来
    app._show_page("channels")
    app.root.update_idletasks()
    app.root.focus_force()
    seen = 0
    for _ in range(12):
        try:
            app.root.tk.call("tk_focusNext", app.root.focus_get() or app.root)
        except Exception:
            pass
        app.root.event_generate("<Tab>")
        app.root.update()
        f = app.root.focus_get()
        if f is not None and f is not app.root:
            seen += 1
    ok(seen >= 3, f"Tab 连续 12 次，焦点落到 {seen} 个控件上")

    rings = 0
    def count(w):
        nonlocal rings
        for c in w.winfo_children():
            if isinstance(c, tk.Canvas):
                try:
                    if c.find_withtag("focusring"):
                        rings += 1
                except Exception:
                    pass
            count(c)
    count(app.root)
    ok(rings >= 1, f"焦点环已绘制（{rings} 处带 focusring 标记）")


# ---------------------------------------------------------------- B 交互

def audit_interaction(app) -> None:
    print("\n[B1] 主按钮唯一性（primary 变体全局只应有一个）")
    for key in ("channels", "triggers", "template", "status", "guide"):
        app._show_page(key)
        app.root.update_idletasks()
        prim = []

        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, W.Button) and getattr(c, "variant", "") == "primary":
                    prim.append(getattr(c, "text", ""))
                walk(c)

        walk(app.root)
        warn(len(prim) <= 1, f"{key} 页 primary 按钮 {len(prim)} 个 {prim}")

    print("\n[B2] 渐进披露（长说明默认收起）")
    app._show_page("channels")
    app.root.update_idletasks()
    cols = []

    def walk(w):
        for c in w.winfo_children():
            if isinstance(c, W.Collapsible):
                cols.append(c)
            walk(c)

    walk(app.root)
    ok(len(cols) >= 1, f"通道详情有 {len(cols)} 个可折叠帮助块")

    print("\n[B3] 空状态有出口动作")
    ok(hasattr(W, "EmptyState"), "EmptyState 组件存在")


# ---------------------------------------------------------------- C 动画

def audit_motion(app) -> None:
    print("\n[C1] 减弱动效开关真的短路补间")
    calls = []

    class Dummy(tk.Canvas):
        pass

    d = Dummy(app.root, width=10, height=10)
    anim.set_reduced(True)
    anim.tween(d, "t", 300, lambda t: calls.append(t), interval=13)
    ok(len(calls) == 1 and calls[0] >= 0.999,
       f"开启后只画终点一帧（帧数={len(calls)}, 进度={calls})")

    calls.clear()
    anim.set_reduced(False)
    anim.tween(d, "t2", 60, lambda t: calls.append(t), interval=13)
    app.root.update()
    app.root.after(120, app.root.quit)
    app.root.mainloop()
    ok(len(calls) > 2, f"关闭后逐帧播放（帧数={len(calls)}）")
    d.destroy()

    print("\n[C2] 同一 (控件, 键) 只跑一个补间")
    got = []
    anim.tween(app.root, "k", 200, lambda t: got.append(("a", t)))
    anim.tween(app.root, "k", 200, lambda t: got.append(("b", t)))
    app.root.after(260, app.root.quit)
    app.root.mainloop()
    old_frames = sum(1 for t, _ in got if t == "a")
    new_frames = sum(1 for t, _ in got if t == "b")
    ok(old_frames <= 1 and new_frames > 2,
       f"后一个补间接管（旧={old_frames} 帧，新={new_frames} 帧）")

    print("\n[C3] 系统减弱动效开关可读")
    ok(isinstance(anim.detect_reduced_motion(), bool), "detect_reduced_motion 返回布尔值")


# ---------------------------------------------------------------- D 响应式

def audit_responsive(app) -> None:
    print("\n[D1] 三档窗口宽度下是否出现横向溢出")
    for w in (940, 1080, 1360):
        app.root.geometry(f"{theme.S(w)}x{theme.S(700)}")
        app.root.update_idletasks()
        app.root.update()
        bad = []

        def walk(container):
            cw = container.winfo_width()
            for c in container.winfo_children():
                try:
                    if not c.winfo_ismapped():
                        continue
                    if c.winfo_width() > cw + theme.S(2) and cw > 1:
                        bad.append(f"{c.__class__.__name__}({c.winfo_width()}>{cw})")
                except Exception:
                    pass
                walk(c)

        for page in ("channels", "triggers", "template", "status", "guide"):
            app._show_page(page)
            app.root.update_idletasks()
            walk(app.content)
        warn(not bad, f"{w}px 宽：{len(bad)} 处子控件超出父容器",
             "，".join(bad[:4]))

    app.root.geometry(f"{theme.S(1080)}x{theme.S(760)}")
    app.root.update_idletasks()


# ---------------------------------------------------------------- 主流程

def main() -> None:
    theme.enable_dpi_awareness()
    theme.set_app_user_model_id()

    import ui

    root = tk.Tk()
    app = ui.App(root)
    root.update_idletasks()

    audit_contrast()
    audit_keyboard(app)
    audit_interaction(app)
    audit_motion(app)
    audit_responsive(app)

    print("\n" + "=" * 60)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for f in FAIL:
            print("  -", f)
    if WARN:
        print(f"提示 {len(WARN)} 项（不一定算错，需要人判断）：")
        for w in WARN:
            print("  -", w)
    if not FAIL and not WARN:
        print("全部通过")
    root.destroy()


if __name__ == "__main__":
    main()
