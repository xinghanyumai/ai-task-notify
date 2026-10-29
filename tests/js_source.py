"""扫前端 JS 源码时共用的两件事：资产路径 + 「只看代码、不看注释」。

## 为什么单独一个模块

`test_no_focus_killing_disabled.py`（扫 `.disabled =`）和
`test_paint_detail_focus.py`（扫 `paintDetail()` 调用点）都要同一件事：
**把注释剥掉再扫**。而「块注释会跨行」这个坑两个扫描器都会踩——

- W6.4 的第一批 6 条误报里有 1 条，就是测试文件自己的 docstring 里写着
  `save.disabled = ...` 当例子，被当成了真实赋值；
- 本文件所在的 `channels.js` 里也有两处注释在讲 `paintDetail()`（一行在
  `paintDetail` 的 docstring 里，一行在 `toggle` 的注释里），不剥就会多扫出
  两个假的调用点。

实现留一份，省得改一处忘一处——**这句话正是这两条守卫要防的病本身**。
"""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "webui" / "assets"


def code_only(src: str) -> list[str]:
    """逐行返回**只留代码**的文本：去掉 `//` 行尾注释与 `/* ... */` 块注释。

    为什么不能只看行首：`/* ... */` 会跨行，注释中间那些续行既不以 `*` 开头
    也不以 `//` 开头。只判行首的话，注释里举例用的代码会被当成真实语句。

    顺便避开字符串里的 `//`（URL 里的 `http://` 被当成注释，会把后半行吃掉），
    并把引号内的内容一并丢弃——字符串里的 `disabled` / `paintDetail()`
    也不该算数。
    """
    out, buf = [], []
    i, n = 0, len(src)
    in_block = False
    quote = ""            # 非空表示正处在这个引号里
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if ch == "\n":
            buf.append(ch)
            out.append("".join(buf))
            buf = []
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
                i += 2
                continue
            if ch == quote:
                quote = ""
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
            i += 1
            continue
        buf.append(ch)
        i += 1
    # 文件以换行结尾时，最后那次 `\n` 已经把 buf 冲掉了，这里再 append 会多出
    # 一个空行——`splitlines()` 不会。两边的行号必须一一对齐（调用方按下标取
    # 「紧邻上一行」），所以只在真有剩余内容时收尾。
    if buf:
        out.append("".join(buf))
    return out


def norm(text: str) -> str:
    """空白归一化：`!ready  ||  state.busy` 与 `!ready || state.busy` 等价。"""
    return re.sub(r"\s+", " ", text).strip()


def raw_lines(src: str) -> list[str]:
    """原始行（**带注释**）。

    有些检查要看注释本身（比如 `/* focus-ok: 理由 */` 这种豁免标记），
    那种场合不能用 `code_only`。
    """
    return src.splitlines()
