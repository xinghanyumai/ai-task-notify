"""pytest 公共装置：路径隔离。

铁律：任何测试都不得碰真实的 config.json / state/ / logs/。
`store.RUNTIME_PATH` 和 `store.QUEUE_PATH` 是**模块级常量**，在 import 时
就从 `config.STATE_DIR` 算好了，所以只 monkeypatch config 是不够的——
必须把 store 里那两个常量一起指向 tmp_path，否则限流/去重测试会
往真实的 state/runtime.json 里写数据。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """把一切落盘路径重定向到 tmp_path，返回该目录供断言使用。"""
    from wbn import config, store

    state = tmp_path / "state"
    logs = tmp_path / "logs"
    sessions = state / "sessions"
    for d in (state, logs, sessions):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "STATE_DIR", state, raising=True)
    monkeypatch.setattr(config, "LOG_DIR", logs, raising=True)
    monkeypatch.setattr(config, "SESSION_DIR", sessions, raising=True)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json", raising=True)

    monkeypatch.setattr(store, "RUNTIME_PATH", state / "runtime.json", raising=True)
    monkeypatch.setattr(store, "QUEUE_PATH", state / "queue.jsonl", raising=True)
    # ui.json 存的是「上次关沙箱才起来」这种启动偏好。漏掉这一行，
    # 测试就会往**真实的** state/ui.json 里写「以后别开沙箱」——
    # 后果是开发者本机下次启动悄悄关掉沙箱，而且没人知道是谁写的。
    monkeypatch.setattr(store, "UI_PREF_PATH", state / "ui.json", raising=True)

    # `config._last_load_error` 是模块级、会话级粘性的（故意：隔离完 config.json
    # 之后文件就不存在了，下一次 load 不能把这条错误抹掉）。粘性状态跨用例泄漏
    # 会让「配置健康」相关断言随机红，所以每个用例开头清一次。
    monkeypatch.setattr(config, "_last_load_error", "", raising=True)

    return tmp_path


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """把 socket 连接掐死，任何漏网的网络调用都会直接报错而不是静默通过。

    设为 autouse：这条防线的价值在于拦住**将来**误写的联网测试。
    如果做成按需使用，下一个人加了发真实通知的用例也不会有人发现。
    """
    import socket

    def _boom(*args, **kwargs):
        raise AssertionError("测试中禁止联网")

    monkeypatch.setattr(socket.socket, "connect", _boom, raising=True)
    monkeypatch.setattr(socket, "create_connection", _boom, raising=True)
    return _boom
