"""CSS 结构检查：同一个选择器不许在两个样式表里各定义一次。

为什么单独一个文件：`test_guide_copy.py` 管的是「文案是不是逐字搬运」，
这里管的是「样式规则是不是只有一个出处」。两者的失败信息完全不同，
混在一个文件里读起来累。

来历（R39）：W4 把 `.field` 整族从 `channels.css` 搬到 `shell.css`，69 条声明
搬对 68 条。漏下的那条是 `@media (max-width: 820px) { .fields … }`，而
`shell.css` 里写成了 760。两份规则同时存在时谁生效取决于加载顺序 + 视口宽度，
默认视口下像素比对看不出任何异常——手工比 69 条能抓到一次，但下次搬家不会
自动再跑一遍。所以写成测试。
"""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "webui" / "assets"

# `tokens.css` 只有自定义属性，没有选择器，不扫。
SHEETS = ("shell.css", "channels.css")


def _strip_comments(text: str) -> str:
    """把 `/* … */` 换成等量换行——注释里的 `.fields` 不算定义，但行号不能漂。"""
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"),
                  text, flags=re.S)


def _split_selectors(head: str) -> list[str]:
    """按顶层逗号切选择器。`@media (min-width: 1px), print` 这种不算选择器表。"""
    parts, buf, depth = [], "", 0
    for ch in head:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
            continue
        buf += ch
    parts.append(buf)
    return [" ".join(p.split()) for p in parts if p.strip()]


def _definitions(path: Path) -> list[tuple[str, int]]:
    """`[(选择器, 行号)]`。

    - `@media` / `@supports` 递归进去（里面的 `.fields` 也是定义）
    - `@keyframes` 整块跳过：`from` / `to` / `50%` 不是选择器
    - 其余 at-rule（`@font-face` 等）没有选择器，跳过
    - 用括号配平找块尾，不用 `[^}]*`——`@media` 嵌套会把它切断
    """
    text = _strip_comments(path.read_text(encoding="utf-8"))
    out: list[tuple[str, int]] = []

    def line_of(idx: int) -> int:
        return text.count("\n", 0, idx) + 1

    def walk(start: int, end: int) -> None:
        i = start
        head_start = start
        while i < end:
            ch = text[i]
            if ch == "{":
                depth, j = 1, i + 1
                while j < end and depth:
                    if text[j] == "{":
                        depth += 1
                    elif text[j] == "}":
                        depth -= 1
                    j += 1
                raw_head = text[head_start:i]
                head = " ".join(raw_head.split())
                lead = len(raw_head) - len(raw_head.lstrip())
                sel_line = line_of(head_start + lead)
                if head.startswith("@keyframes") or head.startswith("@-webkit-keyframes"):
                    pass                                  # from/to/50% 不是选择器
                elif head.startswith("@media") or head.startswith("@supports"):
                    walk(i + 1, j - 1)
                elif head.startswith("@"):
                    pass                                  # @font-face 之类
                else:
                    for sel in _split_selectors(head):
                        out.append((sel, sel_line))
                i = j
                head_start = j
                continue
            if ch == "}":
                i += 1
                head_start = i
                continue
            i += 1

    walk(0, len(text))
    return out


def _format(conflicts: dict[str, list[tuple[str, int]]]) -> str:
    lines = ["同一个选择器在两个样式表里各定义了一次："]
    for sel in sorted(conflicts):
        lines.append(f"  {sel}")
        for name, line in conflicts[sel]:
            lines.append(f"    {name}:{line}")
    lines.append("")
    lines.append("两份规则同时存在时，谁生效取决于加载顺序 + 视口宽度，"
                 "默认视口下的像素比对看不出异常（R39）。")
    return "\n".join(lines)


def test_no_selector_defined_in_two_stylesheets():
    """同一个选择器不许在两个样式表里各定义一次。

    为什么：W4 把 `.field` 整族从 channels.css 搬到 shell.css，69 条声明搬对 68 条，
    漏下的那条是 `@media (max-width: 820px) { .fields … }`，而 shell.css 里
    写成了 760。两份规则同时存在时，谁生效取决于加载顺序 + 视口宽度，
    像素比对在默认视口下看不出任何异常（R39）。

    **零白名单。** 这个项目里没有「故意在两个文件定义同一个选择器」的合法场景。
    """
    seen: dict[str, list[tuple[str, int]]] = {}
    for name in SHEETS:
        for sel, line in _definitions(ASSETS / name):
            seen.setdefault(sel, []).append((name, line))

    conflicts = {
        sel: where for sel, where in seen.items()
        if len({f for f, _ in where}) > 1
    }
    assert not conflicts, _format(conflicts)
