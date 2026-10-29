"""令牌用法守卫：**「哪个令牌画控件」这件事不许再退回去**（W6.3 §3 / §4）。

`tools/a11y_contrast.py` 是主守卫，但它要起真实窗口 + Playwright，CI 跑不了。
这里补一条纯文本的：只查 CSS 源码里**用了哪个令牌**，以及那几个被点名的
色值有没有被悄悄调过。两件事分开守，因为它们坏起来的方式不一样：

- **令牌误用**（拿装饰令牌画控件）→ 源码里能看出来，不需要渲染
- **色值被调**（为了过对比度把 `--brand` 调深）→ 也只有源码里能看出来

对比度本身不在这里算——那是扫描器的活，它读的是 `getComputedStyle`。
这里只算一条：`--track-on` 对 `--bg` 够不够 3:1。**为什么这一条要在这里算**：
`--track-on` 是专为这条对比度新开的令牌，它唯一的存在理由就是这个比值，
而这个比值只依赖两个十六进制字面值，不需要浏览器。独立算一遍（不 import
任何生产代码）才叫验证，拿 `--track-on` 自己的注释当依据不叫验证。
"""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "webui" / "assets"
TOKENS = (ASSETS / "tokens.css").read_text(encoding="utf-8")
SHELL = (ASSETS / "shell.css").read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    """取一条规则声明的正文，**去掉注释**。`selector` 要精确匹配到 `{` 之前。

    必须去注释：`.btn-secondary` 的注释里写着「**不是 --border-strong**」，
    不剥的话 `"--border-strong" not in body` 会红，而那条断言要查的是
    **声明**里用了什么，不是注释里提了什么。
    """
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"找不到规则：{selector}"
    return re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)


def _token(theme_block: str, name: str) -> str:
    m = re.search(rf"{re.escape(name)}\s*:\s*(#[0-9a-fA-F]{{6}})", theme_block)
    assert m, f"找不到令牌 {name}"
    return m.group(1).lower()


def _light_block() -> str:
    """:root { ... } 那一段（到 `[data-theme="dark"]` 之前）。"""
    start = TOKENS.index(":root {")
    end = TOKENS.index('[data-theme="dark"]')
    return TOKENS[start:end]


def _dark_block() -> str:
    start = TOKENS.index('[data-theme="dark"]')
    return TOKENS[start:TOKENS.index("\n}\n", start)]


def _ratio(fg: str, bg: str) -> float:
    """WCAG 相对亮度对比度。**独立实现**，不 import 生产代码。"""
    def lum(h: str) -> float:
        ch = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
               for c in ch]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    a, b = lum(fg), lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


# ---------------------------------------------------------------- §3 按钮


def test_secondary_button_border_is_not_a_decorative_token():
    """`.btn-secondary` 的边框必须是 `--border-input`，不许是 `--border-strong`。

    `tokens.css` 第 31 行写着 `--border` / `--border-strong` **只做装饰分隔线**，
    对白卡 1.45:1。拿它画按钮边框，等于让「唯一能看出这里有按钮」的那条线
    不达标（WCAG 1.4.11 要 3:1）。这不是豁免争议，是令牌误用——
    跟 R41 / R46 / R47 / R49 是同一类病：话和用不一致。
    """
    body = _rule(SHELL, ".btn-secondary")
    assert "--border-input" in body, f".btn-secondary 应当用 --border-input，实际：{body!r}"
    assert "--border-strong" not in body, (
        ".btn-secondary 又用回了 --border-strong（装饰令牌，1.45:1）。"
        "要改对比度请改控件令牌，别改装饰令牌——那会让所有分隔线一起变重")


def test_border_strong_value_is_untouched():
    """`--border-strong` 的色值不许为了「修按钮」被调深。

    防的是图省事：把 `#d6d6db` 改成 `#86868d` 也能让按钮过 3:1，
    代价是卡片描边、区块分隔线全部变重——拿全局换局部。
    """
    assert _token(_light_block(), "--border-strong") == "#d6d6db"
    assert _token(_dark_block(), "--border-strong") == "#3a3a42"


# ---------------------------------------------------------------- §4 开关


def test_switch_on_uses_track_on_not_brand():
    """开态轨道必须用 `--track-on`，不许直接铺 `--brand`。

    开关的填充是**唯一**表达开/关的信号（没有文字、没有边框），所以它必须
    ≥3:1。`--brand` 对 `--bg` 只有 2.19:1——反讽的是关态 3.11 过了、开态没过。
    """
    body = _rule(SHELL, '.switch[aria-checked="true"]')
    assert "--track-on" in body, f"开态轨道应当用 --track-on，实际：{body!r}"
    assert "background: var(--brand)" not in body, (
        "开态轨道又铺回了 --brand（对 --bg 2.19:1）。要修就改 --track-on，"
        "别改 --brand——它还用在状态点、进度线上，那些地方不要求 3:1")


def test_brand_value_is_frozen():
    """`--brand` 的亮色值被裁定冻结在 `#00c29a`。

    PM 的硬约束：改它会连带改状态点、进度线、ring。要过对比度就新开令牌。
    这条红了先问「是不是真的要让状态点和进度线一起变色」，别顺手改数字。
    """
    assert _token(_light_block(), "--brand") == "#00c29a"


def test_track_on_meets_three_to_one_on_page_background():
    """`--track-on` 对 `--bg` 必须 ≥3:1，两个主题各算一遍。

    这是它存在的唯一理由。3.0 是 WCAG 1.4.11 对非文本边界的门槛。
    """
    for name, block in (("light", _light_block()), ("dark", _dark_block())):
        bg = _token(block, "--bg")
        on = _token(block, "--track-on")
        r = _ratio(on, bg)
        assert r >= 3.0, f"{name} 主题：--track-on {on} 对 --bg {bg} 只有 {r:.2f}:1"


def test_track_on_is_the_same_hue_as_brand():
    """新色必须还是「同一个绿」，不许变成墨绿/深青。

    判据是色相不偏。用 HSL 的 H 通道比：差超过 10° 就不是同一个颜色家族了，
    那会让开关看起来像换了个品牌色。亮度差多少不管——那正是要改的东西。
    """
    def hue(h: str) -> float:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        mx, mn = max(r, g, b), min(r, g, b)
        if mx == mn:
            return 0.0
        d = mx - mn
        if mx == r:
            hh = (g - b) / d % 6
        elif mx == g:
            hh = (b - r) / d + 2
        else:
            hh = (r - g) / d + 4
        return hh * 60

    light = _light_block()
    diff = abs(hue(_token(light, "--track-on")) - hue(_token(light, "--brand")))
    assert diff <= 10, f"色相差 {diff:.1f}°，已经不是同一个绿了"
