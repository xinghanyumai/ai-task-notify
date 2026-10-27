"""审查脚本：可访问性 / 交互 / 动画 / 响应式。

不是单元测试，是「用程序把这几条人工很难穷举的东西验一遍」：
  A 可访问性 —— 对比度、键盘可达、焦点环是否真的画出来
  B 交互     —— 渐进披露、主按钮唯一性、空状态
  C 动画     —— 减弱动效开关是否真的短路、补间是否可被接管
  D 响应式   —— 最窄 / 默认 / 更宽三档下有无溢出

用法：python tools/audit.py
"""
from __future__ import annotations

import inspect
import sys
import tkinter as tk
from pathlib import Path

# 脚本住在 tools/，但 wbn 包在上一级。sys.path[0] 是脚本所在目录，
# 所以要把项目根塞进去；用 __file__ 而不是 "."，否则只有在根目录下
# 执行时才碰巧能 import 到。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wbn import anim, theme  # noqa: E402
from wbn import widgets as W  # noqa: E402

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


# ---------------------------------------------------------------- 控件树遍历
#
# 这三个遍历器刻意放在模块层，而不是写成循环里的嵌套函数：
# 嵌套函数会闭包捕获循环变量（B023），虽然当前都是「定义完立刻调用」不会出错，
# 但只要有人把调用挪到循环外，捕获的就全是最后一轮的容器了。


def _visible(child) -> bool:
    """这个控件当前是不是真的显示着。

    页面缓存之后，五个页面同时是 `app.content` 的子控件，只有当前页被
    pack 出来。审查必须只看可见的那一份——否则「一页只有一个主按钮」
    会被另外四页的按钮一起打破，溢出检查也会量到没显示的页面。
    跳过未映射的子树即可：父不显示时子也不会显示。
    """
    try:
        return bool(child.winfo_ismapped())
    except Exception:
        return False


def _collect_focusable(widget, out: list) -> None:
    """收集能用 Tab 到达的控件。"""
    for child in widget.winfo_children():
        if not _visible(child):
            continue
        try:
            tf = str(child.cget("takefocus") or "")
            if isinstance(child, (tk.Entry, tk.Text)) or tf not in ("", "0"):
                out.append(child)
        except Exception:
            pass
        _collect_focusable(child, out)


def _collect_primary_buttons(widget, out: list) -> None:
    """收集 variant=primary 的按钮文案（一页只该有一个主按钮）。"""
    for child in widget.winfo_children():
        if not _visible(child):
            continue
        if isinstance(child, W.Button) and getattr(child, "variant", "") == "primary":
            out.append(getattr(child, "text", ""))
        _collect_primary_buttons(child, out)


def _collect_overflow(container, out: list) -> None:
    """收集比父容器还宽的子控件（横向溢出）。"""
    cw = container.winfo_width()
    for child in container.winfo_children():
        if not _visible(child):
            continue
        try:
            if child.winfo_width() > cw + theme.S(2) and cw > 1:
                out.append(f"{child.__class__.__name__}({child.winfo_width()}>{cw})")
        except Exception:
            pass
        _collect_overflow(child, out)


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
           "不达标：" + "，".join(f"{lbl}={c}" for lbl, c in bad))

    # 品牌填充色绝不能被当成文字色用
    c = theme.contrast(theme.LIGHT.brand, theme.LIGHT.bg_app)
    warn(c < 4.5, f"品牌填充色在白底只有 {c:.2f}:1，已拆分出 brand_ink 专供文字")


def audit_keyboard(app) -> None:
    print("\n[A2] 键盘可达（Tab 能到达多少个可操作控件）")
    for key in ("channels", "triggers", "template", "status", "guide"):
        app._show_page(key)
        app.root.update_idletasks()
        focusable: list = []
        _collect_focusable(app.content, focusable)
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
        prim: list = []
        _collect_primary_buttons(app.root, prim)
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
    # 真正的契约是 EmptyState 必须能接受一个 (文案, 回调) 的出口动作。
    # 原来的 hasattr(W, "EmptyState") 是永真断言 —— 只要类还在就通过，
    # 测不到标题承诺的任何东西。
    params = inspect.signature(W.EmptyState.__init__).parameters
    ok("action" in params, "EmptyState 接受 action 出口动作",
       "构造函数里没有 action 参数，空状态会变成死胡同")


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
        bad: list = []

        for page in ("channels", "triggers", "template", "status", "guide"):
            app._show_page(page)
            app.root.update_idletasks()
            # 必须 update() 而不是只 update_idletasks()：卡片钳制宽度靠的是
            # <Configure> 回调，而 <Configure> 走事件队列，只有 update() 才派发。
            # 只跑空闲任务量出来的是「事件还没送达」的中间态，不是用户看到的样子。
            app.root.update()
            _collect_overflow(app.content, bad)
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
    # Tk 回调里的异常默认只写 stderr，审查脚本就看不见。
    # 装钩子把它们计入失败项 —— 否则「界面少画了点东西」这类问题永远是绿的。
    theme.install_tk_exception_hook(
        root, on_error=lambda text: FAIL.append(f"Tk 回调异常：{text.splitlines()[-1]}")
    )
    app = ui.App(root)
    root.update_idletasks()
    # 两次 update() 是必须的，不是保险：update_idletasks() 只跑空闲任务，
    # 不派发 <Map>/<Configure>。不 update 的话窗口还没被映射，
    # winfo_ismapped() 全是假，可见性过滤会把整页都判成「没显示」，
    # A2 于是报「channels 页 0 个可聚焦控件」——那是审查脚本的错，不是界面的。
    root.update()
    root.update()
    # 关掉启动后的页面预热：它会在审查过程中途建页，把每项计数搅成不确定值。
    if getattr(app, "_warm_job", None) is not None:
        try:
            root.after_cancel(app._warm_job)
            app._warm_job = None
        except Exception:
            pass
    root.update()

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
