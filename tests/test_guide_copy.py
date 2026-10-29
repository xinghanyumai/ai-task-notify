"""教程页的文案必须和 `ui.py:_page_guide` 逐段一致。

为什么要有这条测试：`guide.js` 里的文字是**抄**过去的（`ui.py` 是 GUI 层，
import tkinter，bridge 不能引它，所以没法在运行时共用一份）。抄写就会漏、
就会顺手润色。这条测试把「逐字照搬」从人工检查变成可执行断言。

做法：用 `ast` 解析 `ui.py`，取 `_page_guide` 里所有字符串常量
（Python 会把相邻字面量折叠成一个 `Constant`，所以一段话正好是一个节点），
再把 `guide.js` 里被 `" +` / `+ "` 拆开的相邻字面量接回去，逐个做子串匹配。

**四处例外是白名单，不是通配。** 只有「控件指代」那四句允许不同，
其余任何差异都会红——包括多加一个逗号。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 原文 → 页面上实际写的那句。理由都是同一个：web 版的控件名/位置和 tkinter 不同。
LOCATOR_FIXES = (
    ("打开「推送通道」", "打开「通知通道」"),
    ("点右下角「发送测试」", "点「测试此通道」"),
    ("点「发送测试」报错", "点「测试此通道」报错"),
    ("点左下角「日志」，logs/notify-日期.log 里每条都有 ok=True/False 和原始返回",
     "打开 logs/notify-日期.log，里面每条都有 ok=True/False 和原始返回"),
)

MIN_LEN = 6          # 短于这个长度的字符串是控件参数（"body" / "bold" / "x"），不比对


def _page_guide_strings() -> list[str]:
    tree = ast.parse((ROOT / "ui.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_page_guide":
            # 函数自己的 docstring 是写给维护者的，不是给用户看的文案，
            # 而且它讲的是「怎么排版」，页面上不该出现。跳过。
            doc = node.body[0] if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ) else None
            skip = doc.value if doc is not None else None

            out = []
            for sub in ast.walk(node):
                if sub is skip:
                    continue
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    if len(sub.value.strip()) >= MIN_LEN:
                        out.append(sub.value)
            return out
    raise AssertionError("ui.py 里找不到 _page_guide")


def _strip_comments(src: str) -> str:
    """去掉 `/* … */` 和行首 `//` 注释。

    注释里会写「原文是「点右下角「发送测试」」，所以要改」——那是给维护者看的
    说明，不是页面上给用户看的文案。不剥掉的话，两条断言会互相打架：
    「必须逐字照搬」和「必须改掉这四处」都能在注释里找到证据。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def _js_flat() -> str:
    """把 `"…" +` / `+ "…"` 接回去，让被换行拆开的段落变回一整串。"""
    src = (ROOT / "webui" / "assets" / "pages" / "guide.js").read_text(encoding="utf-8")
    return re.sub(r'"\s*\+\s*"', "", _strip_comments(src))


def _fixed(text: str) -> str:
    for old, new in LOCATOR_FIXES:
        text = text.replace(old, new)
    return text


def test_guide_copy_is_verbatim():
    js = _js_flat()
    missing = [s for s in _page_guide_strings() if _fixed(s) not in js]
    assert not missing, "教程页漏了或改了这些原文：\n" + "\n".join(
        f"  - {s[:60]}…" for s in missing)


def test_locator_fixes_are_the_only_differences():
    """四处指代的**原文**不许出现在页面里——否则就是「照抄了一个不存在的按钮」。"""
    js = _js_flat()
    leaked = [old for old, _new in LOCATOR_FIXES if old in js]
    assert not leaked, f"这四处必须改掉，页面里还留着原文：{leaked}"


def test_no_stray_innerhtml_in_pages():
    """三页都不许有 innerHTML / insertAdjacentHTML / document.write。

    模板预览显示的是 AI 产出的正文，一次 innerHTML 就是一条 XSS
    （`app.py` 带 --no-sandbox，等于本地代码执行）。
    """
    bad = []
    for name in ("triggers", "templates", "guide"):
        src = _strip_comments((ROOT / "webui" / "assets" / "pages" /
                               f"{name}.js").read_text(encoding="utf-8"))
        for pat in ("innerHTML", "insertAdjacentHTML", "document.write"):
            if pat in src:
                bad.append(f"{name}.js: {pat}")
    assert not bad, bad


def test_no_literal_colors_in_pages():
    """三页的 JS 里不许出现字面色值（`#rrggbb` / `rgb()` / `rgba()`）。

    颜色只走 token（`tokens.css`）和 `data-level` / `data-tone` 属性。
    """
    pat = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(")
    bad = []
    for name in ("triggers", "templates", "guide"):
        src = _strip_comments((ROOT / "webui" / "assets" / "pages" /
                               f"{name}.js").read_text(encoding="utf-8"))
        for i, line in enumerate(src.splitlines(), 1):
            if pat.search(line):
                bad.append(f"{name}.js:{i}: {line.strip()[:60]}")
    assert not bad, bad
