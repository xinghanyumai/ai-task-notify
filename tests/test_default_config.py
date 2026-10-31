"""首次运行的默认配置：必须落盘，而且必须**干净**。

W14 §3 干净目录实测发现：双击 exe 起来窗口了，目录里却一个 `config.json` 都
没有——`install` / `selftest` / `tray` 三个入口都调了 `save_default_if_missing()`，
唯独 `run_ui()` 漏了。后果不是「功能坏」，是用户想手改配置连文件都找不到，
而且「拷走整个文件夹 = 带走配置」这个绿色便携的承诺也说不通。

第 1 条同时守 §3.2 的硬判据：**11 个通道凭证必须全空、clawbot 不许自动开**。
这条要是红了，说明 exe 把开发机的配置打进去了——那是要立刻停手的事故。
"""

from __future__ import annotations

import json
import re

from wbn import config

# 只允许「非凭证」的默认值：服务地址、群名、@all 这类。真凭证一律不许有。
NON_SECRET_KEYS = {
    "type", "enabled", "label", "name", "server", "group", "sound", "level",
    "tags", "touser", "host", "method", "port", "template", "mode", "title",
    "remark", "topic", "id",
}


def test_first_run_writes_a_clean_config(isolated_state):
    assert not config.CONFIG_PATH.exists(), "起点不该已有配置"

    created = config.save_default_if_missing()
    assert created is True, "第一次调用必须真写文件"
    assert config.CONFIG_PATH.exists()

    cfg = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    chans = cfg.get("channels") or []

    assert len(chans) == 11, f"通道数应当是 11，实际 {len(chans)}：{[c.get('type') for c in chans]}"

    by_type = {c.get("type"): c for c in chans}
    assert "clawbot" in by_type, "默认配置里必须有 clawbot 通道"
    assert by_type["clawbot"].get("enabled") is False, (
        "clawbot 不许默认打开——它的凭证在装过 WorkBuddy 的机器上会自动探测成功，"
        "按探测结果自动启用等于让所有人存一次配置就悄悄多收一份微信通知"
    )

    for c in chans:
        leaked = {
            k: v
            for k, v in c.items()
            if k not in NON_SECRET_KEYS and isinstance(v, str) and v.strip()
        }
        assert not leaked, f"{c.get('type')} 带了不该有的凭证：{leaked}"

    text = config.CONFIG_PATH.read_text(encoding="utf-8")
    assert not re.search(r"wx[0-9a-f]{16}", text), "默认配置里出现了真实 AppID"


def test_second_call_does_not_clobber_user_edits(isolated_state):
    """幂等：第二次不许覆盖——否则用户改完配置，重启就没了。"""
    assert config.save_default_if_missing() is True

    cfg = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    cfg["app_name"] = "用户改过的名字"
    config.CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    assert config.save_default_if_missing() is False, "已经有配置时不该再写"

    again = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    assert again["app_name"] == "用户改过的名字", "用户改的内容被默认值覆盖了"
