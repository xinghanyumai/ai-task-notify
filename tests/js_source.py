"""扫前端 JS 源码时共用的两件事：资产路径 + 「只看代码、不看注释」。

## 为什么单独一个模块

`test_no_focus_killing_disabled.py`（扫 `.disabled =`）、
`test_paint_detail_focus.py`（扫 `paintDetail()` 调用点）和
`test_no_markdown_in_ui_text.py`（扫字符串里的 `**` / `__`）都要同一件事：
**把注释剥掉再扫**。而「块注释会跨行」这个坑每个扫描器都会踩——

- W6.4 的第一批 6 条误报里有 1 条，就是测试文件自己的 docstring 里写着
  `save.disabled = ...` 当例子，被当成了真实赋值；
- `channels.js` 里也有两处注释在讲 `paintDetail()`（一行在 `paintDetail` 的
  docstring 里，一行在 `toggle` 的注释里），不剥就会多扫出两个假的调用点；
- 三页的注释里到处是 `**加粗**` 写法（那是给人读的），不剥就会被
  「字符串里不许有 markdown 星号」这条守卫全部报出来。

实现留一份，省得改一处忘一处——**这句话正是这些守卫要防的病本身**。

## 一个状态机，两种视图

`split_source()` 走一遍源码，同时产出「只有代码的行」和「所有字符串字面量」。
`code_only()` / `string_literals()` 都从它派生。

**不能各写一遍**：剥注释和取字符串都要处理转义（`"a\\"b"`）、块注释跨行、
模板串跨行这三件事，两份实现必然在某个边界上分叉，然后一条守卫绿一条红，
而没人知道该信哪条。
"""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "webui" / "assets"


def split_source(src: str) -> tuple[list[str], list[tuple[int, str]]]:
    """一次扫描，两种视图。

    返回 `(code_lines, strings)`：

    - `code_lines`：逐行，**只留代码**——`//` 行尾注释、`/* … */` 块注释、
      以及**字符串的内容**都去掉。行数与 `src.splitlines()` 对齐。
    - `strings`：每个字符串字面量的 `(起始行号, 内容)`，单引号 / 双引号 /
      反引号都算。内容保留转义原样（`"a\\"b"` 的内容是 `a\\"b`）——
      调用方要判的是「里面有没有某些记号」，不需要解码。

    为什么 `code_lines` 里连字符串内容也去掉：`.disabled =` / `paintDetail()`
    这类判据只看代码；而注释里举例用的代码不该算数。字符串内容由
    `strings` 单独给出，需要的调用方去那里取。
    """
    code_lines: list[str] = []
    buf: list[str] = []
    strings: list[tuple[int, str]] = []
    quote_buf: list[str] = []
    quote_line = 1
    quote = ""            # 非空表示正处在这个引号里
    in_block = False
    line = 1
    i, n = 0, len(src)

    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        if ch == "\n":
            # 模板串可以跨行：换行既是字符串内容，也要结束当前代码行
            if quote:
                quote_buf.append(ch)
            buf.append(ch)
            code_lines.append("".join(buf))
            buf = []
            line += 1
            i += 1
            continue

        if in_block:
            if ch == "*" and nxt == "/":
                in_block = False
                i += 2
                continue
            i += 1
            continue

        if quote:
            if ch == "\\":
                quote_buf.append(ch)
                if i + 1 < n:
                    quote_buf.append(src[i + 1])
                i += 2
                continue
            if ch == quote:
                strings.append((quote_line, "".join(quote_buf)))
                quote_buf = []
                quote = ""
                i += 1
                continue
            quote_buf.append(ch)
            i += 1
            continue

        if ch == "/" and nxt == "*":
            in_block = True
            i += 2
            continue
        if ch == "/" and nxt == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if ch in "\"'`":
            quote = ch
            quote_line = line
            i += 1
            continue

        buf.append(ch)
        i += 1

    # 文件以换行结尾时，最后那次 `\n` 已经把 buf 冲掉了，这里再 append 会多出
    # 一个空行——`splitlines()` 不会。两边的行号必须一一对齐（调用方按下标取
    # 「紧邻上一行」），所以只在真有剩余内容时收尾。
    if buf:
        code_lines.append("".join(buf))
    return code_lines, strings


def code_only(src: str) -> list[str]:
    """逐行返回**只留代码**的文本：去掉注释与字符串内容。

    为什么不能只看行首：`/* ... */` 会跨行，注释中间那些续行既不以 `*` 开头
    也不以 `//` 开头。只判行首的话，注释里举例用的代码会被当成真实语句。

    顺便避开字符串里的 `//`（URL 里的 `http://` 被当成注释，会把后半行吃掉）。
    """
    return split_source(src)[0]


def string_literals(src: str) -> list[tuple[int, str]]:
    """所有字符串字面量的 `(起始行号, 内容)`。注释里的不算。

    给「用户可见文案」这类守卫用：`ctx.el(…, text)` 走的是 `textContent`，
    所以**字符串里写什么，用户就看到什么**——markdown 记号也不例外。
    """
    return split_source(src)[1]


def norm(text: str) -> str:
    """空白归一化：`!ready  ||  state.busy` 与 `!ready || state.busy` 等价。"""
    return re.sub(r"\s+", " ", text).strip()


def raw_lines(src: str) -> list[str]:
    """原始行（**带注释**）。

    有些检查要看注释本身（比如 `/* focus-ok: 理由 */` 这种豁免标记），
    那种场合不能用 `code_only`。
    """
    return src.splitlines()
