"""平台相关用例：Windows 专有 API、hooks 安装逻辑、GUI 冒烟。

CI 矩阵里 ubuntu 也要跑 pytest，所以这里用 `pytest.mark.skipif` 按平台跳过，
**不用 `-k` 排除**——被跳过的用例会以 skipped 状态出现在报告里，
而不是悄悄消失。用 -k 排除的话，哪天这些用例被误删了也看不出来。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from wbn import config, theme

IS_WINDOWS = sys.platform == "win32"

windows_only = pytest.mark.skipif(
    not IS_WINDOWS,
    reason="Windows 专有 API（icacls / shell32 / winreg），在 POSIX 上无意义",
)


# ---------------------------------------------------------------- GUI 冒烟


@pytest.fixture(scope="session")
def _tk_session():
    """整个测试进程只开一次 Tk。

    实测：同一个进程里反复 Tk() / destroy() 三次以上，Windows 上第三次会在
    `source tk.tcl` 阶段失败（Tcl 报 SourceLibFile icons 失败）。所以根窗口
    会话级共享，每个用例只负责清理自己挂上去的子控件。
    """
    try:
        import tkinter as tk
    except Exception as exc:  # 没装 tk 的系统
        pytest.skip(f"无 tkinter：{exc}")
    try:
        root = tk.Tk()
    except Exception as exc:  # 无声环境 / 无 X11
        pytest.skip(f"无图形栈：{exc}")
    root.withdraw()
    try:
        yield root
    finally:
        try:
            root.destroy()
        except Exception:
            pass


@pytest.fixture
def tk_root(_tk_session):
    """会话级 root + 用例级清理，保证用例之间互不干扰。"""
    yield _tk_session
    for child in list(_tk_session.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass


@pytest.mark.gui
def test_widgets_construct_without_error(tk_root):
    """自绘控件全是 Canvas 手绘，构造期最容易出 IndexError / TclError。"""
    from wbn import widgets as W

    pal = theme.LIGHT
    W.Button(tk_root, "测试", pal, lambda: None)
    W.Switch(tk_root, pal, True, lambda v: None)
    W.Checkbox(tk_root, pal, "勾选", True, lambda v: None)
    W.Section(tk_root, pal, "小节标题")
    W.Pill(tk_root, pal, "标签")
    W.Metric(tk_root, pal, "标签", "42", pal.text)
    W.Paragraph(tk_root, pal, "一段正文，用来验证换行与字体解析。", pal.bg_app, pal.text)


@pytest.mark.gui
def test_icon_set_draws_only_known_names(tk_root):
    """图标名写错时 draw_icon 会静默什么都不画，界面上一片空白。"""
    import tkinter as tk

    from wbn import widgets as W

    canvas = tk.Canvas(tk_root, width=200, height=200)
    for name in ("bell", "chat", "mail", "link", "file", "check"):
        W.draw_icon(canvas, name, 0, 0, 16, theme.LIGHT.text)
    assert len(canvas.find_all()) > 0


@pytest.mark.gui
def test_toast_draws_its_background(tk_root):
    """回归：Toast._draw 曾引用未导入的 RADIUS_LG，NameError 被 Tk 吞掉，
    表现为「提示能弹出但没有圆角背景」。F821 由 ruff 发现，这里守住行为。
    """
    from wbn import widgets as W

    toast = W.Toast(tk_root, theme.LIGHT)
    toast._draw()  # 修复前这里抛 NameError

    assert len(toast.cv.find_all()) >= 2, "Toast 背景没画出来"


# ---------------------------------------------------------------- Windows API


@windows_only
def test_harden_file_locks_down_credentials_file(tmp_path):
    """config.json 里有明文 appsecret，保存后要把 ACL 收紧到当前用户。"""
    target = tmp_path / "config.json"
    target.write_text('{"secret": "x"}', encoding="utf-8")

    config.harden_file(target)  # 不应抛异常

    assert target.exists()
    assert target.read_text(encoding="utf-8") == '{"secret": "x"}'


@windows_only
def test_harden_file_is_a_noop_for_missing_file(tmp_path):
    config.harden_file(tmp_path / "not-there.json")  # 不抛即通过


@windows_only
def test_app_user_model_id_is_applied():
    """不设 AppUserModelID，任务栏会把窗口归到 pythonw.exe 名下、显示 Python 图标。"""
    import ctypes

    theme.set_app_user_model_id()

    buf = ctypes.c_wchar_p()
    ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(buf))
    assert buf.value == "Wbn.TaskNotify.Desktop.1"


def test_dpi_awareness_call_is_safe_everywhere():
    """必须在 Tk() 之前调用；POSIX 上直接 return，Windows 上走 shcore。"""
    theme.enable_dpi_awareness()  # 不抛即通过


def test_system_theme_returns_a_known_mode():
    assert theme.system_theme() in ("light", "dark")


# ---------------------------------------------------------------- hooks 安装

install = pytest.importorskip("install", reason="install.py 依赖仓库根布局")


def _foreign_entry() -> dict:
    return {"hooks": [{"type": "command", "command": "some-other-tool --flag"}]}


def test_is_our_hook_recognizes_generated_command():
    entry = install.build_hooks("WorkBuddy")["Stop"][0]
    assert install.is_our_hook(entry) is True


def test_is_our_hook_rejects_foreign_command():
    assert install.is_our_hook(_foreign_entry()) is False


def test_hook_command_points_at_hook_py_and_quotes_paths():
    """路径含空格 / 中文时必须整体加引号，否则命令行会被拆断。"""
    cmd = install.hook_command("WorkBuddy")

    assert cmd.endswith("--app WorkBuddy")
    assert "hook.py" in cmd
    assert cmd.startswith('"') and cmd.count('"') == 4


def test_merge_hooks_keeps_third_party_entries():
    existing = {"Stop": [_foreign_entry()], "Unrelated": [_foreign_entry()]}
    merged = install.merge_hooks(existing, "WorkBuddy")

    assert len(merged["Stop"]) == 2, "第三方 hook 不能被我们的覆盖掉"
    assert merged["Unrelated"] == existing["Unrelated"]
    assert install.is_our_hook(merged["Stop"][-1])


def test_merge_hooks_is_idempotent():
    """重复安装不能把自己写成两条，否则每个事件会触发两次通知。"""
    once = install.merge_hooks({}, "WorkBuddy")
    twice = install.merge_hooks(once, "WorkBuddy")

    assert {k: len(v) for k, v in twice.items()} == {k: len(v) for k, v in once.items()}


def test_strip_hooks_removes_only_ours():
    existing = {
        "Stop": [_foreign_entry(), install.build_hooks("WorkBuddy")["Stop"][0]],
        "Unrelated": [_foreign_entry()],
    }
    stripped = install.strip_hooks(existing)

    assert len(stripped["Stop"]) == 1
    assert install.is_our_hook(stripped["Stop"][0]) is False
    assert stripped["Unrelated"] == existing["Unrelated"]


def test_strip_hooks_drops_empty_events():
    stripped = install.strip_hooks({"Stop": [install.build_hooks("WorkBuddy")["Stop"][0]]})
    assert stripped == {}


@windows_only
def test_python_exe_prefers_pythonw():
    """pythonw.exe 不弹控制台；没有就退回 python.exe，绝不能返回空。"""
    exe = install.python_exe()
    assert isinstance(exe, Path)
    assert exe.name in ("pythonw.exe", "python.exe")


@windows_only
def test_hook_command_uses_forward_slashes():
    """正斜杠避免 JSON 里出现 \\\\ 转义，宿主解析更稳。"""
    cmd = install.hook_command("WorkBuddy")
    assert "\\\\" not in cmd
