"""英文错误串不被拦腰截断 + 宽度口径统一（W6.2 / R42）。

真机现象（2026-10-29 15:15 那条通知）：

    detail  = "…: No"
    detail2 = "module named 'pyside6'"

`No module` 被拆成 `No` / `module`——虽然空格本来就允许断行，但下一行的
`ModuleNotFoundError` 被劈成 `ModuleNotF` / `oundError` 就是真的读不懂了。

三条约束（任务书 §R42）：

1. **英文词不劈开**：切点落在词中间就退回最近的断点，退完可能只剩半行——认了。
2. **一把尺子**：`_clip_line` 的 `limit` 与 `_wrap_detail` 的 `width` 都按半角位算。
   以前 `_clip_line` 数 `len()`，于是「`…` 按 3 预留」这种改动要在两处各改一遍。
3. **`…` 按 3 个半角位预留**：微信渲染成 `...`，`unicodedata` 按 Ambiguous 只算 2。

本文件与 `test_wrap.py` 的分工：那边测中文折行的**排版观感**（禁则、行数、
省略号位置），这边测**不劈词**这条硬约束和**尺子**的唯一性。
"""

from __future__ import annotations

import pytest

from wbn import channels

# 20 条真机上见过的错误串。挑的标准是「长英文词紧挨着断点」——
# 短词本来就劈不开，测了也看不出差别。
ERRS = [
    "ModuleNotFoundError: No module named 'pyside6'",
    "exit code 1: command not found: ffmpeg",
    "FileNotFoundError: [Errno 2] No such file or directory: 'config.json'",
    "UnicodeEncodeError: 'gbk' codec can't encode character '\\u2705' in position 0",
    "PermissionError: [WinError 5] Access is denied: 'C://Windows//System32//drivers'",
    "ImportError: cannot import name 'BridgeNope' from 'wbn.bridge'",
    "TypeError: unsupported operand type(s) for +: 'int' and 'str'",
    "subprocess.CalledProcessError: Command '['npm', 'install']' returned non-zero exit status 1.",
    "fatal: not a git repository (or any of the parent directories): .git",
    "error TS2307: Cannot find module './types' or its corresponding type declarations.",
    "npm ERR! code ERESOLVE npm ERR! ERESOLVE unable to resolve dependency tree",
    'Traceback (most recent call last): File "app.py", line 12, in <module>',
    "ssl.SSLCertVerificationError: certificate verify failed: unable to get local issuer certificate",
    "requests.exceptions.ConnectionError: HTTPSConnectionPool(host='api.weixin.qq.com', port=443)",
    "KeyError: 'template_id'",
    "ValueError: invalid literal for int() with base 10: 'abc'",
    "OSError: [WinError 10049] The requested address is not valid in its context",
    "SyntaxError: invalid syntax (channels.py, line 381)",
    "RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB",
    "pytest: error: unrecognized arguments: --runs=3",
]

# 24 是窄模板（标签占掉大半行）时的可用宽度，34 是默认值。
WIDTHS = (24, 30, 34)


def _rendered_w(text: str) -> int:
    """微信里实际占几格：`…` 渲染成 `...`，按 3 算，不是 `_char_w` 给的 2。"""
    return sum(channels._ELLIPSIS_W if c == "…" else channels._char_w(c) for c in text)


def _junctions(flat: str, lines: list[str]) -> list[str]:
    """每对相邻行之间被吃掉的字符（空格、行首禁则符号）。

    直接比对 `a[-1]` 和 `b[0]` 会误报：`'[Errno 2]'` 结尾是 `]`、下一行开头是
    `No` 的 `N`，拆开读本来就通顺。只有**一个字符都没被吃掉**时，两行才是
    真的在词中间硬切。
    """
    out: list[str] = []
    pos = 0
    for a, b in zip(lines, lines[1:], strict=False):
        end = flat.find(a, pos) + len(a)
        nxt = flat.find(b, end)
        out.append(flat[end:nxt])
        pos = nxt
    return out


# ------------------------------------------------- 不劈英文词（R42 主判据）


@pytest.mark.parametrize("width", WIDTHS)
def test_no_ascii_word_is_cut_in_half(width):
    """相邻两行之间没有断点、且末/首字符都是字母数字 → 这个词被劈开了。

    旧实现（回退上限 12 个字符）在 width=24 有 2 处、width=34 有 1 处：
        cess.CalledProcess || Error
        LCertVerificationE || rror
        ons.ConnectionErro || r
    词长超过 12 就放弃回退，等于没修。
    """
    bad = []
    for err in ERRS:
        flat = channels._flatten(err)
        lines = channels._wrap_detail(err, width, 4).split("\n")
        for (a, b), gap in zip(
            zip(lines, lines[1:], strict=False), _junctions(flat, lines), strict=False
        ):
            if not gap and a[-1].isalnum() and b[0].isalnum():
                bad.append(f"{a!r} | {b!r}")
    assert not bad, f"width={width} 有 {len(bad)} 处把英文词劈开了：\n" + "\n".join(bad)


@pytest.mark.parametrize("width", WIDTHS)
def test_error_strings_never_overflow_the_card(width):
    lines = channels._wrap_detail("x", width, 4)  # 冒烟：不抛
    assert lines == "x"
    for err in ERRS:
        for line in channels._wrap_detail(err, width, 4).split("\n"):
            assert _rendered_w(line) <= width, f"width={width} 行宽超了：{line!r}"


def test_identifier_is_never_split_across_lines():
    """`pyside6` 不能变成 `pysi` / `de6`——这是 R42 的原始症状。"""
    err = "ModuleNotFoundError: No module named 'pyside6'"
    for width in WIDTHS:
        joined = "\n".join(channels._wrap_detail(err, width, 4).split("\n"))
        assert "pyside6" in joined, f"width={width} 把 pyside6 拆散了：{joined!r}"


def test_slash_is_a_legal_break_point():
    """`/` 是断点：路径可以在斜杠后断开，斜杠留在行尾。"""
    head = channels._take_width("C://Windows//System32//drivers", 12)
    assert head.endswith("/"), f"应该在斜杠后断开，实际 {head!r}"


def test_very_long_word_without_break_point_still_terminates():
    """整段一个断点都没有（一长串 hash）时只能切在词中间，但不能死循环。"""
    got = channels._take_width("a" * 200, 10)
    assert got == "a" * 10


def test_take_width_retreat_may_waste_half_a_line():
    """回退之后只剩半行是**认了的**代价：宁可短行，也不劈词。"""
    # "aaaa bbbb" 预算 8 → 硬切出 "aaaa bbb"，回退到空格 → "aaaa "
    assert channels._take_width("aaaa bbbb", 8) == "aaaa "


# ------------------------------------------------- 一把尺子（R42 要求 B）


def test_clip_line_limit_is_half_width_units():
    """同样 20 个字符，中文 40 格、英文 20 格——limit 数的是半角位不是字符数。"""
    cjk = "一二三四五六七八九十一二三四五六七八九十"      # 20 字 = 40 格
    ascii_ = "abcdefghijklmnopqrst"                      # 20 字 = 20 格
    assert len(cjk) == len(ascii_) == 20

    assert channels._clip_line(ascii_, 39) == ascii_, "20 格放得进 39 格，不该截"
    assert channels._clip_line(cjk, 39).endswith("…"), "40 格放不进 39 格，该截"


def test_clip_line_and_wrap_detail_share_one_ruler():
    """`_clip_line(t, w)` 与 `_wrap_detail(t, w)` 对同一个 `w` 都不许溢出。

    以前 `_clip_line` 数 `len()`、`_wrap_detail` 算半角，两把尺子——同一个
    数字在两边含义不同，改一处忘一处就是 R39 那类 bug。
    """
    samples = [
        "中文标题很长很长很长很长很长",
        "ModuleNotFoundError: No module named 'pyside6'",
        "混合 mixed 中英 text 12345",
        "a" * 80,
    ]
    for text in samples:
        for width in (10, 24, 40):
            clipped = channels._clip_line(text, width)
            assert _rendered_w(clipped) <= width, f"_clip_line({text!r}, {width}) → {clipped!r}"
            for line in channels._wrap_detail(text, width, 4).split("\n"):
                assert _rendered_w(line) <= width, f"_wrap_detail({text!r}, {width}) → {line!r}"


def test_width_sum_is_written_only_once():
    """`sum(_char_w(...))` 只许出现在 `_width()` 里。

    这是 R36「markdown 剥离只能有一个出处」的同类问题：散落的宽度求和会让
    「`…` 按 3 预留」这类改动要逐处跟进，漏一处就静默差 1 格。
    """
    import inspect

    src = inspect.getsource(channels)
    hits = [ln.strip() for ln in src.splitlines() if "sum(_char_w" in ln]
    assert len(hits) == 1, f"又出现了第二把尺子：{hits}"
    assert hits[0] == "return sum(_char_w(c) for c in text)"


# ------------------------------------------------- 省略号按 3 预留（要求 C）


@pytest.mark.parametrize("width", WIDTHS)
def test_ellipsis_reserves_three_cells_on_last_line(width):
    """`…` 在微信里占 3 格。末行按 2 预留，实际就比预算宽 1，最后一个字被硬切。

    `_char_w("…")` 返回 2（Ambiguous），所以要用 `_rendered_w` 复核——
    这正是「两把尺子」问题在同一个字符上的体现。
    """
    for err in ERRS:
        lines = channels._wrap_detail(err, width, 4).split("\n")
        last = lines[-1]
        if last.endswith("…"):
            assert _rendered_w(last) <= width, (
                f"width={width} 末行按 3 格算超宽：{last!r}（{_rendered_w(last)} 格）"
            )


def test_ellipsis_constant_is_three():
    """把 `_ELLIPSIS_W` 改回 2 就等于撤销这条修复，钉住它。"""
    assert channels._ELLIPSIS_W == 3
    assert channels._char_w("…") == 2, "底层的宽度表仍按 Ambiguous 算 2，差异是预期的"


# ------------------------------------------------- 中文禁则回归


@pytest.mark.parametrize("width", WIDTHS)
def test_error_lines_do_not_start_with_closing_punctuation(width):
    """改了断点逻辑，别把行首禁则弄丢——`，` / `.` 不该落到行首。"""
    for err in ERRS:
        for line in channels._wrap_detail(err, width, 4).split("\n"):
            assert line, f"折出了空行：{err!r}"
            assert line[0] not in channels._NO_LINE_START, f"行首是标点：{line!r}"
