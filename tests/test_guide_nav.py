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


def test_every_other_channel_has_a_unique_anchor_key():
    """9 个通道各有一个 `key`，用来拼 `guide-ch-<key>`。

    重复或缺失时，二级 chip 会指向不存在的 id（点了没反应，也不报错）——
    这种静默失败最难看。键就写在 `name` 旁边，两处不会各自漂。
    """
    code = _code()
    keys = re.findall(r'\{\s*key:\s*"([a-z0-9-]+)"', code)
    assert len(keys) == 9, f"OTHERS 应当有 9 个 key，实际 {len(keys)} 个：{keys}"
    assert len(set(keys)) == 9, f"key 有重复：{keys}"
    assert all(k.isascii() for k in keys), "id 里不要用中文"
