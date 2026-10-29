"""W4 新加的四个 bridge 方法 + `set_dirty` 的来源语义。

这一组测试守的是「两处说同一件事」那条线：
预览文字只能由 `insight.preview` 算（bridge 只转手），过滤器的类型转换只能由
`insight.normalize_filters` 做（bridge 只转手）。测试直接拿两边对拍。
"""

from __future__ import annotations

import json

from wbn import bridge, config, insight


def _write_cfg(tmp_path, **over):
    cfg = config.load()
    for k, v in over.items():
        cfg[k] = v
    (tmp_path / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return cfg


# ------------------------------------------------------------ set_dirty


def test_set_dirty_tracks_sources_separately():
    """两个页面各报各的，取并集——一个页面报干净不许把另一个页面的脏抹掉。

    现场（W4 审计 A12）：触发页改脏 → 切到通道页 → 通道页 `syncDirty()` 发现
    自己不脏 → 发 `set_dirty(false)` → 触发页的改动失去保护，关窗直接丢。
    """
    b = bridge.Bridge()

    assert b.set_dirty(True, "triggers")["data"] == {"dirty": True}
    assert b._unsaved is True

    b.set_dirty(True, "channels")
    b.set_dirty(False, "channels")
    assert b._unsaved is True, "通道页报干净，不该把触发页的脏抹掉"

    b.set_dirty(False, "triggers")
    assert b._unsaved is False


def test_set_dirty_default_source_keeps_old_contract():
    """不传 source 时仍是「一个布尔」——`channels.js` 和旧测试都这么调。"""
    b = bridge.Bridge()
    assert b.set_dirty(True)["data"] == {"dirty": True}
    assert b._unsaved is True
    assert b.set_dirty(False)["data"] == {"dirty": False}
    assert b._unsaved is False


# ------------------------------------------------------------ ui_meta


def test_ui_meta_lists_every_template_kind():
    """7 个模板类型一个都不能少。

    任务书 §2.1 列了 5 个（含两个 session、不含 `test`/`tool_failed`），
    `ui.py` 列了 5 个（含 `test`/`tool_failed`、不含两个 session）。
    两个列表都不完整——漏掉的那个类型用户永远改不了，但它照样会发出去。
    """
    data = bridge.Bridge().ui_meta()["data"]
    keys = [k["key"] for k in data["template_kinds"]]
    assert keys == list(config.DEFAULT_CONFIG["templates"])
    assert "test" in keys and "session_end" in keys and "tool_failed" in keys


def test_ui_meta_placeholders_match_render():
    """占位符清单只有一份（`render.PLACEHOLDERS`），bridge 只转手。"""
    from wbn import render

    data = bridge.Bridge().ui_meta()["data"]
    assert [p["token"] for p in data["placeholders"]] == [
        t for t, _d in render.PLACEHOLDERS]
    # 任务书 §2.1 里那两个不存在的变量不许出现在清单里
    tokens = {p["token"] for p in data["placeholders"]}
    assert "{title}" not in tokens and "{detail}" not in tokens


def test_ui_meta_reports_booleans_not_counts():
    """只回「有没有一条通道开着」，不回数字——数字在状态页有唯一出处。"""
    data = bridge.Bridge().ui_meta()["data"]
    assert isinstance(data["any_channel_on"], bool)
    assert isinstance(data["enabled"], bool)
    assert "enabled_channels" not in data


# ------------------------------------------------------------ 预览


def test_template_preview_is_insight_preview():
    """预览文字**逐字**来自 `insight.preview`，bridge 不加工。"""
    b = bridge.Bridge()
    cfg = config.load()
    title = "✅ {app} 任务完成"
    content = "**项目**：{project}\n**耗时**：{duration}\n\n{summary}"

    got = b.template_preview("task_done", title, content)["data"]
    want = insight.preview(cfg, "task_done", title, content)

    assert got["title"] == want["title"]
    assert got["body"] == want["body"]
    assert got["vars_used"] == want["vars_used"]
    assert got["vars_missing"] == want["vars_missing"]


def test_template_preview_reports_unknown_placeholder():
    got = bridge.Bridge().template_preview(
        "task_done", "t", "项目：{projcet}")["data"]
    assert got["vars_missing"] == ["{projcet}"]


def test_template_preview_strips_markdown():
    """纯文本通道会去掉 `**`，预览必须和发出去的一致。"""
    got = bridge.Bridge().template_preview(
        "task_done", "t", "**项目**：{project}")["data"]
    assert "**" not in got["body"]
    assert got["body"].startswith("项目：")


# ------------------------------------------------------------ 触发页保存


def test_triggers_save_normalizes_filter_types(tmp_path):
    """`"20"` → 20、`"a, b"` → ["a","b"]，转换只在 Python 侧做一次。

    前端若自己 split，`projects_include` 可能存成字符串；`_match_any` 对 str
    迭代是**逐字符**匹配，过滤规则会静默失效（W4 审计 A8）。
    """
    _write_cfg(tmp_path)
    got = bridge.Bridge().triggers_save(
        {"task_done": True, "session_start": "yes"},
        {"min_duration_seconds": "20",
         "projects_include": "demo, work",
         "quiet_hours": " 23:00-08:00 "},
    )["data"]

    assert got["triggers"]["task_done"] is True
    assert got["triggers"]["session_start"] is True       # 真布尔，不是字符串
    assert got["filters"]["min_duration_seconds"] == 20
    assert got["filters"]["projects_include"] == ["demo", "work"]
    assert got["filters"]["quiet_hours"] == "23:00-08:00"

    on_disk = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert on_disk["filters"]["projects_include"] == ["demo", "work"]


def test_triggers_save_does_not_touch_channels(tmp_path):
    """读-改-写：没出现在 patch 里的键原样保留。"""
    _write_cfg(tmp_path)
    before = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))

    bridge.Bridge().triggers_save({"task_done": False}, {"dedup_seconds": "60"})

    after = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert after["channels"] == before["channels"]
    assert after["templates"] == before["templates"]


# ------------------------------------------------------------ 静默时段


def test_quiet_hours_check_rejects_unparseable_segment():
    """`23:00~08:00` 这种会被 `filters` 整段忽略——必须在失焦时说出来。

    `in_quiet_hours` 遇到解析不了的段是 `continue`，界面上一切正常，
    实际一条也没拦住（W4 审计 A6）。
    """
    data = bridge.Bridge().quiet_hours_check("23:00~08:00")["data"]
    assert data["ok"] is False
    assert data["bad"] == ["23:00~08:00"]


def test_quiet_hours_check_accepts_multi_segment():
    data = bridge.Bridge().quiet_hours_check("23:00-08:00, 12:00-13:30")["data"]
    assert data["ok"] is True
    assert data["bad"] == []


def test_quiet_hours_check_agrees_with_filters():
    """合法性判据就是 `filters._parse_hhmm` 本身，不是第二套正则。"""
    from wbn import filters

    b = bridge.Bridge()
    for spec in ("23:00-08:00", "8:00-9:00", "23:00", "abc", "", "12:00-13:30"):
        data = b.quiet_hours_check(spec)["data"]
        # 逐段核对：合法的段必须能被 filters 解析，非法的段必然解析失败
        for part in data["bad"]:
            if "-" in part:
                a, c = part.split("-", 1)
                assert filters._parse_hhmm(a) is None or filters._parse_hhmm(c) is None
        if data["ok"]:
            for part in [p.strip() for p in spec.split(",") if p.strip()]:
                a, c = part.split("-", 1)
                assert filters._parse_hhmm(a) is not None
                assert filters._parse_hhmm(c) is not None
