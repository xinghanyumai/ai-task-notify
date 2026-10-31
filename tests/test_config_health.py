"""配置损坏时不许静默清空（W10 §4）。

旧行为是 `except: user = {}`——用户看到所有通道突然「待配置」，以为凭证丢了，
其实只是程序假装没看见。新行为两条：

1. **改名备份**，绝不删。那是用户唯一一份凭证副本。
2. **说人话**，并且界面上真的出现一条警示（条件渲染，不是藏起来）。
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from wbn import bridge, config

BROKEN = '{坏 JSON'


def _broken_cfg(isolated_state):
    path = isolated_state / "config.json"
    path.write_text(BROKEN, encoding="utf-8")
    return path


def _backups(isolated_state):
    return sorted((isolated_state).glob("config.json.broken-*"))


# ---------------------------------------------------------------- 备份


def test_broken_config_is_renamed_not_deleted(isolated_state):
    path = _broken_cfg(isolated_state)

    cfg = config.load()

    assert not path.exists(), "原文件必须被改名（不是删掉，也不是留着）"
    backups = _backups(isolated_state)
    assert len(backups) == 1
    # 逐字节一致：用户去备份里抄 appsecret 时，抄到的必须是他原来那份
    assert backups[0].read_bytes() == BROKEN.encode("utf-8")
    assert cfg["channels"], "回退到默认值，不是空配置"


def test_backup_name_is_sortable_local_timestamp(isolated_state):
    """`config.json.broken-YYYYmmdd-HHMMSS`：字典序 = 时间序。"""
    _broken_cfg(isolated_state)

    config.load()

    name = _backups(isolated_state)[0].name
    m = re.fullmatch(r"config\.json\.broken-(\d{8})-(\d{6})", name)
    assert m, f"备份名格式不对：{name}"


def test_second_load_does_not_create_another_backup(isolated_state):
    """连续两次 load 只该产生一个 broken 文件。

    第二次已经没有「可损坏的原文件」了（它被改名了，`CONFIG_PATH` 不存在），
    再备份一次就等于凭空多一个空文件，用户会以为坏了两回。
    """
    _broken_cfg(isolated_state)

    config.load()
    config.load()

    assert len(_backups(isolated_state)) == 1


def test_same_second_collision_does_not_overwrite(isolated_state):
    """同一秒内坏两次也不许覆盖前一个备份——那会把用户唯一那份凭证冲掉。"""
    path = _broken_cfg(isolated_state)
    config.load()
    first = _backups(isolated_state)[0]
    path.write_text("{另一份坏的", encoding="utf-8")

    config.load()

    backups = _backups(isolated_state)
    assert len(backups) == 2
    assert backups[0].read_bytes() == BROKEN.encode("utf-8")
    assert first.exists()


# ---------------------------------------------------------------- 人话


def test_error_message_names_the_backup(isolated_state):
    """错误信息里必须有备份文件名，否则用户不知道去哪找。"""
    _broken_cfg(isolated_state)

    config.load()

    backup = _backups(isolated_state)[0].name
    assert backup in config._last_load_error
    assert "备份" in config._last_load_error


def test_error_clears_after_a_successful_load(isolated_state):
    _broken_cfg(isolated_state)
    config.load()
    assert config._last_load_error

    config.save_default_if_missing()
    config.load()

    assert config._last_load_error == ""


def test_healthy_config_reports_ok(isolated_state):
    config.save_default_if_missing()

    got = bridge.Bridge().config_health()["data"]

    assert got["ok"] is True
    assert got["broken_backup"] == ""
    assert got["msg"] == ""


def test_health_reports_the_backup_file(isolated_state):
    _broken_cfg(isolated_state)
    config.load()

    got = bridge.Bridge().config_health()["data"]

    assert got["ok"] is False
    assert got["broken_backup"] == _backups(isolated_state)[0].name
    assert got["msg"]


@pytest.mark.parametrize("payload", ["[]", "null", '"字符串"', "123"])
def test_valid_json_but_wrong_type_is_not_silently_accepted(isolated_state, payload):
    """`[]` 是合法 JSON，但当成配置用会炸。

    这种「能解析、不能用」的配置和语法错误一样是坏配置，必须同样备份，
    否则下一步 `deep_merge` 会抛一个用户完全看不懂的 TypeError。
    """
    path = isolated_state / "config.json"
    path.write_text(payload, encoding="utf-8")

    cfg = config.load()

    assert isinstance(cfg, dict)
    assert cfg["channels"]
    assert len(_backups(isolated_state)) == 1
    assert config._last_load_error


def test_concurrent_loads_report_success_not_failure(isolated_state):
    """两个线程同时读到坏文件时，后到的那个不许报「备份失败」。

    前端一进页面就同时发好几个 bridge 调用（`stats_today` / `channels_list` /
    `config_get` 都会 `load()`）。谁先读到谁去改名，剩下的 `replace` 必然扑空
    ——源文件已经没了。这时报「原文件备份失败」是在吓用户：备份其实好好的。
    """
    import threading

    _broken_cfg(isolated_state)
    errors = []

    def _worker():
        config.load()
        errors.append(config._last_load_error)

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(_backups(isolated_state)) == 1
    assert all("备份失败" not in e for e in errors), errors
    assert all(_backups(isolated_state)[0].name in e for e in errors), errors


def test_transient_permission_error_on_rename_is_retried(isolated_state, monkeypatch):
    """Windows 上「改名一个正被别人打开的文件」会 EACCES。

    CI 真撞到过（`windows-latest / py3.12`，2026-10-31 06:38）：另一个线程
    正 `read_text()`，这个线程去改名，`PermissionError` 一路冒到用户面前变成
    「原文件备份失败」——而备份其实一秒钟后就成功了。

    这里把**第一次** `Path.replace` 打成必失败，钉住「等一下再试」这条路。
    不重试的话这条用例会红。
    """
    _broken_cfg(isolated_state)
    real = pathlib.Path.replace
    state = {"n": 0}

    def _flaky(self, target):
        if self.name == "config.json" and state["n"] == 0:
            state["n"] += 1
            raise PermissionError(13, "另一个线程还开着它")
        return real(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", _flaky)

    config.load()

    assert state["n"] == 1, "第一次必须真的失败过，否则这条用例什么都没验"
    assert len(_backups(isolated_state)) == 1
    assert "备份失败" not in config._last_load_error
    assert _backups(isolated_state)[0].name in config._last_load_error


def test_roundtrip_still_works(isolated_state):
    """备份逻辑不许影响正常路径：存一次、读一次，值还在。"""
    config.save_default_if_missing()
    cfg = config.load()
    cfg["app_name"] = "改过的"
    config.save(cfg)

    assert config.load()["app_name"] == "改过的"
    assert _backups(isolated_state) == []


def test_backup_content_is_json_parseable_when_original_was(isolated_state):
    """一个「字段类型错」的配置备份出来仍然是合法 JSON——用户能直接改。"""
    path = isolated_state / "config.json"
    path.write_text(json.dumps({"channels": "不是数组"}), encoding="utf-8")

    config.load()

    backups = _backups(isolated_state)
    assert backups, "这种配置也该备份"
    json.loads(backups[0].read_text(encoding="utf-8"))
