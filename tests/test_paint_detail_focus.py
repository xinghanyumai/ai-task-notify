"""`paintDetail()` 会销毁 pane 里的所有节点，正在聚焦的那个跟着没了，焦点掉到 `body`。

## 病

`channels.js` 的 `paintDetail()` 第一行是 `pane.textContent = ""`。**任何在 pane 里
聚焦的元素都会在重建时被移除**。W6.4 修了 `saveChannel` 和 `toggle`（重建后把焦点
还回新节点），但**漏了「获取 openid 成功」那处**。

漏的原因不是不小心，是那句话：`paintDetail()` 的 docstring 当时写着
「以后再加调用点，照这个写」。**「照这个写」靠人记住，而同一轮里已经漏了一次。**
再加两个调用点就会再漏两次。

所以这条守卫不检查「某一行写对没写对」，检查**每个调用点有没有交代焦点怎么办**。

## 判据

1. 扫 `webui/assets/pages/channels.js` 里所有 `paintDetail()` 的**调用**
   （不是函数定义，不是注释里提到的——`code_only()` 负责剥注释）
2. 每个调用点必须满足其一：
   - 调用行起的 3 行内出现 `.focus(` 或 `focusFirstMissing(`（还原焦点）
   - 调用行或紧邻上一行有 `/* focus-ok: <理由> */`（说明为什么这次不会丢焦点）
3. 还原焦点那行必须带 `hadFocus` 条件——**无条件 `focus()` 会把用户主动移开的
   焦点硬拽回来**，那是另一种病（异步保存期间用户可能已经 Tab 走了）
4. 豁免理由要写清「为什么这次重建不会销毁聚焦节点」，不许写「历史原因」

## 为什么豁免写在源码里，不写成测试里的名单

W6.4 的 `.disabled` 守卫用了一张外部名单，那是为了**分类**（语义禁用 vs 临时禁用）
必须集中看。这里不一样：豁免要回答的是「这一行重建时焦点在不在 pane 里」，
**答案只有站在那一行上才看得懂**。写在别处，下一个人读代码时看不到，改的时候
也不会想起来去改名单。

代价是守卫没法「防止名单烂掉」——但豁免直接贴在代码上，代码删了注释也就没了，
不存在「名单里留着一条已不存在的条目」这种腐烂。
"""

from __future__ import annotations

import re

from js_source import ASSETS, code_only, raw_lines

CHANNELS = ASSETS / "pages" / "channels.js"

_CALL = re.compile(r"\bpaintDetail\(\)")
_DEF = re.compile(r"function\s+paintDetail\s*\(")
_FOCUS_OK = re.compile(r"focus-ok:\s*(.+?)\s*(?:\*/|$)")


def _scan() -> tuple[list[str], list[str], list[int]]:
    """返回 (代码行, 原始行, 调用点行号)。行号从 1 开始，两边对齐。"""
    src = CHANNELS.read_text(encoding="utf-8")
    code = code_only(src)
    raw = raw_lines(src)
    assert len(code) == len(raw), "剥注释改了行数，行号对不上了"

    lines = []
    for no, line in enumerate(code, 1):
        if _DEF.search(line):
            continue
        if _CALL.search(line):
            lines.append(no)
    return code, raw, lines


def _restore_window(code: list[str], line: int) -> list[str]:
    """调用行 + 其后 3 行（任务书 §2.2 的「后续 3 行内」）。"""
    return code[line - 1:line + 3]


def _exempt_reason(raw: list[str], line: int) -> str | None:
    """调用行或紧邻上一行里的 `/* focus-ok: 理由 */`。"""
    for ln in (raw[line - 1], raw[line - 2] if line >= 2 else ""):
        m = _FOCUS_OK.search(ln)
        if m:
            return m.group(1).strip()
    return None


def _classify() -> tuple[list[dict], list[dict], list[dict]]:
    """把每个调用点分成 已还原 / 豁免 / 没人管 三堆。"""
    code, raw, lines = _scan()
    restored, exempt, missing = [], [], []
    for line in lines:
        window = _restore_window(code, line)
        if any(".focus(" in ln or "focusFirstMissing(" in ln for ln in window):
            restored.append({"line": line, "window": window})
            continue
        reason = _exempt_reason(raw, line)
        if reason is not None:
            exempt.append({"line": line, "reason": reason})
            continue
        missing.append({"line": line, "code": code[line - 1].strip()})
    return restored, exempt, missing


def test_every_call_site_restores_focus_or_says_why():
    """每个 `paintDetail()` 调用点都要交代焦点：还原，或者写清为什么不用还原。"""
    _, _, missing = _classify()
    assert not missing, (
        "下面这些地方调了 `paintDetail()`，但既没还原焦点、也没写 `/* focus-ok: 理由 */`。\n"
        "`paintDetail()` 会 `pane.textContent = \"\"`，pane 里正在聚焦的节点会被销毁，\n"
        "焦点掉到 body，键盘用户下一个 Tab 从页首重走。\n\n"
        "修法（照 saveChannel 写）：重建前 `var hadFocus = document.activeElement === 节点;`，\n"
        "重建后 `if (hadFocus && state.nodes.节点) state.nodes.节点.focus();`。\n"
        "如果这次重建时焦点确实不在 pane 里，就在那一行后面写 `/* focus-ok: 为什么 */`。\n\n"
        + "\n".join(f"  channels.js:{m['line']}  {m['code']}" for m in missing))


def test_focus_restore_is_conditional_on_had_focus():
    """还原焦点必须带 `hadFocus` 条件。

    无条件 `focus()` 更糟：保存要等一次 IPC，这期间用户可能已经 Tab 到别处了，
    回来一看焦点被硬拽回按钮上。**「焦点不丢」不等于「焦点必须回到按钮上」。**
    """
    restored, _, _ = _classify()
    for site in restored:
        window = "\n".join(site["window"])
        if "focusFirstMissing(" in window and ".focus(" not in window:
            continue          # 刻意的焦点移动，见 toggle 缺字段回弹那支
        assert "hadFocus" in window, (
            f"channels.js:{site['line']} 的还原没有 `hadFocus` 条件——"
            f"用户已经移开的焦点会被硬拽回来。\n"
            f"  窗口内容：{window.strip()[:200]}")


def test_exemptions_have_a_substantive_reason():
    """豁免理由要说明「为什么这次重建不会销毁聚焦节点」。"""
    _, exempt, _ = _classify()
    assert exempt, "一条豁免都没扫到——扫描器坏了，别当成通过"
    for site in exempt:
        reason = site["reason"]
        assert len(reason) >= 12, (
            f"channels.js:{site['line']} 的豁免理由太短：{reason!r}。"
            f"至少写清「焦点在哪、为什么不在 pane 里」")
        assert "焦点" in reason, (
            f"channels.js:{site['line']} 的豁免理由没提「焦点」：{reason!r}。"
            f"这条注释要回答的就是「焦点在哪」，不提焦点等于没写")


def test_call_site_audit_is_complete():
    """调用点数量与分类必须和逐个审过的结果一致。

    这条会在新增/删除调用点时红。**那不是麻烦，是它唯一的作用**：逼你回来看一眼
    新那处归哪一类，看完把下面三个数改掉。数字不更新，上一条测试就只覆盖旧的地方。
    """
    restored, exempt, missing = _classify()
    total = len(restored) + len(exempt) + len(missing)
    assert total == 8, (
        f"扫出 {total} 个 `paintDetail()` 调用点，审过的是 8 个。\n"
        f"注释里提到的两处（`paintDetail` 的 docstring、`toggle` 里那句）不算，"
        f"如果总数变成 10，说明 `code_only()` 没剥干净注释。")
    assert len(missing) == 0
    assert len(restored) == 5, (
        f"已还原 {len(restored)} 处，审过的是 5 处（saveChannel / toggle×3 / openid）")
    assert len(exempt) == 3, (
        f"豁免 {len(exempt)} 处，审过的是 3 处（首次加载 / 切通道 / refresh）")
