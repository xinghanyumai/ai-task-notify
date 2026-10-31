"""onedir 首次启动的配置迁移（W15 §3.3）。

背景：形态从 onefile 换成 onedir，数据根目录跟着挪了一层。

    onefile  `<仓库>/通知.exe`          → ROOT = `<仓库>`
    onedir   `<仓库>/WBNotify/通知.exe` → ROOT = `<仓库>/WBNotify`

升级上来的用户机器上，`WBNotify/config.json` 不存在，程序于是生成一份**默认**
配置：微信 appid / appsecret / openid、11 个通道凭证全空。用户看到的是
「升级把配置弄丢了」，而文件其实好端端躺在上一级目录里。

这五条守的是「迁移」这件事的五个面：该迁的迁、不该迁的一律不迁。
**第 3 条（缺 `channels` 键）是最容易被「顺手简化」掉的一条**——没有它，
`D:\\Tools\\config.json`（别的程序的配置）会被当成本程序的配置拷进来。
"""

from __future__ import annotations

import json
import sys

import pytest

from wbn import config

VALID = {
    "version": 1,
    "enabled": True,
    "channels": [{"type": "wxmp", "enabled": True, "appid": "wx_test", "secret": "s"}],
}


def _onedir_layout(tmp_path, parent_config: dict | str | None = None):
    """造一个 onedir 布局，返回 (root, exe)。

    `parent_config` 写到 `<root>/../config.json`（即仓库根）。传字符串就原样
    写入（用来造非法 JSON），传 dict 就 `json.dumps`。
    """
    root = tmp_path / "WBNotify"
    root.mkdir()
    (root / "_internal").mkdir()
    exe = root / "通知.exe"
    exe.write_bytes(b"stub")

    if parent_config is not None:
        p = tmp_path / "config.json"
        text = parent_config if isinstance(parent_config, str) else json.dumps(
            parent_config, ensure_ascii=False)
        p.write_text(text, encoding="utf-8")
    return root, exe


@pytest.fixture
def as_onedir(monkeypatch):
    """把进程伪装成「正在跑 onedir 的 exe」。"""
    def _apply(exe):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe), raising=True)

    return _apply


def test_valid_parent_config_is_copied_verbatim(tmp_path, as_onedir):
    """场景 1：onedir + 上级有合法 config → 复制过来，内容逐字节相同。"""
    root, exe = _onedir_layout(tmp_path, VALID)
    as_onedir(exe)

    src = config._migrate_from_parent(root)

    assert src == tmp_path, f"应当报告来源是 {tmp_path}，实际 {src}"
    got = root / "config.json"
    assert got.exists(), "上级有合法配置却没迁过来——用户会看到 11 个通道全空"
    assert got.read_bytes() == (tmp_path / "config.json").read_bytes(), "内容不是逐字节相同"


def test_state_and_logs_come_along(tmp_path, as_onedir):
    """state/（token 缓存）和 logs/ 也要一起搬——只搬 config 等于还得重新授权。

    注：`tmp_path/state` 和 `tmp_path/logs` 是 conftest 的 `isolated_state`
    装置建的（它把一切落盘路径重定向到这里），所以用 `exist_ok=True`。
    """
    root, exe = _onedir_layout(tmp_path, VALID)
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "token_cache.json").write_text('{"k": 1}', encoding="utf-8")
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "logs" / "notify-2026-10-31.log").write_text("一行日志\n", encoding="utf-8")
    as_onedir(exe)

    config._migrate_from_parent(root)

    assert (root / "state" / "token_cache.json").exists(), "state/ 没搬过来"
    assert (root / "logs" / "notify-2026-10-31.log").exists(), "logs/ 没搬过来"


def test_broken_json_is_not_migrated(tmp_path, as_onedir, monkeypatch):
    """场景 2：上级 config 是非法 JSON → 不迁移，之后生成默认配置。"""
    root, exe = _onedir_layout(tmp_path, "{ 这不是 JSON")
    as_onedir(exe)

    assert config._migrate_from_parent(root) is None
    assert not (root / "config.json").exists(), "非法 JSON 被当配置拷进来了"

    # 「不迁移」之后的行为要还是「生成一份默认配置」，不是「什么都没有」
    monkeypatch.setattr(config, "CONFIG_PATH", root / "config.json")
    assert config.save_default_if_missing() is True
    assert (root / "config.json").exists()


def test_config_without_channels_key_is_not_migrated(tmp_path, as_onedir):
    """场景 3：上级 config 缺 `channels` → 不迁移。

    这条守的是「别人的 config.json」：用户把 zip 解压到 `D:\\Tools\\`，
    那里可能躺着另一个程序的同名文件。没有 `channels` 就不是我们的。
    """
    root, exe = _onedir_layout(tmp_path, {"version": 1, "some_other_tool": {"k": "v"}})
    as_onedir(exe)

    assert config._migrate_from_parent(root) is None
    assert not (root / "config.json").exists(), "缺 channels 键的配置被当成本程序的了"


def test_existing_config_is_never_touched(tmp_path, as_onedir):
    """场景 4：自己已经有 config → 不迁移，自己的内容一个字节都不变（幂等）。"""
    root, exe = _onedir_layout(tmp_path, VALID)
    mine = {"version": 1, "channels": [{"type": "log", "enabled": True}], "note": "我自己的"}
    (root / "config.json").write_text(json.dumps(mine, ensure_ascii=False), encoding="utf-8")
    before = (root / "config.json").read_bytes()
    as_onedir(exe)

    assert config._migrate_from_parent(root) is None
    assert (root / "config.json").read_bytes() == before, "已有配置被覆盖了"


def test_source_mode_never_migrates(tmp_path, monkeypatch):
    """场景 5：源码态永不迁移。

    源码态 `root` 就是仓库根，上一级是 `D:\\Code`——那里要真有份同名配置，
    迁进来就是灾难。所以第 1 条条件里的 `_internal` 判断不能省。
    """
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "config.json").write_text(json.dumps(VALID), encoding="utf-8")

    assert config._migrate_from_parent(repo) is None
    assert not (repo / "config.json").exists()


def test_onefile_never_migrates(tmp_path, monkeypatch):
    """onefile 的 exe 就在仓库根，没有 `_internal` —— 不该触发迁移。

    onefile 下 ROOT 本来就是仓库根，`root/config.json` 就是用户那份，
    条件 2 已经挡住了；但形态判断先挡一次更安全（万一 exe 被拷到别处）。
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    exe = tmp_path / "通知.exe"
    exe.write_bytes(b"stub")
    monkeypatch.setattr(sys, "executable", str(exe), raising=True)
    (tmp_path / "config.json").write_text(json.dumps(VALID), encoding="utf-8")

    assert config._migrate_from_parent(tmp_path) is None
