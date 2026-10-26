# -*- coding: utf-8 -*-
"""详情折行 / 卡片宽度回归。

背景：微信模板消息的字段值在卡片里是**定宽一行**，微信不会替我们折行，
超出部分从字符中间硬切。所以行宽、断点、省略号位置全部由
`channels._take_width` / `_wrap_detail` 自己保证。

原来这些检查只存在于 `selftest.py check_wrap()` 的 print 里，人眼看 ✓/✗，
CI 判定不了成败。这里逐条改成 assert。
"""

from __future__ import annotations

import pytest

from wbn import channels

WIDTH, MAXL = 34, 4


def _w(text: str) -> int:
    return sum(channels._char_w(c) for c in text)


# ---------------------------------------------------------------- _char_w


@pytest.mark.parametrize(
    "ch,expected",
    [
        ("a", 1),      # 半角字母
        ("1", 1),      # 半角数字
        ("中", 2),     # 汉字 = Wide
        ("，", 2),     # 全角逗号 = Fullwidth
        ("。", 2),     # 句号 = Wide
        ("…", 2),      # 省略号 = Ambiguous，按全角算，所以「…」吃掉 2 格
    ],
)
def test_char_width_matches_card_metrics(ch, expected):
    """中文 2 格、英文 1 格——按 len() 折行会让含英文的行短一截。"""
    assert channels._char_w(ch) == expected


def test_char_width_of_mixed_line_equals_sum():
    # 修好(4) + 空格(1) + hook(4) + .(1) + py(2) + ，(2) + 中文正常(8) = 22
    assert _w("修好 hook.py，中文正常") == 22


# ---------------------------------------------------------------- _take_width


def test_take_width_returns_whole_text_when_it_fits():
    assert channels._take_width("短句", WIDTH) == "短句"


def test_take_width_never_exceeds_budget():
    for budget in (1, 5, 12, 34, 100):
        got = channels._take_width("这是一段很长的中文和 english words 混排", budget)
        assert _w(got) <= budget


def test_take_width_breaks_at_punctuation_not_inside_word():
    """核心需求：sys.stdin.buffer 断成 sys.stdin. / buffer，而不是 bu / ffer。"""
    got = channels._take_width("sys.stdin.buffer instead", 12)
    assert got == "sys.stdin."
    assert not got.endswith("bu")


def test_take_width_counts_cjk_as_two_cells():
    # 4 格只放得下两个汉字，第三个字符是半角 a 也塞不进（2+2+1 > 4）
    assert channels._take_width("中文abc", 4) == "中文"


def test_take_width_empty_and_zero_budget():
    assert channels._take_width("", WIDTH) == ""
    assert channels._take_width("abc", 0) == ""


# ---------------------------------------------------------------- _wrap_detail

CASES = [
    ("短句", "已修好编码问题。", 1),
    ("中等", "已修好编码问题，现在通知能正常送达，中文不会再变成乱码。", 2),
    (
        "长文",
        "已修好编码问题，现在通知能正常送达，中文不会再变成乱码，"
        "详情也会按卡片宽度自动折行显示。另外还修了模板变量缺冒号的问题。",
        2,
    ),
    ("超长", "这是一段非常长的内容" * 12, MAXL),
    (
        "中英",
        "Fixed the UnicodeEncodeError in hook.py by reading "
        "sys.stdin.buffer instead of sys.stdin. 现在中文正常了。",
        2,
    ),
    ("无标点", "这是一段完全没有标点符号的连续中文文本用来测试硬切断行会不会浪费空间", 2),
]


@pytest.mark.parametrize("name,text,min_lines", CASES, ids=[c[0] for c in CASES])
def test_every_line_fits_the_card_width(name, text, min_lines):
    """行宽不溢出：每行（含带省略号的末行）都不得超过卡片可用宽度。"""
    lines = channels._wrap_detail(text, WIDTH, MAXL).split("\n")
    for i, line in enumerate(lines, 1):
        assert _w(line) <= WIDTH, f"{name} 第 {i} 行宽 {_w(line)} 超出 {WIDTH}：{line!r}"


@pytest.mark.parametrize("name,text,min_lines", CASES, ids=[c[0] for c in CASES])
def test_line_count_within_limits(name, text, min_lines):
    lines = channels._wrap_detail(text, WIDTH, MAXL).split("\n")
    assert len(lines) <= MAXL, f"{name} 折出 {len(lines)} 行，超过上限 {MAXL}"
    assert len(lines) >= min_lines, f"{name} 只折了 {len(lines)} 行，至少应有 {min_lines} 行"


@pytest.mark.parametrize("name,text,min_lines", CASES, ids=[c[0] for c in CASES])
def test_no_line_starts_with_closing_punctuation(name, text, min_lines):
    """行首禁则：逗号句号不能落到行首，折到行首就该被吞掉。"""
    for line in channels._wrap_detail(text, WIDTH, MAXL).split("\n"):
        assert line, f"{name} 折出了空行"
        assert line[0] not in channels._NO_LINE_START, f"{name} 行首是标点：{line!r}"


@pytest.mark.parametrize("name,text,min_lines", CASES, ids=[c[0] for c in CASES])
def test_ellipsis_only_appears_on_last_line(name, text, min_lines):
    """省略号只在末行——中间行带省略号会让读者以为内容断了。"""
    lines = channels._wrap_detail(text, WIDTH, MAXL).split("\n")
    for i, line in enumerate(lines, 1):
        if "…" in line:
            assert i == len(lines), f"{name} 第 {i} 行就有省略号，但还有后续行"


def test_long_text_is_truncated_with_ellipsis_on_last_line():
    lines = channels._wrap_detail("内容" * 60, WIDTH, MAXL).split("\n")
    assert len(lines) == MAXL
    assert lines[-1].endswith("…")


def test_english_token_is_not_split_mid_word():
    """回归：sys.stdin.buffer 不能被劈成 sys.stdin.bu / ffer。"""
    text = (
        "Fixed the UnicodeEncodeError in hook.py by reading "
        "sys.stdin.buffer instead of sys.stdin."
    )
    lines = channels._wrap_detail(text, WIDTH, MAXL).split("\n")
    for i in range(len(lines) - 1):
        assert not (lines[i].endswith("bu") and lines[i + 1].startswith("ffer")), (
            f"第 {i + 1}/{i + 2} 行把 buffer 劈开了：{lines[i]!r} | {lines[i + 1]!r}"
        )
    joined = "\n".join(lines)
    assert "UnicodeEncodeError" in joined, "长单词不该被拆散"


def test_empty_input_produces_no_lines():
    """空串不能折出一个空行——那会在卡片里多占一行高度。"""
    for blank in ("", "   ", "\n\n", " 　\t "):
        assert channels._wrap_detail(blank, WIDTH, MAXL) == ""
        assert channels._wrap_detail(blank, WIDTH, MAXL).splitlines() == []


def test_default_width_fits_the_wechat_card():
    """默认参数就是生产路径用的值（`_wxmp_values` 不传 width）。

    微信卡片一行 42 个半角位，减掉「详情：」标签，值区域不超过 34 格。
    把这个默认值改大 = 手机上一行被微信从中间硬切。
    """
    lines = channels._wrap_detail("内容" * 60).split("\n")
    assert len(lines) <= 4
    for line in lines:
        assert _w(line) <= 34, f"默认宽度下第 {lines.index(line) + 1} 行超宽：{_w(line)}"


def test_single_line_budget_still_terminates():
    lines = channels._wrap_detail("一二三四五六七八九十" * 5, WIDTH, 1).split("\n")
    assert len(lines) == 1
    assert lines[0].endswith("…")


# ------------------------------------------------- 多槽位切分：不重复不丢失

TPL_4_SLOTS = (
    "状态：{{title.DATA}}\n"
    "项目：{{project.DATA}}\n"
    "详情：{{detail.DATA}}\n"
    "补充：{{detail2.DATA}}\n"
    "补充：{{detail3.DATA}}\n"
    "补充：{{detail4.DATA}}\n"
    "时间：{{time.DATA}}"
)

# 53 个汉字、无标点、无空格：切片是纯几何操作，拼接后必须与原文逐字相同
LONG_NO_PUNCT = (
    "这是一段完全没有标点符号的连续中文文本用来测试硬切断行会不会浪费空间"
    "并且检查多槽位切分是否重复丢失内容啊啊"
)


def _slots(content: str, wrap: bool = True) -> dict:
    values = channels._wxmp_values(
        "标题", content, {"kind": "task_done", "project": "demo"},
        "2026年10月26日 21:00", wrap=wrap, tpl_content=TPL_4_SLOTS,
    )
    return {k: values[k] for k in ("detail", "detail2", "detail3", "detail4")}


def test_multislot_split_neither_duplicates_nor_loses_content():
    segs = list(_slots(LONG_NO_PUNCT).values())
    assert "".join(segs) == LONG_NO_PUNCT, f"拼接后与原文不一致：{segs!r}"


def test_multislot_split_fills_slots_in_order():
    segs = list(_slots(LONG_NO_PUNCT).values())
    nonempty = [s for s in segs if s]
    assert len(nonempty) >= 3, f"53 个字应占满 3 个以上槽位，实际 {nonempty!r}"
    # 相邻槽位不得重复同一段内容
    for a, b in zip(nonempty, nonempty[1:]):
        assert not b.startswith(a[-6:]), f"槽位内容重复：{a[-6:]!r} 出现在两个槽位里"


def test_short_detail_leaves_trailing_slots_empty():
    """短文本不该被复制到 detail2/3/4 去凑行数。"""
    segs = _slots("已修好编码问题。")
    assert segs["detail"] == "已修好编码问题。"
    assert segs["detail2"] == segs["detail3"] == segs["detail4"] == ""


def test_wrap_off_fills_only_first_slot():
    """wrap=False 时只填第一个详情变量，其余留空。"""
    segs = _slots(LONG_NO_PUNCT, wrap=False)
    assert segs["detail"]
    assert segs["detail2"] == segs["detail3"] == segs["detail4"] == ""
