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


# ---------------------------------------------------------------- W16 §2 下划线
#
# 原来 `summarize()` 里是 `re.sub(r"[*_`#>]+", "", flat)`，把下划线当 markdown
# 一起清了。症状在手机上：`is_our_hook` 显示成 `isourhook`、`_our_hook_exe_paths()`
# 显示成 `ourhookexepaths()`。任何涉及文件名 / 函数名的通知都是乱码级可读性。
#
# 修法是把 `_` 从字符类里摘出来，但成对的 `_斜体_` 还得剥——所以先单独处理
# 成对的，再清其它标记，顺序不能反。


def test_identifiers_keep_their_underscores():
    """标识符里的下划线原样保留，一个都不许变形。"""
    text = "修复了 is_our_hook 判断，新增 _our_hook_exe_paths()"
    out = summarize(text, 200)

    assert out == text, f"标识符被改坏了：{out!r}"
    assert "isourhook" not in out, out
    assert "ourhookexepaths" not in out, out


def test_paired_underscores_are_still_italics():
    """成对的下划线是斜体标记，仍然要剥——但只剥标记，不剥内容。"""
    assert summarize("这是 _斜体_ 文字", 200) == "这是 斜体 文字"


def test_other_markers_still_go():
    """`*` / 反引号 / `#` / `>` 照旧清掉，别因为动了 `_` 就把它们漏掉。

    期望值里 `标题` 前面是**两个空格**：`#` 被剥掉后原位留空，这是
    `test_plain_text_is_untouched` 钉住的既有行为，W16 不动它。
    """
    out = summarize("**粗体** 和 `代码` 和 # 标题", 200)

    assert out == "粗体 和 代码 和  标题", out
    for mark in ("*", "`", "#", ">"):
        assert mark not in out, f"{mark} 没清掉：{out!r}"


# ---------------------------------------------------------------- W16 §3/§5 卡片摘要
#
# `summarize()` 是 11 个通道共用 + 关键词过滤器的匹配源，一个字都不改。
# 卡片摘要（`summarize_for_card`）是独立的抽取式函数，只在微信切段之前调用：
# 选句子、不改写、不调模型——hook 只有 1.5 秒预算，而且通知不能有幻觉。

from wbn import channels  # noqa: E402
from wbn.events import _card_width, summarize_for_card  # noqa: E402

# 任务书 §0 那段输入：AI 干完一堆事后的典型回复
CARD_INPUT = """好的，我已经完成了你要求的全部改动。具体来说，我做了以下几件事：
1. 把 `install.py` 里的 `is_our_hook` 改成支持 onedir 路径
2. 新增 `_our_hook_exe_paths()` 返回候选列表
3. 修掉了 `or "wbn" in cmd` 的误删问题
4. 补了 4 条单元测试
5. 重新打包并验证

现在所有 675 个用例都通过了，CI 也绿了。
"""


def test_short_text_comes_back_untouched():
    """短文本（< 68 半角）原样返回——本来装得下，加工只会引入噪声。"""
    text = "已推送，CI 绿了。"
    assert summarize_for_card(text, 68) == text


def test_empty_text_returns_empty_string():
    """空 / 全空白返回 `""`，**不继承** `summarize()` 的「（无摘要）」。"""
    assert summarize_for_card("", 68) == ""
    assert summarize_for_card("   \n\t  ", 68) == ""


def test_all_polite_still_returns_something():
    """全是客套话、没有结论时兜底取首句——不许空手。"""
    text = "好的，我来帮你看看。" * 6
    out = summarize_for_card(text, 68)

    assert out, "全是客套话时返回空了"
    assert out.startswith("好的"), f"兜底应该是首句：{out!r}"


def test_conclusion_survives_the_budget():
    """§5 硬判据：结论必须进卡片，客套必须滚出去。"""
    summary = summarize(CARD_INPUT, 200)          # 真实链路：先过 summarize
    out = summarize_for_card(summary, 144)        # 4 槽 × 36 宽

    assert any(k in out for k in ("675", "CI", "通过")), f"结论没进卡片：{out!r}"
    for polite in ("好的", "我来", "具体来说", "以下几件事"):
        assert polite not in out, f"客套话没去掉：{polite} → {out!r}"
    assert len(out) >= 40, f"压得太干，四行只有 {len(out)} 字：{out!r}"


def test_identifiers_inside_the_card_are_intact():
    """标识符一旦进卡片就必须原样——下划线不许被当成 markdown 吃掉。

    144 的预算下这段输入的标识符（在第 1、2 条步骤里）落在预算之外，
    所以这里把预算放宽到 200，专门验「进得去的时候不变形」。
    """
    summary = summarize(CARD_INPUT, 200)
    out = summarize_for_card(summary, 200)

    assert "is_our_hook" in out, f"标识符没进卡片：{out!r}"
    assert "isourhook" not in out, f"标识符被改坏：{out!r}"
    assert "_our_hook_exe_paths" in out, f"带下划线的标识符被改坏：{out!r}"


def test_card_summary_respects_the_budget():
    """拼出来的文本不许超预算——超了就是下游白折行。"""
    out = summarize_for_card(CARD_INPUT, 100)
    assert _card_width(out) <= 100, f"超预算：{_card_width(out)} > 100"


def test_card_width_matches_channels():
    """events 里的宽度尺子必须和 channels 同一口径，否则预算算不准。"""
    for s in ("中文混排 abc 123", "675 个用例通过", "CI 也绿了。"):
        assert _card_width(s) == channels._width(s), s


def test_error_detail_is_not_compressed():
    """失败类的 `detail` 是错误原因，**不许走摘要压缩**——压掉半句就没法排查。"""
    err = "好的，我先说明一下。真正的错误是 ERR_CONN_REFUSED 端口 7897 不通。已经重试 3 次。"
    values = channels._wxmp_values(
        "❌ 任务失败",
        err,
        {"kind": "task_failed", "error": err, "project": "demo"},
        "2026年11月1日 00:00",
        wrap=True,
        tpl_content=(
            "状态：{{title.DATA}}\n项目：{{project.DATA}}\n详情：{{detail.DATA}}\n"
            "补充：{{detail2.DATA}}\n补充：{{detail3.DATA}}\n补充：{{detail4.DATA}}\n"
            "时间：{{time.DATA}}"
        ),
    )
    joined = "".join(values[k] for k in ("detail", "detail2", "detail3", "detail4"))

    assert "好的" in joined, f"错误原因被当成摘要压了：{joined!r}"
    assert "ERR_CONN_REFUSED" in joined, joined
