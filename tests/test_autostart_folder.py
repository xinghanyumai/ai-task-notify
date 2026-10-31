"""「打开启动文件夹」按钮（W10 §6）。

这个按钮的存在意义只有一个：**让用户自己确认那个 `.lnk` 在不在**。
所以它的失败姿态很重要——

- 读不到目录时**按钮仍然可点**（返回 `ok=False` + 人话），不许凭空消失。
  消失了用户永远不知道为什么打不开。
- 非 Windows 不许抛，返回 `ok=False`。`bridge` 的每个公开方法都可能被
  前端在任何系统上调到。
"""

from __future__ import annotations

import sys

import pytest

import install
from wbn import bridge


class _FakeInstall:
    def __init__(self, path):
        self._path = path

    def autostart_path(self):
        return self._path


def _call():
    return bridge.Bridge().autostart_open_folder()["data"]


def test_non_windows_returns_false_not_raise(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    got = _call()

    assert got["ok"] is False
    assert "非 Windows" in got["msg"]


def test_missing_folder_reports_the_path(monkeypatch, tmp_path):
    """目录不存在时要说清楚是哪个路径——「打不开」三个字没法排查。"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        bridge, "_install", lambda: _FakeInstall(tmp_path / "nope" / "Startup.lnk")
    )

    got = _call()

    assert got["ok"] is False
    assert str(tmp_path) in got["msg"]


def test_opens_the_folder_that_contains_the_shortcut(monkeypatch, tmp_path):
    """成功路径：`os.startfile` 收到的必须是**目录**，不是那个 .lnk。"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        bridge, "_install", lambda: _FakeInstall(tmp_path / install.AUTOSTART_NAME)
    )
    opened = []
    monkeypatch.setattr("os.startfile", opened.append, raising=False)

    got = _call()

    assert got["ok"] is True
    assert opened == [str(tmp_path)]


def test_startfile_failure_is_swallowed(monkeypatch, tmp_path):
    """资源管理器打不开（权限 / 关联被改）也不许把 bridge 带崩。"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        bridge, "_install", lambda: _FakeInstall(tmp_path / install.AUTOSTART_NAME)
    )

    def _boom(_):
        raise OSError("没有关联的应用程序")

    monkeypatch.setattr("os.startfile", _boom, raising=False)

    got = _call()

    assert got["ok"] is False
    assert "打不开" in got["msg"]


@pytest.mark.parametrize("name", ["autostart_open_folder", "config_health"])
def test_new_methods_are_on_the_public_api(name):
    """漏注册的话前端 `call()` 会拿到 AttributeError，按钮点了没反应。"""
    assert callable(getattr(bridge.Bridge, name, None))
    assert not name.startswith("_")
