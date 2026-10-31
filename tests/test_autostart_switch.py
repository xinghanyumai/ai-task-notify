"""自启开关的行为守卫（W9 遗留 P1）。

W9 验收时两个变异**都 570 全绿**：把 `set_autostart` 里 `if not on:` 分支的首行
直接 `return True, "ok"`（关掉但文件还在）、把 `bridge.autostart_set` 的「重读」
换成回显入参。原因是 `autostart_get` / `autostart_set` 在 `tests/` 里只出现在
`test_bridge.py` 的方法名白名单里——有名字，零行为断言。

这三条补的就是那个洞。

**所有用例都用 `tmp_path` 造一个假启动目录**，打桩 `install._startup_dir`。
绝不碰真实的 `%APPDATA%\\...\\Startup`——碰了就是把开发者自己的开机自启改掉，
而且是静默改。
"""

from __future__ import annotations

import sys

import pytest

import install
from wbn import bridge

# 启动文件夹是 Windows 概念；非 Windows 上 `_autostart_probe()` 直接返回
# `(None, "非 Windows 系统…")`，这三条断言没法成立。CI 两个平台都跑。
pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="启动文件夹是 Windows 概念"
)


@pytest.fixture
def startup_dir(tmp_path, monkeypatch):
    """假启动目录。`_startup_dir` 打桩成它，真实目录一次都不会被读。"""
    d = tmp_path / "Startup"
    d.mkdir()
    monkeypatch.setattr(install, "_startup_dir", lambda: d, raising=True)
    return d


def test_off_really_removes_the_shortcut(startup_dir):
    """关自启 = `.lnk` 真的从磁盘上消失。

    这条打死「报成功但没删」——W9 的 M1 变异就是在 `if not on:` 首行插一句
    `return True, "ok"`，界面显示已关闭、文件还在、下次开机照样自启。
    """
    lnk = startup_dir / install.AUTOSTART_NAME
    lnk.write_bytes(b"pretend this is a shortcut")
    assert lnk.exists(), "前置条件：文件得先在那儿"

    ok, msg = install.set_autostart(False)

    assert ok is True, msg
    assert not lnk.exists(), "关自启必须真的删掉 .lnk，不能只返回成功"


def test_off_when_already_off_is_not_an_error(startup_dir):
    """文件本来就不在 → 算成功，不抛异常。

    用户点「关」时它可能早被手动删了。这时报错只会让人以为程序坏了，
    然后去反复点——反而点出别的问题。
    """
    assert list(startup_dir.iterdir()) == []

    ok, msg = install.set_autostart(False)

    assert ok is True, msg
    assert msg, "得给一句人话，不能返回空说明"


def test_set_never_echoes_the_argument(startup_dir, isolated_state, monkeypatch):
    """点了「开」但快捷方式没建成 → 界面必须显示**关着**。

    打死「回显入参」——W9 的 M2 变异把重读换成 `{"enabled": bool(on), ...}`，
    于是建失败也报「已开启」，用户以为好了，下次开机没起来也查不出原因。
    """
    monkeypatch.setattr(install, "make_shortcut", lambda *a, **k: False, raising=True)

    res = bridge.Bridge().autostart_set(True)

    assert res["ok"] is True, res          # 调用本身没炸
    data = res["data"]
    assert data["ok"] is False, "建快捷方式失败，ok 不能是 True"
    assert data["enabled"] is False, "磁盘上没建成，enabled 必须是 False，不许回显入参"
    assert not (startup_dir / install.AUTOSTART_NAME).exists()
