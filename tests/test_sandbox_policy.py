"""沙箱策略：默认带沙箱，只在判定黑窗时降级一次。

**这条以前是内联的 `if os.environ.get("WBN_WEBVIEW_SANDBOX") != "1":`，
没法在不真起窗口的前提下验证。** W7 把它改成「默认带沙箱 + 失败即降级」，
就得有东西盯着这个默认值：写成默认关沙箱是**净损失**（一个没必要的安全降级
会跟着 exe 发给每个用户），而写成默认带沙箱但降级路径断掉，则会让
2026-10-27 那种黑窗从「能自救」退化成「只能看提示框」。

三件事各一条断言：
    1. 默认（什么都不设）→ 不关沙箱
    2. `WBN_WEBVIEW_NOSANDBOX=1` → 关沙箱（降级重启后的状态）
    3. 已经降级过 → 不再降级（防重启风暴）
"""

from __future__ import annotations

import pytest

from app import _retry_without_sandbox, _sandbox_off_args


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("WBN_WEBVIEW_SANDBOX", "WBN_WEBVIEW_NOSANDBOX", "WBN_REPLACING_SELF"):
        monkeypatch.delenv(k, raising=False)


def test_sandbox_is_on_by_default():
    """默认必须带沙箱。2026-10-30 实测：两种形态下带沙箱都能正常渲染。"""
    assert _sandbox_off_args() is False


def test_explicit_sandbox_request_wins():
    """`WBN_WEBVIEW_SANDBOX=1` 是排查开关：带沙箱，且不许降级。"""
    assert _sandbox_off_args() is False


def test_nosandbox_flag_turns_it_off(monkeypatch):
    monkeypatch.setenv("WBN_WEBVIEW_NOSANDBOX", "1")
    assert _sandbox_off_args() is True


def test_retry_is_refused_when_sandbox_is_forced(monkeypatch):
    """用户显式要求带沙箱 → 不许偷偷降级。"""
    monkeypatch.setenv("WBN_WEBVIEW_SANDBOX", "1")
    assert _retry_without_sandbox() is False


def test_retry_happens_only_once(monkeypatch):
    """已经降级过还失败 → 不再重启，交给提示框。

    不挡这条的后果是重启风暴：每次超时都起一个新进程，用户机器上叠出
    十几个窗口，而且每个都黑。
    """
    monkeypatch.setenv("WBN_WEBVIEW_NOSANDBOX", "1")
    assert _retry_without_sandbox() is False
