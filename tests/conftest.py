# -*- coding: utf-8 -*-
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

    return tmp_path


@pytest.fixture
def no_network(monkeypatch):
    """把 socket 连接掐死，任何漏网的网络调用都会直接报错而不是静默通过。"""
    import socket

    def _boom(*args, **kwargs):
        raise AssertionError("测试中禁止联网")

    monkeypatch.setattr(socket.socket, "connect", _boom, raising=True)
    monkeypatch.setattr(socket, "create_connection", _boom, raising=True)
    return _boom
