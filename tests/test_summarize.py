"""摘要：markdown 表格不许原样推给用户。

真机截图暴露的（`logs/notifications.log` 2026-10-31 16:46:31）：

    收到消息，README:91 的 ✅ 保留，不回滚。  常开策略：已经是「由用户决定」 | 位置 | 现状 | 判定 | |---|---|---| | config.example.json | clawbot.enabled = false | 新用户装完不会被强开 ✓ | …

三行表格把 200 字预算吃掉近 1/3，而且 `|---|---|---|` 对微信 / ClawBot /
Bark / ntfy 这些**纯文本通道**就是纯噪声——它们不渲染 markdown。

第 3 条是**防修过头**：`summarize()` 被 11 个通道共用，改坏了全崩。
它的期望值是**改之前**跑出来的硬编码，不是「跑一遍看输出是什么就抄什么」。
"""

from __future__ import annotations

from wbn.events import summarize

TABLE_SAMPLE = """收到消息，README:91 的 ✅ 保留，不回滚。

| 位置 | 现状 | 判定 |
|---|---|---|
| `config.example.json` | `enabled = false` | 新用户不会被强开 ✓ |
"""


def test_table_pipes_are_gone():
    """输出里不许再有竖线，也不许再有 `---` 分隔行。"""
    out = summarize(TABLE_SAMPLE, 200)

    assert "|" not in out, f"表格竖线还在：{out!r}"
    assert "---" not in out, f"分隔行还在：{out!r}"


def test_table_cells_become_separators():
    """单元格之间用 ` · ` 连，谁对谁还能看出来。"""
    out = summarize(TABLE_SAMPLE, 200)

    assert "位置 · 现状 · 判定" in out, out
    assert "config.example.json · enabled = false · 新用户不会被强开 ✓" in out, out


# 期望值取自**改前**的 `summarize()`（W12 开工时先跑一遍存下来的），
# 不是改完再抄。它同时钉住三件事：空白折叠、修饰符清理、链接去壳。
_PLAIN_INPUT = """**项目**：Work buddy 通知
**耗时**：1 分 46 秒

已完成 `pipeline.py` 的改造，见 [设计文档](https://example.com/x)。
第二行   有多个连续空格。
> 引用一行 # 井号一个"""

_PLAIN_EXPECTED = (
    "项目：Work buddy 通知 耗时：1 分 46 秒 已完成 pipeline.py 的改造，见 设计文档。"
    " 第二行 有多个连续空格。  引用一行  井号一个"
)


def test_plain_text_is_untouched():
    """没有表格的纯文本，输出必须与改前**逐字节相同**。

    注意 `_PLAIN_EXPECTED` 里那两个连续空格：`> 引用一行 # 井号一个` 先做
    空白折叠、后删修饰符，所以 `>` 和 `#` 的位置留下两个空格。看着丑，但那是
    **既有行为**——本轮只解决表格，顺手「美化」它就是在改 11 个通道的正文。
    """
    assert summarize(_PLAIN_INPUT, 200) == _PLAIN_EXPECTED


def test_code_fence_markers_are_dropped():
    """``` 围栏标记行整行丢掉，围栏里的内容照常保留。"""
    text = "看这段：\n```python\nprint(1)\n```\n结束"
    out = summarize(text, 200)

    assert "```" not in out, out
    assert "print(1)" in out, "围栏标记要丢，内容不许跟着丢"
    assert "结束" in out
