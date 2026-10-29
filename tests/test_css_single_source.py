"""CSS 结构检查：选择器只有一个出处，且两页的窄窗断点必须差得够。

为什么单独一个文件：`test_guide_copy.py` 管的是「文案是不是逐字搬运」，
这里管的是「样式结构对不对」。两者的失败信息完全不同，混在一个文件里读起来累。

三个用例，共用**同一个**括号配平解析器（`_walk_rules`）——解析规则写两份
就又是「同一件事两个说法」：

- `test_no_selector_defined_in_two_stylesheets`：R39，搬家时漏删旧规则
- `test_each_fields_rule_has_one_breakpoint`：W4.4，同一选择器不许有两条断点
- `test_channels_breakpoint_is_wider_than_generic`：W4.3，两页可用宽度不同，
  一个断点服务不了两页

后两条分工明确：一条管「有几条」，一条管「差多少」。聚合（min/max）判不出
「有多条」，`min()` / `max()` 无论怎么取，都是从某一个方向猜。
"""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "webui" / "assets"

# `tokens.css` 只有自定义属性，没有选择器，不扫。
SHEETS = ("shell.css", "channels.css")

# 断点差值下限。两页可用宽度差 318px（实测拟合：通道页 .fields ≈ 视口 − 632，
# 触发页 .fields = min(720, 视口 − 314)），留出余量后取 150：
# 差值小于这个数，通道页两列必然窄于 150px，18 字符的 appID 就装不下。
MIN_BREAKPOINT_GAP = 150


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


def _max_width_of(head: str) -> int | None:
    """`@media (max-width: 760px)` → 760。取不到（如 `prefers-color-scheme`）回 None。"""
    m = re.search(r"max-width\s*:\s*(\d+)px", head)
    return int(m.group(1)) if m else None


def _walk_rules(path: Path) -> list[tuple[int | None, str, int]]:
    """`[(媒体查询的 max-width 或 None, 选择器, 行号)]`。

    - `@media` 递归进去，并记下这一层的 `max-width`（内层继承外层）
    - `@keyframes` 整块跳过：`from` / `to` / `50%` 不是选择器
    - 其余 at-rule（`@font-face` 等）没有选择器，跳过
    - 用括号配平找块尾，不用 `[^}]*`——`@media` 嵌套会把它切断
    """
    text = _strip_comments(path.read_text(encoding="utf-8"))
    out: list[tuple[int | None, str, int]] = []

    def line_of(idx: int) -> int:
        return text.count("\n", 0, idx) + 1

    def walk(start: int, end: int, max_width: int | None) -> None:
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
                    inner = _max_width_of(head)
                    walk(i + 1, j - 1, inner if inner is not None else max_width)
                elif head.startswith("@"):
                    pass                                  # @font-face 之类
                else:
                    for sel in _split_selectors(head):
                        out.append((max_width, sel, sel_line))
                i = j
                head_start = j
                continue
            if ch == "}":
                i += 1
                head_start = i
                continue
            i += 1

    walk(0, len(text), None)
    return out


def _definitions(path: Path) -> list[tuple[str, int]]:
    return [(sel, line) for _mw, sel, line in _walk_rules(path)]


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


def test_each_fields_rule_has_one_breakpoint():
    """`.fields` 的两个窄窗规则各自只能有一条断点。

    有两条时，谁生效取决于书写顺序 + 视口，聚合（min/max）只能猜——
    W4.4 实测：同文件再加一条 `@media (max-width: 820px) { .fields … }`
    （R39 的原始形状），单源测试和断点测试**双双绿**。

    这条测试是另外两条兜不住的：单源测试只管**跨文件**，同文件重复它不看；
    断点测试的聚合再保守，也只能从一个方向判——比如通道页多出一条 1400px，
    取 min 仍是 975，差值照样通过。

    **两个样式表都扫。** 只扫 `shell.css` 会留同一个洞的另一个入口：
    `channels.css` 内部自己重复一遍，单源测试不叫（只有一个文件），
    断点测试也看不见（它只读 `shell.css`）。
    """
    hits: dict[str, list[tuple[int, str, int]]] = {}
    for name in SHEETS:
        for mw, sel, line in _walk_rules(ASSETS / name):
            if mw is not None:
                hits.setdefault(sel, []).append((mw, name, line))

    for sel in (".fields", 'body[data-route="channels"] .fields'):
        found = hits.get(sel, [])
        where = "、".join(f"{name}:{line}（max-width: {mw}px）"
                          for mw, name, line in found)
        assert len(found) == 1, (
            f"`{sel}` 应当恰好有 1 条 `@media (max-width: …)` 规则，"
            f"实际 {len(found)} 条：{where or '（一条都没有）'}。\n"
            "有两条时谁生效取决于书写顺序 + 视口，任何聚合都是在猜。"
            "R39 就是这么复活的：同文件补一条低断点，两条守卫都不叫。")


def test_channels_breakpoint_is_wider_than_generic():
    """通道页的窄窗断点必须明显高于通用断点。

    两页的可用宽度差 318px（实测拟合：通道页 .fields ≈ 视口 − 632，
    触发页 .fields = min(720, 视口 − 314)）。两者取同一个值时，通道页两列
    会被压到 126px，18 字符的 appID 直接截断（W4.3 截图 + 实测）。

    **断言的是差值，不是数值。** 具体数值可以调（现在是 975 / 760），但差值不能
    缩到 150 以下——缩到 150 以下，通道页两列必然窄于 150px，appID 就会被切。
    """
    rules = _walk_rules(ASSETS / "shell.css")

    generic = [(mw, line) for mw, sel, line in rules
               if sel == ".fields" and mw is not None]
    assert generic, ("shell.css 里找不到通用 `.fields` 的窄窗断点——"
                     "触发页和模板页就靠它落成一列")

    scoped = [(mw, line) for mw, sel, line in rules
              if sel == 'body[data-route="channels"] .fields' and mw is not None]
    assert scoped, (
        "shell.css 里找不到 `body[data-route=\"channels\"] .fields` 的窄窗断点。\n"
        "通道页比另两页多一个 240px 列表，用通用断点会让两列各只剩 126px，"
        "appID / 模板 ID / openid 全被切（W4.3）。")

    # 聚合取**保守**方向，含义是「最坏情况下差多少」：
    #   通用侧要「不许太高」→ 取最大；通道侧要「不许太低」→ 取最小。
    # 反过来（min/max 往通过那边取）就是乐观聚合，W4.4 实测能被重复规则绕过。
    # 正常情况下各只有一条，和 min/max 无关；重复由
    # `test_each_fields_rule_has_one_breakpoint` 单独钉住。
    generic_max = max(mw for mw, _ in generic)
    scoped_min = min(mw for mw, _ in scoped)

    # 先判「通用值没被抬高」，再判差值。
    # 顺序反了的话，两条一起抬到 950 会先撞差值断言、报「只差 0px」——
    # 那是误导：真正的问题是通用断点服务不了触发页，不是差值不够。
    assert generic_max <= 800, (
        f"通用断点被抬到了 {generic_max}px（上限 800）。"
        "这条规则服务触发页和模板页，它们没有左侧列表、可用宽度宽 318px，"
        "跟着一起抬高会让这两页在本来装得下的时候提前塌成一列。")

    gap = scoped_min - generic_max
    assert gap >= MIN_BREAKPOINT_GAP, (
        f"通道页断点 {scoped_min}px 与通用断点 {generic_max}px 只差 {gap}px，"
        f"小于下限 {MIN_BREAKPOINT_GAP}px。\n"
        f"差值这么小时通道页两列会窄于 150px，appID 这类 18 字符的值读不出来。\n"
        f"行号：通用 shell.css:{generic[0][1]}  "
        f"通道 shell.css:{scoped[0][1]}")
