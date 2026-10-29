"""教程页的文案必须和定稿原文逐段一致。

为什么要有这条测试：`guide.js` 里的文字是**抄**过去的（原设计是 GUI 层，
import tkinter，bridge 不能引它，所以没法在运行时共用一份）。抄写就会漏、
就会顺手润色。这条测试把「逐字照搬」从人工检查变成可执行断言。

**源是 `tests/fixtures/guide_expected.py`，不是 `ui.py`。** W5 把 `ui.py`
删掉了；原文在那之前用 `ast` 原样快照到 fixtures 里（36 段，一个字没改）。
删 `ui.py` 时如果顺手把这条测试也删了，等于把 W4 花力气建的守卫当成
「旧栈的一部分」一起下线——所以快照 + 改读快照，而不是取消检查。

做法：`guide.js` 里被 `" +` / `+ "` 拆开的相邻字面量接回去，再对快照里
每一段做子串匹配。

**四处例外是白名单，不是通配。** 只有「控件指代」那四句允许不同，
其余任何差异都会红——包括多加一个逗号。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 快照就在 tests/fixtures/ 里，直接 import 比再解析一遍 AST 更实在：
# 解析逻辑留在生成那一刻，测试只负责比对。
from fixtures.guide_expected import GUIDE_STRINGS  # noqa: E402

# 原文 → 页面上实际写的那句。理由都是同一个：web 版的控件名/位置和原设计不同。
LOCATOR_FIXES = (
    ("打开「推送通道」", "打开「通知通道」"),
    ("点右下角「发送测试」", "点「测试此通道」"),
    ("点「发送测试」报错", "点「测试此通道」报错"),
    ("点左下角「日志」，logs/notify-日期.log 里每条都有 ok=True/False 和原始返回",
     "打开 logs/notify-日期.log，里面每条都有 ok=True/False 和原始返回"),
)


def _page_guide_strings() -> list[str]:
    """定稿原文。快照为空说明有人把文件清空了，那比「文案被改」更糟。"""
    assert GUIDE_STRINGS, (
        "tests/fixtures/guide_expected.py 是空的——文案守卫失去了比对源，"
        "等于没在守。它不该被清空。")
    return list(GUIDE_STRINGS)


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
