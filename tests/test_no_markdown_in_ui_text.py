"""用户看得见的文案里不许出现 markdown 加粗记号（`**` / `__`）。

## 为什么

`ctx.el(tag, cls, text)` 走的是 `shell.js` 里的 `n.textContent = String(text)`。
**textContent 不解析 markdown**，所以字符串里写 `**密码要填授权码**`，页面上就是
两个字面星号。`templates.js` 在 W7.0 踩过一次（第一版截图里看得见），
同一次提交里 `guide.js` 又写了一处——**修了一个实例，没修一类**。

这一页的判据是「字符串字面量里 `**` 和 `__` 必须为 0」。要加粗就用 `<strong>`
元素，或者换一种不依赖渲染的说法（「注意：」比加粗更醒目，纯文本在任何通道里
都不会翻车）。

## 为什么只查这两个记号

| 记号 | 为什么不查 |
|---|---|
| `#` | 撞颜色值 `#fff`、路由名 `#channels` |
| `|` | 撞 `a \\|\\| b` |
| `` ` `` | 撞模板串和 markdown 代码段的正经用法 |

加粗是唯一「写了就一定想渲染」的记号，误报率最低。**只查会误报的东西不如不查**：
一条隔三差五假红的守卫，结局是被 skip 掉。

## 扫描器复用

`tests/js_source.py` 的 `string_literals()`（W7.1 从同一个状态机派生）。
不自己写正则：`"a\\"b"` 会被切错，模板串跨行也会被切错。
"""

from __future__ import annotations

from pathlib import Path

from js_source import ASSETS, string_literals

# 会渲染成字面星号的两个记号。加粗是唯一「写了就一定想渲染」的，
# 其它 markdown 记号（# | ` ）误报率太高，不在这里管。
BOLD_MARKS = ("**", "__")


def _js_files() -> list[Path]:
    """`pages/*.js` + `assets/*.js`（shell.js / theme-boot.js）。

    取 `assets/*.js` 而不是只写 `shell.js`：将来往 assets/ 下加一个 js
    就自动被扫到，不用回来改名单。
    """
    return sorted(ASSETS.glob("pages/*.js")) + sorted(ASSETS.glob("*.js"))


def _scan_text(src: str, label: str) -> list[str]:
    """返回违规清单。`label` 只用于报错信息里指位置。"""
    bad = []
    for line, text in string_literals(src):
        for mark in BOLD_MARKS:
            if mark in text:
                bad.append(f"{label}:{line}: 文案里有 {mark!r} → {text[:70]!r}")
    return bad


def test_no_bold_markers_in_user_visible_strings():
    """全项目扫一遍：字符串字面量里 `**` / `__` 必须为 0。"""
    files = _js_files()
    # 扫不到文件的守卫是永远绿的装饰
    assert len(files) >= 7, f"只扫到 {len(files)} 个 js 文件：{[p.name for p in files]}"

    bad: list[str] = []
    for path in files:
        bad += _scan_text(path.read_text(encoding="utf-8"),
                          path.relative_to(ASSETS).as_posix())
    assert not bad, (
        "这些文案会原样显示星号（textContent 不解析 markdown）：\n"
        + "\n".join(bad)
        + "\n改法：去掉星号用「注意：」，或者用 <strong> 元素。")


def test_scanner_catches_a_known_bad_string(tmp_path):
    """**扫描器自检**：塞一个已知坏样本，必须扫得出来。

    没有这条的话，「扫描器坏了」和「代码干净了」在测试输出里长得一模一样
    ——W6.3 的对比度扫描器第一版就是这个病（把选中态填充也当成边界，
    报了 12 处无关项，看着「扫得很勤快」，其实判据是错的）。

    这个样本同时钉住三件事：
    1. 字符串里的 `**` 要报
    2. 注释里的 `**` 不报（注释是给人读的，不渲染）
    3. 转义引号 `"a\\"b"` 不能被切错（正则在这里会断在 `\\"` 上）
    """
    sample = (
        'var a = "正常文案";\n'                       # 1
        'var b = "**加粗**";\n'                       # 2 ← 唯一该报的
        '/* 注释里写 **加粗** 不算，它不渲染 */\n'      # 3
        'var c = "a\\"b**c";\n'                       # 4 ← 转义引号后面还有星号
        '// 行尾注释里的 ** 也不算\n'                   # 5
        'var d = `模板串 ** 也不许`;\n'                # 6 ← 反引号也算字符串
    )
    bad = _scan_text(sample, "sample.js")
    lines = sorted(int(x.split(":")[1]) for x in bad)
    assert lines == [2, 4, 6], (
        f"扫描器没按预期报，实际报了第 {lines} 行（期望 2/4/6）：\n" + "\n".join(bad))

    # 再落一次盘，证明「读文件」这条路也通（上面的 sample 是内存字符串）
    p = tmp_path / "probe.js"
    p.write_text(sample, encoding="utf-8")
    assert len(_scan_text(p.read_text(encoding="utf-8"), "probe.js")) == 3
