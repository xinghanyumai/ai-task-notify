"""微信通道的 emoji 剥离（W6.2 / R43）。

真机现象：默认标题 `✅ {app} 任务完成` 到手机上变成 ` WorkBuddy 任务完成`——
**微信静默过滤了 emoji，但把 emoji 后面的空格留着**，状态行开头多一个空格，
卡片里看是缩进的。

三条要求：
1. 剥 emoji，**不做映射表**（`✅` → `[完成]` 这种）。映射表永远跟不上 Unicode。
2. 剥完必须 `strip()`，并且把留下的连续空格折叠掉。
3. **只动微信通道**。bark / ntfy / webhook 的标题要原样保留 emoji——它们
   的客户端能正常渲染。

判据走 `unicodedata.category()`：`So` / `Sk` 是符号类。但只剥**非 Latin-1**
的那部分，因为 Latin-1 里的 `° © ® ¨ ^ ~` 是排版符号不是表情，剥掉会把
`25°C` 变成 `25C`、把代码里的脱字符吃掉——过度剥离和漏剥离一样是 bug
（R45 的教训）。
"""

from __future__ import annotations

import unicodedata

import pytest

from wbn import channels, config

# 默认标题的剥除结果。这张表同时是「默认标题清单」的断言——
# 加了新 kind 就必须来这里补一行，否则 test_title_table_matches_config 会红。
TITLES = [
    ("task_done", "✅ {app} 任务完成", "{app} 任务完成"),
    ("task_failed", "❌ {app} 任务失败", "{app} 任务失败"),
    ("need_input", "🙋 {app} 等你确认", "{app} 等你确认"),
    ("tool_failed", "⚠️ 工具失败 · {tool}", "工具失败 · {tool}"),
    ("session_start", "▶️ {app} 会话开始", "{app} 会话开始"),
    ("session_end", "⏹ {app} 会话结束", "{app} 会话结束"),
    ("test", "🔔 通知测试", "通知测试"),
]


def _leftovers(text: str) -> list[str]:
    """字符串里还残留的 emoji / 不可见修饰符，用于断言「剥干净了」。"""
    bad = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("So", "Sk") and ord(ch) > 0xFF:
            bad.append(ch)
        elif unicodedata.name(ch, "").startswith(channels._EMOJI_GLUE):
            bad.append(ch)
    return bad


# ------------------------------------------------- 7 个默认标题


def test_title_table_matches_config():
    """表里的 kind 和源码里的一模一样——防止默认标题悄悄加了第 8 个。"""
    defaults = config.DEFAULT_CONFIG["titles"]
    assert {kind for kind, _, _ in TITLES} == set(defaults), "默认标题清单变了，补测试"
    for kind, src, _ in TITLES:
        assert defaults[kind] == src, f"{kind} 的默认标题变了：{defaults[kind]!r}"


@pytest.mark.parametrize("kind,src,want", TITLES, ids=[t[0] for t in TITLES])
def test_default_title_is_stripped_clean(kind, src, want):
    """`✅ {app} 任务完成` → `{app} 任务完成`，前面不留空格。"""
    filled = src.replace("{app}", "WorkBuddy").replace("{tool}", "Bash")
    got = channels._tidy(filled)
    assert got == want.replace("{app}", "WorkBuddy").replace("{tool}", "Bash")
    assert not got.startswith(" "), f"{kind} 剥完仍有前导空格：{got!r}"


def test_no_default_title_keeps_emoji_or_orphans():
    """7 个标题全过一遍：没有 So/Sk，也没有看不见的孤儿修饰符。"""
    for kind, src, _ in TITLES:
        got = channels._tidy(src)
        assert not _leftovers(got), f"{kind} 还残留 {_leftovers(got)!r}：{got!r}"


# ------------------------------------------------- 微信字段出口


TPL = (
    "状态：{{title.DATA}}\n"
    "详情：{{detail.DATA}}\n"
    "补充：{{detail2.DATA}}\n"
    "项目：{{project.DATA}}\n"
    "时间：{{time.DATA}}"
)
DECLARED = ["title", "detail", "detail2", "project", "time"]

EMOJI_CONTENT = "✅ 已经修好了 🎉\n❌ 但回归没过\n⚠️ 需要你确认 🙋‍♀️"


def _wxmp_payload(title: str = "✅ WorkBuddy 任务完成", content: str = EMOJI_CONTENT):
    values = channels._wxmp_values(
        title, content, {"kind": "task_done", "project": "demo ✅"},
        "2026年10月30日 21:00", wrap=True, tpl_content=TPL,
    )
    return channels._map_wxmp_data(DECLARED, values)


def test_every_wxmp_field_is_free_of_emoji():
    """微信通道的每个字段值都不许带 emoji——它们到手机上会变成空气 + 空格。"""
    for name, item in _wxmp_payload().items():
        assert not _leftovers(item["value"]), f"{name} 还带 emoji：{item['value']!r}"


def test_every_wxmp_field_is_stripped_and_collapsed():
    """字段值不许有前导/尾随空格，也不许有两个挨着的空格。"""
    for name, item in _wxmp_payload().items():
        v = item["value"]
        assert v == v.strip(), f"{name} 首尾有空白：{v!r}"
        assert "  " not in v, f"{name} 有连续空格：{v!r}"


def test_status_line_has_no_leading_space():
    """R43 的原始症状：状态行是 ` 任务完成`。"""
    title_field = _wxmp_payload()["title"]["value"]
    assert not title_field.startswith(" "), f"状态行仍有前导空格：{title_field!r}"
    assert title_field.startswith("WorkBuddy"), title_field


def test_map_wxmp_data_covers_fields_not_in_role_hints():
    """没有语义的变量名（`keyword1`）也要过一遍——收口在「值」这一侧，不挑字段名。"""
    values = channels._wxmp_values(
        "🔔 测试", "内容 ✅ 带表情", {}, "2026年10月30日 21:00",
        wrap=False, tpl_content="",
    )
    out = channels._map_wxmp_data(["keyword1", "keyword2"], values)
    for name, item in out.items():
        assert not _leftovers(item["value"]), f"{name} 漏了：{item['value']!r}"
        assert item["value"] == item["value"].strip()


# ------------------------------------------------- 只动微信：别的通道保留 emoji


def test_bark_keeps_emoji_in_title(monkeypatch):
    """bark 客户端能正常渲染 emoji，标题必须原样发出去。"""
    sent: dict = {}

    def fake_post(url, body, timeout):
        sent["url"] = url
        sent["body"] = body
        return 200, '{"code": 200, "message": "success"}'

    monkeypatch.setattr(channels, "_post_json", fake_post)
    ok, _ = channels._send_bark(
        {"key": "k" * 20}, "✅ WorkBuddy 任务完成", "已修好", 5
    )
    assert ok
    assert sent["body"]["title"] == "✅ WorkBuddy 任务完成", "bark 的标题不该被剥 emoji"


def test_plain_text_keeps_emoji():
    """纯文本通道（webhook / smtp / 企业微信）也不剥 emoji。"""
    assert channels.plain_text("✅ 完成", "🎉 全部通过") == "✅ 完成\n\n🎉 全部通过"


# ------------------------------------------------- 过度剥离守卫（R45 的教训）


@pytest.mark.parametrize(
    "src",
    [
        "25°C 有点热",          # ° 是 So，但在 Latin-1 里，是排版符号不是表情
        "© 2026 WorkBuddy",     # © 也是 So
        "a ^ b ~ c `d`",        # ^ ~ ` 是 Sk，代码里天天出现
        "版本 1.2.3 已发布",     # 数字和点号
        "if a | b: pass",       # 竖线是 Sm，不是 Sk
    ],
)
def test_ordinary_symbols_are_left_alone(src):
    assert channels._tidy(src) == src


def test_strip_is_idempotent():
    once = channels._tidy("✅ 完成 🎉")
    assert channels._tidy(once) == once


# ------------------------------------------------- 剥除边界（R49）


def test_boundary_is_latin1_not_emoji():
    """边界是 `So/Sk 且 ord > 0xFF`，代价是 `℃ ™ №` 一起消失。

    **这是取舍不是 bug。要改先读 roadmap R43 / R45（边界登记在 R49）。**

    `0xFF` 是 **Latin-1 的上界，不是表情的下界**，所以这条测试要双向咬：

    | 输入 | 期望 | 钉住什么 |
    |---|---|---|
    | `25°C` | `25°C` | `°`(U+00B0 So) 在边界内 → 保留。删掉 `ord > 0xFF` 这条就红 |
    | `25℃` | `25` | `℃`(U+2103 So) 越过边界 → 跟着 emoji 走。改成白名单/码位表可能让它变绿 |
    | `ACM™` | `ACM` | 同上，`™`(U+2122 So) |
    | `№3` | `3` | 同上，`№`(U+2116 So) |

    前一行防「放宽」，后三行防「收窄」——只写前一行的话，把判据换成
    「只剥 U+1F300–U+1FAFF 这段表情区」也能全绿，而每年新出的表情会漏一批。
    后三行同时也是**已知损失的台账**：它们红说明边界被动了，不是发现新 bug。
    """
    assert channels._tidy("25°C") == "25°C", "° 在 Latin-1 内，必须保留，否则 25°C 变 25C"
    assert channels._tidy("25℃") == "25", "℃ 越过 0xFF，跟 emoji 一起剥（已知损失，不是 bug）"
    assert channels._tidy("ACM™") == "ACM", "™ 同上（已知损失）"
    assert channels._tidy("№3") == "3", "№ 同上（已知损失）"


def test_latin1_symbols_survive_the_boundary():
    """0x00–0xFF 里恰好 10 个 So/Sk，全是排版符号，一个都不许剥。

    这条把 `ord > 0xFF` 那半句的**理由**钉住：不是随手挑的 255，是
    Latin-1 的上界。10 个字符逐个列出来，多一个少一个都会红。
    """
    keep = "^`¦¨©®¯°´¸"
    assert len(keep) == 10
    for ch in keep:
        assert unicodedata.category(ch) in ("So", "Sk"), f"{ch!r} 不该在这一组"
        assert ord(ch) <= 0xFF, f"{ch!r} 越过了 Latin-1 上界"
        assert channels._tidy(f"a{ch}b") == f"a{ch}b", f"{ch!r} 被剥掉了"

    # `~` 是 Sm 不是 Sk：它不被剥跟这条边界无关，别把它算进来当理由
    assert unicodedata.category("~") == "Sm"

