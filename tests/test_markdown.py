"""markdown → 纯文本：`channels.plain_text` 的剥离规则。

背景（R45）：2026-10-29 真机通知的「补充」两行是 `…： |` 和
`手机上详情显示 | 结论 || --- | |…`——`plain_text` 当时只剥了 `**粗体**`，
表格、列表、标题、代码围栏全原样发到手机上。

**这条测试守两个方向**：
- 该剥的必须剥（9 类语法）
- 不该剥的一个字都不许动（`2 * 3`、`a | b`）——过度剥离和漏剥离一样是 bug
"""

from __future__ import annotations

import inspect
import re

import pytest

from wbn import channels

# 9 类语法，每类一条 input → expected
TABLE = [
    ("表格分隔行", "| --- | --- |", ""),
    ("表格数据行", "| 手机上详情显示 | 结论 |", "手机上详情显示 · 结论"),
    ("列表", "- 第一项\n* 第二项\n+ 第三项\n1. 第四项",
     "· 第一项\n· 第二项\n· 第三项\n· 第四项"),
    ("标题", "## 小标题", "小标题"),
    ("代码块围栏", "```bash\nrm -rf build/\n```", "rm -rf build/"),
    ("行内代码", "跑 `pytest -q` 就行", "跑 pytest -q 就行"),
    ("链接", "[文档](https://a.b/c)", "文档"),
    ("图片", "![构建截图](https://a.b/x.png)", "构建截图"),
    ("粗体/斜体/删除线", "**项目**：*斜体* ~~删除~~", "项目：斜体 删除"),
]


@pytest.mark.parametrize("name,src,expected", TABLE, ids=[t[0] for t in TABLE])
def test_markdown_syntax_is_stripped(name, src, expected):
    assert channels.plain_text("", src) == expected


# ---------------------------------------------------------------- 不过度剥离


@pytest.mark.parametrize(
    "src",
    [
        "2 * 3",                 # 乘号，不是斜体
        "2 * 3 * 4",             # 两个星号也不许当斜体吃掉
        "a | b",                 # 正文竖线，不是表格行
        "if a | b: pass",        # 同上，行首也不是竖线
        "版本 1.2.3 已发布",      # 有序列表要求「数字 + 点 + 空格」在行首
        "见 #3 号问题",           # 井号不在行首
    ],
)
def test_ordinary_text_is_left_alone(src):
    """过度剥离和漏剥离一样是 bug——这些是正文，不是 markdown。"""
    assert channels.plain_text("", src) == src


def test_table_row_without_leading_pipe_is_not_a_table():
    """`|` 必须在行首行尾同时出现才算表格行，否则正文竖线会被换成间隔号。"""
    assert channels.plain_text("", "结果：a | b") == "结果：a | b"


# ---------------------------------------------------------------- 真实样本


def test_real_notification_table_becomes_readable():
    """2026-10-29 15:15 那条真机通知的原文——修之前手机上就是这一串乱码。"""
    src = (
        "手机上详情显示 | 结论\n"
        "|---|---|\n"
        "| 三行 | 分别填 detail / detail2 / detail3 |\n"
        "| 一行 | 只能显示第一行 |"
    )
    got = channels.plain_text("", src)

    assert "|" not in got, f"竖线没清干净：{got!r}"
    assert "---" not in got, f"分隔行没丢掉：{got!r}"
    assert "三行 · 分别填 detail / detail2 / detail3" in got


def test_title_and_content_keep_the_blank_line_between_them():
    """标题和正文之间的空行要留着——纯文本通道靠它分段。"""
    got = channels.plain_text("标题", "正文第一行\n正文第二行")
    assert got == "标题\n\n正文第一行\n正文第二行"


def test_empty_title_does_not_leave_leading_blank_lines():
    assert channels.plain_text("", "正文") == "正文"
    assert channels.plain_text("", "") == ""


def test_flatten_collapses_the_stripped_result_to_one_line():
    """`_flatten` 是 wxmp 的入口：剥完再压成单行，表格不能留下空档。"""
    flat = channels._flatten("| a | b |\n|---|---|\n- 第一项")
    assert flat == "a · b · 第一项"


# ---------------------------------------------------------------- 唯一出处


def test_render_py_has_no_second_markdown_rule():
    """R36 的收口：markdown 规则只许在 `plain_text` 一处。

    这里不用 `grep re.sub`（render.py 里可能用正则做别的正经事），而是查
    「有没有哪个正则字面量在处理 markdown 标记」——`**` / `` ` `` / `|`
    出现在正则里就是又长出了一套规则。
    """
    src = inspect.getsource(__import__("wbn.render", fromlist=["x"]))
    literals = re.findall(r"re\.(?:sub|compile|match|search)\(\s*r?[\"'](.+?)[\"']", src)
    offenders = [p for p in literals if "**" in p or "`" in p or "|" in p]
    assert not offenders, f"render.py 里又出现了 markdown 正则：{offenders}"
