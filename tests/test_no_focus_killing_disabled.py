"""**给元素设 `disabled` 会把它 blur 掉，焦点掉到 `body`。** 这条守卫钉住这件事。

## 病是什么

浏览器规范：给一个**已聚焦**的元素设 `disabled = true`，它会失焦，焦点回退到
`body`。键盘用户按一下回车保存，下一个 Tab 就从页面开头重走 30 个元素
（W6 第 10 条基线「Tab 走一圈」测的是**没点保存**的状态，所以一条都没暴露）。

W6.3 只修了总开关那一个实例。**「修了一个实例」和「修了一类」的区别，就是
下次还会不会再犯。** 所以这里不查某一行写错没写错，查**还有没有人在给
已聚焦的元素设 `disabled`**。

## 两种禁用，只有一种是病

| 类型 | 例子 | 该不该留 `disabled` |
|---|---|---|
| **语义禁用**——用户条件不满足 | `test.disabled = !ready`（没填完不能测） | **留**。用户能理解「没填完不能点」，Tab 不到它也是对的 |
| **临时禁用**——程序正忙 | `save.disabled = state.busy` | **不许**。改用 `WBN_UI.setBusyAttr()` + 事件里 `if (busy) return;` |

一刀切把语义禁用也换成 `aria-disabled` 是错的：用户会 Tab 到一个点了没反应的
按钮，比丢焦点更让人困惑。

## 判据

1. 扫 `webui/assets/**/*.js` 里所有 `.disabled =` 赋值
2. 每一条必须命中 `EXEMPT` 里的「文件 + 赋值语句 + 理由」
3. 名单里每一条也必须真的对应一行现存代码（防止名单烂掉）

**第 2 条是这条测试存在的全部意义。** 随便加一行 `btn.disabled = true`
就必须红。只扫不咬的守卫是一个永远绿的装饰。

## 为什么名单用「文件 + 语句」而不是「文件:行」

任务书 §5 写的是「文件:行 + 理由」。行号会随任何一次无关改动漂移，漂了之后
要么测试无故变红、要么有人把行号一改了事——两种都比不写更糟。
这里用 `文件 :: 赋值语句`（空白归一化），**同时钉住右边**：把
`test.disabled = !ready` 改回 `= !ready || state.busy`，右边变了，名单不再命中，
测试当场红。行号只出现在失败信息里，供人定位。
"""

from __future__ import annotations

import re
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "webui" / "assets"

# 赋值语句：`<lhs>.disabled = <rhs>;`。lhs 允许 `state.nodes.save` 这种点链。
_ASSIGN = re.compile(r"(?P<lhs>[A-Za-z_$][\w$.\[\]]*)\.disabled\s*=\s*(?P<rhs>[^;\n]+)")


def _code_only(src: str) -> list[str]:
    """逐行返回**只留代码**的文本：去掉 `//` 行尾注释与 `/* ... */` 块注释。

    为什么不能只看行首：`/* ... */` 会跨行，注释中间那些续行既不以 `*` 开头
    也不以 `//` 开头。本文件自己的注释里就写着 `save.disabled = ...` 当例子，
    只判行首的话扫描器会把这段**注释**当成一处真实赋值报上来——守卫的第一批
    误报就是它。

    顺便避开字符串里的 `//`（URL 里的 `http://` 被当成注释，会把后半行吃掉）。
    字符串里的 `disabled` 也不该算数，所以引号内的内容一并丢弃。
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
    out.append("".join(buf))
    return out


def _norm(text: str) -> str:
    """空白归一化：`!ready  ||  state.busy` 与 `!ready || state.busy` 等价。"""
    return re.sub(r"\s+", " ", text).strip()


def _hits() -> list[dict]:
    """扫出所有 `.disabled =` 赋值。返回 file / line / stmt。

    `stmt` 里的左边只保留**最后一段**：`state.nodes.openid.disabled` 记成
    `openid.disabled`。豁免名单不该被「这个按钮挂在哪个节点下」绑住——
    重构把 `state.nodes.x` 提成局部变量，禁用语义一个字没变，名单却会假红。
    """
    out = []
    for path in sorted(ASSETS.rglob("*.js")):
        src = path.read_text(encoding="utf-8")
        for no, line in enumerate(_code_only(src), 1):
            for m in _ASSIGN.finditer(line):
                lhs = m.group("lhs").split(".")[-1]
                stmt = _norm(f"{lhs}.disabled = {m.group('rhs')}")
                out.append({
                    "file": path.name,
                    "line": no,
                    "stmt": stmt,
                    "where": f"webui/assets/{path.relative_to(ASSETS).as_posix()}:{no}",
                })
    return out


# ---------------------------------------------------------------- 豁免名单
#
# 只有**语义禁用**能进这里。每条必须写「为什么这个禁用不会丢焦点」——
# 半年后没人知道那条为什么被放过，是这类名单最典型的烂法。
#
# 想加一条之前先问：这个按钮的 disabled 是「用户条件不满足」还是「程序正忙」？
# 是后者就用 WBN_UI.setBusyAttr()，不要往这里加。

EXEMPT = [
    ("channels.js", "test.disabled = !ready",
     "语义禁用：凭证没填完不能测。按钮的 title/why 里写明了还差哪几项，"
     "用户能理解「现在不能点」；Tab 跳过它是正确的，因为点了也没有意义。"
     "忙碌时的那半句已拆到 WBN_UI.setBusyAttr(test, state.busy)。"),
    ("channels.js", "openid.disabled = !ok",
     "语义禁用：没填 appID + appsecret 时「自动获取」无从下手，title 里写了原因。"
     "忙碌时的那半句已拆到 WBN_UI.setBusyAttr(openid, state.busy)。"),
]


def _key(file: str, stmt: str) -> tuple[str, str]:
    return (file, _norm(stmt))


_ALLOWED = {_key(f, s): reason for f, s, reason in EXEMPT}


def test_every_disabled_assignment_is_registered():
    """每条 `.disabled =` 都必须在豁免名单里，且名单里每条都要有理由。"""
    hits = _hits()
    assert hits, "一条 .disabled = 都没扫到——正则坏了，别当成通过"

    unregistered = [h for h in hits if _key(h["file"], h["stmt"]) not in _ALLOWED]
    assert not unregistered, (
        "下面这些给元素设了 disabled，会把它 blur 掉、焦点掉到 body。\n"
        "如果它是**临时禁用**（程序正忙），改用 WBN_UI.setBusyAttr(el, busy) "
        "并在事件处理开头 `if (busy) return;`；\n"
        "如果它是**语义禁用**（用户条件不满足），把它加进 EXEMPT 并写清理由。\n\n"
        + "\n".join(f"  {h['where']}  {h['stmt']}" for h in unregistered))


def test_exemptions_are_not_stale():
    """名单里每一条都要真的对应一行现存代码。

    没有这条，名单会越攒越长：代码早改掉了，条目还在，下一个人对着一条
    不存在的豁免查半天。
    """
    hits = {_key(h["file"], h["stmt"]) for h in _hits()}
    stale = [f"{f} :: {s}" for f, s in _ALLOWED if (f, s) not in hits]
    assert not stale, (
        "豁免名单里有条目已经对不上任何代码（改了或删了？）：\n"
        + "\n".join(f"  {x}" for x in stale))


def test_every_exemption_has_a_reason():
    """每条豁免都要有一句人话，不是「历史原因」「暂留」这种。"""
    for file, stmt, reason in EXEMPT:
        assert len(reason) >= 20, f"{file} :: {stmt} 的理由太短：{reason!r}"
        assert "语义禁用" in reason, (
            f"{file} :: {stmt} 的理由里要说明它属于哪一类禁用——"
            f"只有语义禁用可以豁免，临时禁用必须改 setBusyAttr")


def test_busy_attr_helper_exists_and_is_shared():
    """`setBusyAttr` 只在 shell.js 定义一次，三页通过 `WBN_UI` 复用。

    三页各写一份的话，改一处忘两处就是这次要收口的病本身。
    """
    shell = (ASSETS / "shell.js").read_text(encoding="utf-8")
    assert "function setBusyAttr(" in shell, "shell.js 里没有 setBusyAttr"
    assert "WBN_UI" in shell, "shell.js 没把 setBusyAttr 挂到 window.WBN_UI 上"

    definitions = sum(
        1 for p in ASSETS.rglob("*.js")
        if "function setBusyAttr(" in p.read_text(encoding="utf-8"))
    assert definitions == 1, f"setBusyAttr 被定义了 {definitions} 次，应当只有 1 次"


def test_pages_use_the_helper_not_disabled_for_busy():
    """三页的「保存中」状态必须走 setBusyAttr，且**开关两半都在**。

    这条和第一条不同：第一条管「有没有漏网的赋值」，这条管「忙碌态是不是
    走了统一入口」。两件事坏起来方式不一样——漏网是新增，走岔是改法不对。

    为什么不能只查「`WBN_UI.setBusyAttr(` 出现过」：变异测试 M2 把模板页的
    `setBusyAttr(save, true)` 换回 `save.disabled = true`、`false` 那半留着，
    字符串照样在，这条就假绿了。**忙碌态是一对**：置忙和解除忙，少一半就还是
    会丢焦点。所以按「元素 → 用到的右值」配对查：

      - 用字面量 `true` / `false` 的（模板页、触发页），两半都得在
      - 用变量 `state.busy` 的（通道页，置忙/解除都走同一个 `setBusy`），
        两边天然是一对，不查字面量
    """
    calls = re.compile(r"WBN_UI\.setBusyAttr\(\s*(?P<el>[^,]+?)\s*,\s*(?P<val>[^)]+?)\s*\)")
    for name in ("channels.js", "triggers.js", "templates.js"):
        src = (ASSETS / "pages" / name).read_text(encoding="utf-8")
        assert "WBN_UI.setBusyAttr(" in src, (
            f"{name} 没有用 WBN_UI.setBusyAttr —— 忙碌态还是直接设 disabled？")

        pairs: dict[str, set[str]] = {}
        for m in calls.finditer(src):
            pairs.setdefault(m.group("el"), set()).add(m.group("val"))

        for el, vals in pairs.items():
            literals = vals & {"true", "false"}
            if literals and literals != {"true", "false"}:
                raise AssertionError(
                    f"{name} 里 {el} 的忙碌态只写了一半：{sorted(vals)}。"
                    f"置忙和解除忙必须成对，否则按钮会一直挂在 aria-disabled 上。")
