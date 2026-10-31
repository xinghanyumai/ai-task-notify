"""降级偏好（W10 §2.2）。

这条记忆的代价不对称，所以值得单独钉住：

- **记错了**（本来是沙箱问题，却因为一次偶发失败记成「以后都关沙箱」）：
  用户从此永久失去 Chromium 沙箱，而且他不知道为什么。
- **没记住**：只是每次多等 10 秒。

所以判据是「**只有真的降级启动、而且 bridge 真的调通了**才写」，
其余一切情况（读不出来、文件坏了、调试环境变量）都退回 False。
"""

from __future__ import annotations

import json

import pytest

import app
from wbn import bridge, store

# ---------------------------------------------------------------- store 层


def test_default_is_false_when_file_missing():
    assert store.prefer_no_sandbox() is False


def test_roundtrip_writes_a_real_true(isolated_state):
    store.set_prefer_no_sandbox(True)

    path = isolated_state / "state" / "ui.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"prefer_no_sandbox": True}
    assert store.prefer_no_sandbox() is True


def test_reset_removes_the_key_instead_of_writing_false(isolated_state):
    """点「恢复默认」之后，文件里不许留 `false`。

    留一条 `prefer_no_sandbox: false` 以后谁也说不清是用户选的还是程序写的，
    下一个人读代码时会以为这个字段有意义。
    """
    store.set_prefer_no_sandbox(True)
    store.set_prefer_no_sandbox(False)

    path = isolated_state / "state" / "ui.json"
    assert "prefer_no_sandbox" not in json.loads(path.read_text(encoding="utf-8"))
    assert store.prefer_no_sandbox() is False


@pytest.mark.parametrize("junk", ["{坏 JSON", "[]", '"yes"', "null", "123"])
def test_broken_file_falls_back_to_false(isolated_state, junk):
    """文件坏了 → 当没记过。**不许抛**，也不许当成 True。

    读不出来就当 True 是最坏的一种：一个写坏的 JSON 会让这台机器
    从此永远关着沙箱。
    """
    path = isolated_state / "state" / "ui.json"
    path.write_text(junk, encoding="utf-8")

    assert store.prefer_no_sandbox() is False


def test_only_the_exact_boolean_true_counts(isolated_state):
    """`"true"` / `1` 都不算。宁可多等一次，也不认一个含糊的值。"""
    path = isolated_state / "state" / "ui.json"
    for value in ("true", 1, "1", [1]):
        path.write_text(json.dumps({"prefer_no_sandbox": value}), encoding="utf-8")
        assert store.prefer_no_sandbox() is False, value


# ---------------------------------------------------------------- app 层


def test_sandbox_env_wins_over_the_memory(monkeypatch):
    """`WBN_WEBVIEW_SANDBOX=1` 是排查用的强制带沙箱，必须压过记忆。"""
    monkeypatch.setattr(store, "prefer_no_sandbox", lambda: True)
    monkeypatch.setenv("WBN_WEBVIEW_SANDBOX", "1")

    assert app._remembered_no_sandbox() is False


def test_memory_is_used_when_no_env_override(monkeypatch):
    monkeypatch.delenv("WBN_WEBVIEW_SANDBOX", raising=False)
    monkeypatch.setattr(store, "prefer_no_sandbox", lambda: True)

    assert app._remembered_no_sandbox() is True


def test_memory_read_failure_is_swallowed(monkeypatch):
    """store 炸了（权限 / 盘符没了）→ 当没记过，不许把启动流程带崩。"""
    monkeypatch.delenv("WBN_WEBVIEW_SANDBOX", raising=False)

    def _boom():
        raise OSError("disk gone")

    monkeypatch.setattr(store, "prefer_no_sandbox", _boom)

    assert app._remembered_no_sandbox() is False


def test_remember_only_after_a_real_no_sandbox_launch(monkeypatch):
    """普通启动（没降级）**不许**写这个标记。

    这是最关键的一条：`_remember_no_sandbox()` 挂在「bridge 调通」上，
    而 bridge 调通在正常启动里也会发生。少一个环境判断，所有正常机器
    都会在第一次开窗后就被记成「要关沙箱」。
    """
    monkeypatch.delenv("WBN_WEBVIEW_NOSANDBOX", raising=False)

    app._remember_no_sandbox()

    assert store.prefer_no_sandbox() is False


def test_remember_after_downgraded_launch(monkeypatch):
    monkeypatch.setenv("WBN_WEBVIEW_NOSANDBOX", "1")

    app._remember_no_sandbox()

    assert store.prefer_no_sandbox() is True


def test_remember_is_idempotent(monkeypatch, isolated_state):
    """已经记过就不再写盘——`_first_activity` 可能被调多次。"""
    monkeypatch.setenv("WBN_WEBVIEW_NOSANDBOX", "1")
    app._remember_no_sandbox()
    path = isolated_state / "state" / "ui.json"
    mtime = path.stat().st_mtime_ns

    app._remember_no_sandbox()

    assert path.stat().st_mtime_ns == mtime


# ---------------------------------------------------------------- bridge


def test_bridge_get_and_reset():
    b = bridge.Bridge()
    store.set_prefer_no_sandbox(True)

    assert b.sandbox_pref_get()["data"] == {"prefer_no_sandbox": True}
    assert b.sandbox_pref_reset()["data"] == {"prefer_no_sandbox": False}
    assert b.sandbox_pref_get()["data"] == {"prefer_no_sandbox": False}
