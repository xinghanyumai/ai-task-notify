"""`wbn/insight.py` 的行为守卫。

这个模块是从 `ui.py` 搬出来的业务逻辑，搬错不会报错，只会让页面上
「19 显示成 38」。所以这里的重点不是覆盖率，是**把那几个踩过的坑钉住**：

- `stats` 只数 `channel=` 行（汇总行的 `remote_ok=False` 不能算失败）
- `stats` 剥掉标题开头的 emoji
- `merge_save` 是读-改-写，不能拿内存整份覆盖磁盘
- `chain_sentence` 里 log 通道不算「送到手机」
- `preview` 的正文要剥 markdown（和实际发出去的一致）

另外三条硬规则也在这里守着：不出现颜色、状态是语义枚举、纯函数无状态。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from wbn import config, insight

# ---------------------------------------------------------------- 硬规则


def test_module_has_no_ui_dependency():
    """insight 不许碰 GUI：没有 tkinter / webview 导入，没有十六进制色值。"""
    src = (config.ROOT / "wbn" / "insight.py").read_text(encoding="utf-8")
    assert "import tkinter" not in src
    assert "import webview" not in src
    assert "from tkinter" not in src
    assert not __import__("re").search(r"#[0-9a-fA-F]{6}", src)


def test_levels_are_semantic_not_colors():
    """语义枚举是固定四个，不是颜色值。"""
    assert set(insight.LEVELS) if hasattr(insight, "LEVELS") else True
    levels = {insight.LEVEL_OK, insight.LEVEL_WARN,
              insight.LEVEL_DANGER, insight.LEVEL_MUTED}
    assert levels == {"ok", "warn", "danger", "muted"}


# ---------------------------------------------------------------- 字符串工具


@pytest.mark.parametrize("raw, want", [
    ("a,b", ["a", "b"]),
    ("a，b", ["a", "b"]),          # 中文逗号
    (" a , b ", ["a", "b"]),       # 两端空白
    ("a,,b", ["a", "b"]),          # 空段丢弃
    ("", []),
    (None, []),
])
def test_split_list(raw, want):
    assert insight.split_list(raw) == want


@pytest.mark.parametrize("raw, default, want", [
    ("12", 0, 12),
    (" 12 ", 0, 12),
    ("", 7, 7),
    ("abc", 7, 7),
    (None, 7, 7),
])
def test_to_int(raw, default, want):
    assert insight.to_int(raw, default) == want


@pytest.mark.parametrize("raw, emoji, rest", [
    ("✅ 任务完成", "✅", "任务完成"),
    ("⚠️ 工具失败", "⚠️", "工具失败"),
    ("任务完成", "", "任务完成"),
    ("", "", ""),
])
def test_split_emoji(raw, emoji, rest):
    assert insight.split_emoji(raw) == (emoji, rest)


# ---------------------------------------------------------------- stats


def _write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_stats_counts_only_channel_lines():
    """**这是「19 变 38」那个 bug 的回归测试。**

    每个事件在通道行之后还有一行汇总，里面 `remote_ok=False` 含子串
    "ok=False"。如果不过滤 `channel=`，失败数直接翻倍。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    _write(config.LOG_DIR / f"notify-{today}.log", [
        f"{today} 10:00:00 channel=wxmp ok=True msg=1.2s",
        f"{today} 10:00:00 channel=bark ok=False msg=timeout",
        f"{today} 10:00:00 event=Stop kind=task_done sent=True "
        f"remote_ok=False remote_fail=1 dedup=False",
    ])
    s = insight.stats(config.load())
    assert s["ok"] == 1
    assert s["fail"] == 1, "汇总行的 remote_ok=False 不能被算成一次失败"


def test_stats_counts_today_and_recent_newest_first():
    today = datetime.now().strftime("%Y-%m-%d")
    # 真实日志是追加写的，最后一行才是最新的
    _write(config.LOG_DIR / "notifications.log", [
        "2020-01-01 09:10:00 [WorkBuddy] task_done | 去年那条",
        f"{today} 09:00:00 [WorkBuddy] task_done | ✅ 第一条",
        f"{today} 09:05:00 [WorkBuddy] task_failed | ❌ 第二条",
    ])
    s = insight.stats(config.load())
    assert s["today"] == 2, "只数今天的"
    assert len(s["recent"]) == 3, "recent 不按日期过滤，只取最后 8 条"
    assert s["recent"][0]["summary"] == "第二条", "recent 是最新在前"
    assert s["recent"][0]["ok"] is False
    assert s["recent"][1]["ok"] is True


def test_stats_strips_leading_emoji_from_title():
    today = datetime.now().strftime("%Y-%m-%d")
    _write(config.LOG_DIR / "notifications.log", [
        f"{today} 09:00:00 [WorkBuddy] task_done | ✅ 重构完成",
    ])
    row = insight.stats(config.load())["recent"][0]
    assert row["summary"] == "重构完成"
    assert "✅" not in row["summary"]


def test_stats_recent_caps_at_eight():
    today = datetime.now().strftime("%Y-%m-%d")
    _write(config.LOG_DIR / "notifications.log", [
        f"{today} 09:00:{i:02d} [WorkBuddy] task_done | 第 {i} 条"
        for i in range(20)
    ])
    assert len(insight.stats(config.load())["recent"]) == 8


def test_stats_missing_logs_is_all_zero():
    s = insight.stats(config.load())
    assert (s["today"], s["ok"], s["fail"]) == (0, 0, 0)
    assert s["recent"] == []


# ---------------------------------------------------------------- chain_sentence


def _cfg(**kw) -> dict:
    cfg = config.load()
    cfg["enabled"] = True
    cfg["triggers"] = {k: False for k in cfg["triggers"]}
    cfg["triggers"]["task_done"] = True
    for ch in cfg["channels"]:
        ch["enabled"] = False
    cfg.update(kw)
    return cfg


def _enable(cfg, ctype):
    for ch in cfg["channels"]:
        if ch["type"] == ctype:
            ch["enabled"] = True


def test_chain_muted_when_master_off():
    cfg = _cfg(enabled=False)
    text, level = insight.chain_sentence(cfg)
    assert "已静音" in text
    assert level == insight.LEVEL_WARN


def test_chain_danger_when_no_channel():
    text, level = insight.chain_sentence(_cfg())
    assert "没有可用通道" in text
    assert level == insight.LEVEL_DANGER


def test_chain_warn_when_no_trigger():
    cfg = _cfg()
    _enable(cfg, "wxmp")
    cfg["triggers"] = {k: False for k in cfg["triggers"]}
    text, level = insight.chain_sentence(cfg)
    assert "没有勾选任何事件" in text
    assert level == insight.LEVEL_WARN


def test_chain_log_only_is_not_phone_delivery():
    """log 通道只写本地文件，不能算「送到手机」——混进去就是骗人。"""
    cfg = _cfg()
    _enable(cfg, "log")
    text, level = insight.chain_sentence(cfg)
    assert "只写入本地日志" in text
    assert "手机" in text
    assert level == insight.LEVEL_WARN


def test_chain_ok_names_channels():
    cfg = _cfg()
    _enable(cfg, "wxmp")
    text, level = insight.chain_sentence(cfg)
    assert "任务完成时" in text
    assert "送到你手机" in text
    assert level == insight.LEVEL_OK


# ---------------------------------------------------------------- channel_state


def test_channel_state_three_states():
    ready = {"type": "bark", "enabled": True, "key": "k"}
    subtitle, level, lit = insight.channel_state("bark", ready)
    assert (subtitle, level, lit) == ("已启用", insight.LEVEL_OK, True)

    off = {"type": "bark", "enabled": False, "key": "k"}
    subtitle, level, lit = insight.channel_state("bark", off)
    assert (subtitle, level, lit) == ("已配置，未启用", insight.LEVEL_WARN, False)

    empty = {"type": "bark", "enabled": False}
    subtitle, level, lit = insight.channel_state("bark", empty)
    assert subtitle.startswith("待配置：缺")
    assert level == insight.LEVEL_MUTED
    assert lit is False


def test_channel_state_counts_missing_fields():
    # wxmp 必填三项，缺 3 个才会出现「等 N 项」后缀
    subtitle, _, _ = insight.channel_state("wxmp", {"type": "wxmp"})
    assert "待配置：缺" in subtitle
    assert "等 3 项" in subtitle


# ---------------------------------------------------------------- pick_channel


def test_pick_channel_prefers_enabled_and_ready():
    cfg = _cfg()
    _enable(cfg, "log")          # log 不需要字段，直接 ready
    assert insight.pick_channel(cfg) == "log"


def test_pick_channel_falls_back_to_wxmp():
    cfg = _cfg()
    for ch in cfg["channels"]:
        ch.clear()
        ch["type"] = "webhook"   # 缺 url，谁都不 ready
    assert insight.pick_channel(cfg) == "wxmp"


# ---------------------------------------------------------------- normalize_filters


def test_normalize_filters_types():
    out = insight.normalize_filters({
        "projects_include": "a, b，c",
        "quiet_hours": " 23:00-08:00 ",
        "min_duration_seconds": "30",
        "max_per_minute": "abc",
        "question_as_need_input": 1,
    })
    assert out["projects_include"] == ["a", "b", "c"]
    assert out["quiet_hours"] == "23:00-08:00"
    assert out["min_duration_seconds"] == 30
    # 非数字回落 DEFAULT_CONFIG 里的默认值，不写死 0
    assert out["max_per_minute"] == config.DEFAULT_CONFIG["filters"]["max_per_minute"]
    assert out["question_as_need_input"] is True


def test_normalize_filters_accepts_list_input():
    out = insight.normalize_filters({"projects_include": [" a ", "", "b"]})
    assert out["projects_include"] == ["a", "b"]


# ---------------------------------------------------------------- merge_save


def test_merge_save_does_not_drop_concurrent_changes():
    """**外来方案第 5 条修正的落点。**

    UI 打开期间 hook 进程可能改了 config（这里模拟：磁盘上已有 bark.key），
    UI 只提交了 triggers 的一个字段。整份覆盖会把 bark.key 抹掉。
    """
    cfg = config.load()
    cfg["triggers"]["session_start"] = False
    config.save(cfg)
    # 模拟「另一个进程」往磁盘写了一个 UI 不知道的字段
    disk = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    for ch in disk["channels"]:
        if ch["type"] == "bark":
            ch["key"] = "hook-写进来的-key"
    config.CONFIG_PATH.write_text(json.dumps(disk, ensure_ascii=False),
                                  encoding="utf-8")

    res = insight.merge_save({"triggers": {"session_start": True}})
    assert res["ok"] is True
    assert res["cfg"]["triggers"]["session_start"] is True
    saved = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    bark = next(c for c in saved["channels"] if c["type"] == "bark")
    assert bark["key"] == "hook-写进来的-key", "并发写入的字段被覆盖了"


def test_merge_save_merges_channels_by_type():
    """patch 里只带一个通道的一个字段，其他通道的配置不能丢。"""
    cfg = config.load()
    config.save(cfg)
    before = len(json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))["channels"])

    res = insight.merge_save({"channels": [{"type": "bark", "group": "改过"}]})
    assert res["ok"] is True
    saved = json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
    assert len(saved["channels"]) == before
    bark = next(c for c in saved["channels"] if c["type"] == "bark")
    assert bark["group"] == "改过"
    assert "server" in bark, "同通道其他字段不能被抹掉"


def test_merge_save_runs_auto_enable():
    """填齐凭证后自动启用——现有行为，不许丢。"""
    res = insight.merge_save({
        "channels": [{"type": "bark", "key": "abc123"}],
    })
    assert res["ok"] is True
    bark = next(c for c in res["cfg"]["channels"] if c["type"] == "bark")
    assert bark["enabled"] is True
    assert any("Bark" in x for x in res["turned_on"]), \
        "turned_on 里存的是给用户看的短名"


# ---------------------------------------------------------------- reset_channel


def test_reset_channel_reads_from_disk_not_memory():
    cfg = config.load()
    config.save(cfg)
    edited = cfg["channels"][0].copy()
    edited["__dirty__"] = "用户改了一半"
    got = insight.reset_channel({"channels": [edited]}, edited["type"])
    assert "__dirty__" not in got, "重置要回到磁盘那份，不是内存副本"


# ---------------------------------------------------------------- preview


def test_preview_strips_markdown_and_renders_placeholders():
    cfg = config.load()
    out = insight.preview(
        cfg, "task_done",
        "✅ {app} 任务完成",
        "**项目**：{project}\n**耗时**：{duration}\n\n{summary}",
    )
    assert "**" not in out["body"], "微信是纯文本，预览要剥 markdown"
    assert "{project}" not in out["body"], "占位符要被替换"
    assert "demo-project" in out["body"]
    assert out["vars_used"], "用到的占位符要能列出来"


def test_preview_reports_unknown_placeholder():
    cfg = config.load()
    out = insight.preview(cfg, "task_done", "标题", "正文 {nope} 和 {project}")
    assert out["vars_missing"] == ["{nope}"], \
        "写了但不存在的变量要报出来，否则发出去是空白"
    assert "{project}" not in out["vars_missing"]


def test_preview_empty_template_does_not_raise():
    out = insight.preview(config.load(), "task_done", "", "")
    assert isinstance(out["title"], str)
    assert isinstance(out["body"], str)


# ---------------------------------------------------------------- 纯函数


def test_stats_does_not_mutate_cfg():
    cfg = config.load()
    snapshot = json.dumps(cfg, ensure_ascii=False, sort_keys=True)
    insight.stats(cfg)
    insight.chain_sentence(cfg)
    insight.pick_channel(cfg)
    assert json.dumps(cfg, ensure_ascii=False, sort_keys=True) == snapshot


def test_open_path_reports_failure_without_raising():
    ok, err = insight.open_path("Z:/definitely/not/here")
    assert ok is False
    assert err
