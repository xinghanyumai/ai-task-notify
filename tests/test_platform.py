"""平台相关用例：Windows 专有 API、hooks 安装逻辑。

CI 矩阵里 ubuntu 也要跑 pytest，所以这里用 `pytest.mark.skipif` 按平台跳过，
**不用 `-k` 排除**——被跳过的用例会以 skipped 状态出现在报告里，
而不是悄悄消失。用 -k 排除的话，哪天这些用例被误删了也看不出来。

W5 删掉了 4 条：`test_widgets_construct_without_error` /
`test_icon_set_draws_only_known_names` / `test_toast_draws_its_background`
（随 `wbn/widgets.py` 一起下线，控件改成 HTML/CSS 组件）、
`test_dpi_awareness_call_is_safe_everywhere`（`theme.enable_dpi_awareness` 随
`theme.py` 删除；DPI 感知改由 pywebview 自己设，验收方式是 200% DPI 下截图不糊，
不是单元测试）。`test_system_theme_returns_a_known_mode` 也删了：主题由 CSS
`prefers-color-scheme` 决定，Python 侧不再读注册表。

**`test_app_user_model_id_is_applied` 活下来了。** 它验的是「任务栏不显示 Python
图标」，跟用什么 UI 技术无关，web 版同样需要。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from wbn import config, winapi

IS_WINDOWS = sys.platform == "win32"

windows_only = pytest.mark.skipif(
    not IS_WINDOWS,
    reason="Windows 专有 API（icacls / shell32 / winreg），在 POSIX 上无意义",
)


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
    """不设 AppUserModelID，任务栏会把窗口归到 pythonw.exe 名下、显示 Python 图标。

    W5 把这个函数从 `wbn/theme.py` 搬到 `wbn/winapi.py`——`theme.py` 被删了，
    但**这个断言必须活下来**：它验的是任务栏行为，和 UI 技术栈无关。
    """
    winapi.set_app_user_model_id()

    assert winapi.read_app_user_model_id() == winapi.APP_USER_MODEL_ID


@windows_only
def test_second_acquire_of_same_mutex_reports_already_running():
    """托盘单实例锁的判据（验收第 7 条）。

    同一进程里第二次拿同名互斥量也会成功返回句柄，但 `GetLastError` 是
    `ERROR_ALREADY_EXISTS`——这正是「已有一个实例在跑」的信号。这条测试钉的是
    「我们读的是 last_error，不是句柄是否为空」——写反了的话单实例永远不生效，
    而且不会有任何报错。
    """
    name = "Local\\WBN_TEST_SINGLETON_LOCK"

    assert winapi.acquire_single_instance(name) is True
    assert winapi.acquire_single_instance(name) is False


@windows_only
def test_acquire_single_instance_fails_open_on_bad_name():
    """探测失败必须放行。宁可开两个窗口，也不能因为探测失败让人彻底打不开。"""
    # 空名字：CreateMutexW 会失败，函数应当返回 True（放行）而不是崩掉
    assert winapi.acquire_single_instance("") is True


def test_app_user_model_id_is_a_noop_on_posix():
    """POSIX 上直接 return，不 import 任何 Windows 专有模块。"""
    if IS_WINDOWS:
        pytest.skip("只在 POSIX 上验这条")
    winapi.set_app_user_model_id()          # 不抛即通过
    assert winapi.read_app_user_model_id() is None


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
