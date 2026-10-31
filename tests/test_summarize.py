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


# ---------------------------------------------------------------- W17 排序权重
#
# W16 的打分是「有数字就 +2」，于是中文技术回复里的**计数**（`4 条测试`、
# `3 个文件`）压过了真正的**实质改动**（`install.py`、`is_our_hook`、`onedir`）。
# 实测 3 槽卡片第一行是「· 补了 4 条单元测试 5.」，最要紧的那条改动一个字没进去。
#
# 新权重：拉丁标识符 +3 > 结论词 +2 > 中文数量短语 +1 > 其余数字 0。

from wbn.events import _card_density  # noqa: E402

_TPL_3 = (
    "状态：{{title.DATA}}\n项目：{{project.DATA}}\n详情：{{detail.DATA}}\n"
    "补充：{{detail2.DATA}}\n补充：{{detail3.DATA}}\n时间：{{time.DATA}}"
)
_TPL_4 = _TPL_3.replace("时间：{{time.DATA}}", "补充：{{detail4.DATA}}\n时间：{{time.DATA}}")


def _card_slots(tpl: str, slots: int) -> list[str]:
    """按模板声明的槽位数渲染卡片，返回 detail..detailN。"""
    summary = summarize(CARD_INPUT, 200)
    fields = {
        "kind": "task_done", "kind_label": "任务完成", "emoji": "✅",
        "title": "✅ WorkBuddy 任务完成", "project": "Work buddy 通知",
        "duration": "1 分 46 秒", "summary": summary, "error": "",
        "host": "XingHan", "date": "2026-11-01",
    }
    v = channels._wxmp_values(
        fields["title"], CARD_INPUT, fields, "00:20", wrap=True, tpl_content=tpl
    )
    return [v[k] for k in ("detail", "detail2", "detail3", "detail4")[:slots]]


def test_density_prefers_identifiers_over_counts():
    """标识符 3 分、结论词 2 分、中文数量短语 1 分、孤立数字 0 分。"""
    # 三条实质改动：install.py / is_our_hook / onedir
    assert _card_density("1. 把 install.py 里的 is_our_hook 改成支持 onedir 路径") == 3
    # 带下划线的标识符从 `o` 开始匹配，照样算
    assert _card_density("2. 新增 _our_hook_exe_paths() 返回候选列表") == 3
    # 只有计数：降权到 1，**不删**
    assert _card_density("4. 补了 4 条单元测试") == 1
    # 结论词（「验证」是 W17 新加的）+ 无标识符无数量
    assert _card_density("5. 重新打包并验证") == 2
    # 孤立的 675 不额外加分，靠结论词拿分
    assert _card_density("现在所有 675 个用例都通过了，CI 也绿了。") == 3


def test_three_slot_card_shows_a_real_change():
    """§2.1 硬判据：真实 3 槽模板下必须看到实质改动 + 结论，且首行不是计数句。"""
    segs = _card_slots(_TPL_3, 3)
    joined = "".join(segs)

    ids = ("is_our_hook", "onedir", "install.py", "_our_hook_exe_paths", "wbn")
    assert any(k in joined for k in ids), f"三行里没有实质改动标识符：{segs}"
    assert any(k in joined for k in ("675", "通过", "CI", "绿")), f"三行里没有结论：{segs}"
    assert any(k in segs[0] for k in ids), f"第一行是纯计数句：{segs[0]!r}"
    assert len(joined) >= 40, f"三行只有 {len(joined)} 字：{segs}"


def test_four_slot_card_does_not_regress():
    """§2.2：4 槽不许退化——`is_our_hook` 仍在第一行，675 / CI 仍在。

    注：任务书说 4 槽结尾是完整的「CI 也绿了。」，但这台机器上**改前就已经**
    是 `CI…`（见 W17 报告里的基线）。原因是 `summarize()` 先把换行压平，
    「5. 重新打包并验证」和结论粘成一句，比参考实现长了 19 个半角位。
    这一条不属于本轮的退化，所以只钉「不许更差」：675 和 CI 必须在。
    """
    segs = _card_slots(_TPL_4, 4)
    joined = "".join(segs)

    assert "is_our_hook" in segs[0], f"第一行丢了 is_our_hook：{segs[0]!r}"
    assert "675" in joined, segs
    assert "CI" in joined, segs


def test_chinese_only_card_is_not_degenerate():
    """§2.3-1：一个标识符都没有时，数量短语就是唯一信号，按 +1 正常补位。"""
    text = (
        "我把界面重新排了一遍，改了 3 个按钮的位置。调了 2 处间距，又补了 5 行说明文字。"
        "最后整体又看了一遍效果，顺手把间距统一了一下，现在看着顺眼多了。"
    )
    out = summarize_for_card(summarize(text, 200), 108)

    assert "3 个按钮" in out, f"数量短语没进卡片：{out!r}"
    assert len(out) >= 40, f"退化成干巴巴一句了：{out!r}"


def test_identifiers_without_conclusion_use_last_sentence():
    """§2.3-2：全是标识符没有结论时，按原文取末句，不许空手。"""
    text = (
        "修改了 install.py 的 _resolve_root()。新增 wbn/paths.py 文件。"
        "调整 config.py 里的 DEFAULT_CONFIG 字段。更新 tests/test_config.py 用例。"
    )
    out = summarize_for_card(summarize(text, 200), 108)

    assert out, "空手了"
    assert "install.py" in out, f"标识符没保留：{out!r}"


# ---------------------------------------------------------------- W18 末行不许被截
#
# W17 把排序修对了，但真实模板（3 槽 width=36）下末行还是 `…`：
# `现在所有 675 个用例都通过了，CI…`——「也绿了」没了。
#
# 根因是**预算算的是名义宽度**：`summarize_for_card` 按半角宽度总和挑句，
# 认为 3 × 36 = 108 就装得下；而 `_wrap_detail` 真折行时行尾会白扔十几个半角位
# （`_take_width` 宁可短行，也要断在词后面）。实测 101 个半角的内容只落得下 92。
#
# **判据必须打在 `_wxmp_values` 上，不能打在 `summarize_for_card` 上**——
# 截断发生在折行那一步，只测摘要根本测不出来（摘要本身一个字都没少）。

import pytest  # noqa: E402

# `width = _CARD_WIDTH(42) - _label_width(标签)`，所以换标签就能造出指定宽度，
# 不必去改 `_CARD_WIDTH`。
_LABEL_BY_WIDTH = {36: "详情：", 34: "详情栏：", 38: "详：", 24: "详细信息（补充）："}


def _tpl_for(slots: int, width: int) -> str:
    """造一个「详情槽 = slots、值区宽 = width」的模板。"""
    lab = _LABEL_BY_WIDTH[width]
    lines = ["状态：{{title.DATA}}", "项目：{{project.DATA}}", f"{lab}{{{{detail.DATA}}}}"]
    for i in range(2, slots + 1):
        lines.append(f"{lab}{{{{detail{i}.DATA}}}}")
    lines.append("时间：{{time.DATA}}")
    return "\n".join(lines)


def _render_card(slots: int, width: int) -> list[str]:
    """按 (槽位数, 值区宽度) 渲染一张真实卡片，返回 detail..detailN。"""
    summary = summarize(CARD_INPUT, 200)
    fields = {
        "kind": "task_done", "kind_label": "任务完成", "emoji": "✅",
        "title": "✅ WorkBuddy 任务完成", "project": "Work buddy 通知",
        "duration": "1 分 46 秒", "summary": summary, "error": "",
        "host": "XingHan", "date": "2026-11-01",
    }
    v = channels._wxmp_values(
        fields["title"], CARD_INPUT, fields, "00:30", wrap=True,
        tpl_content=_tpl_for(slots, width),
    )
    return [v[k] for k in ("detail", "detail2", "detail3", "detail4")[:slots]]


@pytest.mark.parametrize(
    "slots,width", [(1, 36), (2, 36), (3, 36), (4, 36), (3, 24), (3, 38)]
)
def test_last_line_is_never_ellipsised(slots: int, width: int):
    """§3.1 表驱动：六组里末行都不许出现 `…`。

    唯一例外是 `slots=1`——一个详情槽只装得下一行，而结论句本身就有 58 个
    半角，超预算是必然的。那一格只断言「截断后结论关键词还在」。
    """
    segs = _render_card(slots, width)
    joined = "".join(segs)
    last = segs[-1]

    if slots > 1:
        assert not last.endswith(channels._ELLIPSIS_CHAR), (
            f"{slots} 槽 width={width} 末行被截：{segs}"
        )
    # 无论截不截，结论都得看得见
    assert any(k in joined for k in ("675", "通过", "CI")), f"结论丢了：{segs}"


def test_real_template_three_slots_is_complete():
    """§3.2 真实模板（3 槽 width=36）：末行完整，且 L1 不许退回计数句。

    这一条同时钉住 W17 的成果——`· 新增 _our_hook_exe_paths()` 必须还在第一行。
    """
    segs = _render_card(3, 36)
    joined = "".join(segs)

    assert not segs[-1].endswith(channels._ELLIPSIS_CHAR), f"末行还是被截：{segs}"
    assert sum(k in joined for k in ("675", "通过", "CI")) >= 2, f"结论不够：{segs}"

    ids = ("is_our_hook", "onedir", "install.py", "_our_hook_exe_paths", "wbn")
    assert any(k in segs[0] for k in ids), f"L1 退化成计数句了：{segs[0]!r}"


def test_four_slots_stay_complete():
    """§3.3 4 槽不许退化：`is_our_hook` 仍在 L1、末行完整、`CI 也绿了` 还在。"""
    segs = _render_card(4, 36)
    joined = "".join(segs)

    assert "is_our_hook" in segs[0], f"L1 丢了 is_our_hook：{segs[0]!r}"
    assert not segs[-1].endswith(channels._ELLIPSIS_CHAR), f"末行被截：{segs}"
    assert "CI 也绿了" in joined, f"结论不完整：{segs}"


def test_tight_wrap_is_only_the_fallback():
    """兜底的 0.95 断句阈值只许走重试那条路，不许改成默认。

    默认 0.7 是「宁可短行，读起来连贯」——那是所有卡片每天都在走的路，
    为了几格截断把它整体收紧，等于让每张卡片都断在中文词中间。
    """
    import inspect

    sig = inspect.signature(channels._wrap_detail)
    assert sig.parameters["break_at"].default == 0.7, "兜底阈值被改成默认了"
    assert channels._TIGHT_BREAK_AT > 0.7, "兜底阈值没有比默认更紧"


# ---------------------------------------------------------------- W19 标识符不许被折断
#
# `_BREAK_AFTER` 里混着 `_` `.` `-` `:` 四个**标识符组成字符**，`_take_width`
# 会优先在它们后面断行，于是 `summarize_for_card` 在手机上显示成 `summarize_` /
# `for_card` 两行，用户会以为是两个东西。
#
# 判据是表驱动：四段文本各跑一次 `_wxmp_values`（3 槽 width=36），**任意两行
# 拼起来不许出现「行尾字母数字 + 行首字母数字」**。中文标点被劈不算问题——
# 中文本来就没有词边界，任意位置断行都是正常排版，只盯拉丁标识符和数字。

_IDENT_TEXTS = {
    "摘要": (
        "改完了，修改 wbn/channels.py 的 _wxmp_values 函数，新增 "
        "summarize_for_card 调用，测试 700 个用例都通过了。"
    ),
    "安装": (
        "这次改的是安装器，新增 _our_hook_exe_paths() 返回候选列表，"
        "install.py 里的 is_our_hook 支持 onedir。"
    ),
    "断行": (
        "这次修的是摘要那块，导致 summarize_for_card 在手机上显示成两行，"
        "看着像两个东西。"
    ),
    "路径": (
        "断行阈值里混着下划线，wbn/channels.py 的 _wxmp_values "
        "就被劈成两行显示出来了。"
    ),
}

# 词内字符：拼词时用来把两行的碎片还原成一个完整单词
_WORD_CHARS = "._-"

# 任务书 §1.3 点名要完整的标识符（外加路径里常见的两个）
_IDENTS = (
    "summarize_for_card",
    "_our_hook_exe_paths()",
    "install.py",
    "is_our_hook",
    "wbn/channels.py",
    "_wxmp_values",
)


def _split_word(a: str, b: str) -> str | None:
    """行尾是字母数字、行首也是字母数字 → 被劈开。返回拼起来的词。"""
    if not a or not b:
        return None
    if not (a[-1].isascii() and a[-1].isalnum()):
        return None
    if not (b[0].isascii() and b[0].isalnum()):
        return None
    i = len(a)
    while i > 0 and a[i - 1].isascii() and (a[i - 1].isalnum() or a[i - 1] in _WORD_CHARS):
        i -= 1
    j = 0
    while j < len(b) and b[j].isascii() and (b[j].isalnum() or b[j] in _WORD_CHARS):
        j += 1
    return a[i:] + b[:j]


def _card_of(text: str) -> list[str]:
    """把一段正文按真实模板（3 槽 width=36）渲染成三行。"""
    fields = {
        "kind": "task_done", "kind_label": "任务完成", "emoji": "✅",
        "title": "✅ WorkBuddy 任务完成", "project": "Work buddy 通知",
        "duration": "1 分 46 秒", "summary": text, "error": "",
        "host": "XingHan", "date": "2026-11-01",
    }
    v = channels._wxmp_values(
        fields["title"], text, fields, "00:30", wrap=True, tpl_content=_tpl_for(3, 36)
    )
    return [v[k] for k in ("detail", "detail2", "detail3")]


@pytest.mark.parametrize("name", list(_IDENT_TEXTS))
def test_no_identifier_is_split_across_lines(name: str):
    """§1.4 表驱动：四段文本里任意两行都不许在词中间断开。"""
    segs = _card_of(_IDENT_TEXTS[name])

    bad = [
        w for a, b in zip(segs, segs[1:], strict=False) if (w := _split_word(a, b))
    ]
    assert not bad, f"{name}：{bad} 被劈开了 → {segs}"

    # 通用规则漏掉 `install.` + `py` 这种（行尾是点号），再按标识符点名核一遍
    joined = "\n".join(segs)
    broken = [i for i in _IDENTS if i in joined.replace("\n", "") and i not in joined]
    assert not broken, f"{name}：{broken} 跨行了 → {segs}"


def test_break_after_holds_no_identifier_char():
    """`_BREAK_AFTER` 里一个标识符组成字符都不许有——这是整条修法的根。"""
    for ch in "_.-:":
        assert ch not in channels._BREAK_AFTER, (
            f"{ch!r} 又混回 _BREAK_AFTER 了：{channels._BREAK_AFTER!r}"
        )


def test_long_dotted_token_still_breaks_at_a_dot():
    """整行没有空格可退时，二级回退退到点号——不许硬切在词中间。

    `requests.exceptions.ConnectionError` 是 38 个字符的单个 token，
    一级断点（空格 `/` `;` `(` `@`）一个都找不到。这时候硬切会得到
    `ConnectionErro` / `r`，比断在点号后面糟得多。
    """
    head = channels._take_width("requests.exceptions.ConnectionError", 30)
    assert head.endswith("."), f"应该在点号后断开，实际 {head!r}"
    assert "Connection" not in head, head


