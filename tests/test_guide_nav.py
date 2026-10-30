"""指南页页内导航的源码守卫（W7.4）。

**为什么要有这一条**：这一轮唯一的硬约束是「不许用 `<a href="#xxx">`」——
`shell.js:349` 的 `routeFromHash()` 把 `location.hash` 当路由名，不认识的值
直接落回 `DEFAULT_ROUTE`，点一下导航人就被弹到状态页。

这条约束此前只写在 `guide.js` 的注释里，**没有任何测试拦着**。注释挡不住下一个
人「顺手改成锚链接更语义化」。所以这里扫源码，代价是毫秒级。

扫的是**去掉注释后**的源码：注释里提到 `href="#…"` 是在解释为什么不能这么写，
不该判红（`_strip_comments` 与 `test_guide_copy.py` 同一套做法）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "webui" / "assets" / "pages" / "guide.js"


def _code() -> str:
    src = GUIDE.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def test_nav_never_touches_location_hash():
    """导航只许 scrollIntoView，一行都不许碰 location.hash / href。

    判据是 **`href` 这个词本身**，不是 `href="#…"` 这个字面量。第一版写成
    `href="#` 时漏掉了 `setAttribute("href", "#guide-others")` —— 引号把
    `href` 和 `#` 隔开了，字符串里根本不存在 `href="#` 这个子串，变异测试照样绿。
    这一页不需要任何 `href`，所以直接查词最稳。
    """
    code = _code()
    for pat in ("href", "location.hash"):
        assert pat not in code, (
            f"guide.js 里出现了 {pat!r}。\n"
            "`routeFromHash()` 会把 hash 当路由名，不认识的落回状态页——"
            "页内跳转必须走 data-goto + scrollIntoView。"
        )


def test_chips_are_buttons_not_links():
    """chip 是 `<button>`。`<a>` 要么污染 hash，要么需要 preventDefault 补丁。"""
    code = _code()
    assert 'ctx.el("button", "guide-chip' in code, "找不到 guide-chip 的创建点"
    assert not re.search(r'el\(\s*"a"\s*,\s*"guide-chip', code), \
        "guide-chip 被写成了 <a>"


def _array_src(code: str, name: str) -> str:
    """取出 `var NAME = [ … ];` 这一段源码。

    W7.6 之后文件里有三个带 `key` 的数组（`OTHERS` / `SECTIONS` / `CARDS`），
    全文扫 `key:` 会一起抓进来。所以按数组切段，而不是放宽判据。
    """
    m = re.search(r"var " + name + r" = \[(.*?)\n  \];", code, re.S)
    assert m, f"找不到 {name} 数组（写法变了？）"
    return m.group(1)


def test_every_other_channel_has_a_unique_anchor_key():
    """9 个通道各有一个 `key`，用来拼 `guide-ch-<key>`。

    重复或缺失时，二级 chip 会指向不存在的 id（点了没反应，也不报错）——
    这种静默失败最难看。键就写在 `name` 旁边，两处不会各自漂。
    """
    code = _array_src(_code(), "OTHERS")
    keys = re.findall(r'\{\s*key:\s*"([a-z0-9-]+)"', code)
    assert len(keys) == 9, f"OTHERS 应当有 9 个 key，实际 {len(keys)} 个：{keys}"
    assert len(set(keys)) == 9, f"key 有重复：{keys}"
    assert all(k.isascii() for k in keys), "id 里不要用中文"


# ---------------------------------------------------------------- W7.6 单选切换
#
# 这一轮把指南页从「一条长文 + 5 个锚点」改成「总栏 + 单项」。三条守卫对应
# 三个**改坏了不报错、只是悄悄变差**的地方：
#
#   1. 默认不是总栏        → 打开指南页停在上次那节，和「查阅型页面」的预期相反
#   2. 用 CSS 藏 5 节      → 看起来对，读屏仍读到、Ctrl+F 仍搜到，DOM 里 5 节都在
#   3. tab 与面板 id 错位  → aria-controls 指向不存在的 id，读屏报「控件不可用」
#
# 三条都做过变异验证（改坏 → 红 → 改回 → 绿）。判据挑的都是「改坏了必然命中」
# 的字符串，不是「看起来像」——W7.4 那次 `href="#` 假绿就是挑了不存在的子串。


def test_default_section_is_index():
    """`state.section` 的初值必须是 `"index"`（总栏）。"""
    code = _code()
    m = re.search(r'\bsection:\s*"([a-z]+)"', code)
    assert m, "找不到 state.section 的初值（写法变了？）"
    assert m.group(1) == "index", (
        f'指南页的默认分节是 {m.group(1)!r}，必须是 "index"。\n'
        "指南是查阅型页面，每次进来回到总栏比「停在上次看的那节」更可预期。"
    )


def test_sections_are_not_hidden_with_css():
    """不许 5 节全渲染再用 CSS 藏起来。

    判据是两串「改坏了必然命中」的代码：`display: none` / `display = "none"`，
    以及 `x.hidden = true`。只查这两串是有意的——查 `display` 这个词会把
    将来合法的 `display: flex` 也判红，那种守卫没人会留。
    """
    code = _code()
    css_hide = re.findall(r"display\s*[:=][^;\n]*none", code)
    assert not css_hide, (
        "guide.js 里用 display:none 藏分节了：\n  " + "\n  ".join(css_hide) +
        "\n「只显示一项」必须是条件渲染（只 append 当前那节），"
        "不是 5 节全进 DOM 再藏起来——读屏仍能读到、Ctrl+F 仍能搜到。"
    )
    attr_hide = re.findall(r"\.hidden\s*=\s*(?:true|!0|1)\b", code)
    assert not attr_hide, (
        "guide.js 里用 `.hidden = true` 藏分节了：\n  " + "\n  ".join(attr_hide) +
        "\n同上：这不是「只渲染一项」。"
    )


def test_tab_and_panel_ids_are_one_to_one():
    """6 个 tab 与 6 个面板一一对应，且 id 是字面量、不是拼出来的。

    `aria-controls` 指向一个不存在的 id 时，读屏会报「控件不可用」，而界面上
    一点异常都看不出来。id 写成 `"panel-" + key` 的话源码里只剩一个值，
    守卫会退化成「看起来对」——所以这里要求字面量，并逐条核对 `tab-<key>` /
    `panel-<key>` 的拼法。
    """
    code = _code()
    entries = re.findall(
        r'key:\s*"([a-z]+)"[^}]*?tab:\s*"([a-z-]+)"[^}]*?panel:\s*"([a-z-]+)"', code
    )
    assert len(entries) == 6, f"SECTIONS 应当有 6 项，实际解析出 {len(entries)} 项：{entries}"

    keys = [k for k, _t, _p in entries]
    tabs = [t for _k, t, _p in entries]
    panels = [p for _k, _t, p in entries]
    assert len(set(keys)) == 6, f"key 有重复：{keys}"
    assert len(set(tabs)) == 6, f"tab id 有重复：{tabs}"
    assert len(set(panels)) == 6, f"panel id 有重复：{panels}"

    wrong = [
        f"{k}: tab={t!r} panel={p!r}"
        for k, t, p in entries
        if t != f"tab-{k}" or p != f"panel-{k}"
    ]
    assert not wrong, "tab / panel 的 id 和 key 对不上：\n  " + "\n  ".join(wrong)

    # 光有 id 不够：得真被用上，否则等于两套 id 各写各的。
    assert 'setAttribute("aria-controls", item.panel)' in code, \
        "tab 的 aria-controls 不是从 SECTIONS 的 panel 字段来的"
    assert 'setAttribute("aria-labelledby", spec.tab)' in code, \
        "面板的 aria-labelledby 不是从 SECTIONS 的 tab 字段来的"
    assert "panel.id = spec.panel" in code, \
        "面板的 id 不是从 SECTIONS 的 panel 字段来的"

