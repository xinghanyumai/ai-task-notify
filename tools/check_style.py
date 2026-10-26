"""样式纪律检查：把「尺度收口」这件事变成可复跑的命令。

为什么要有这个脚本：间距、字号、时长收成令牌之后，真正容易出问题的是
「后来又有人加了一个 18」。靠 grep 贴一次结果只能证明当时是对的，证明
不了以后。这里把验收条件写成断言，改坏了就跑不过。

检查项：
  1 所有 padx/pady 只剩尺度档位（0 除外）
  2 所有 theme.font(..., 数字) 只剩 TS 引用
  3 所有 anim.tween(..., 数字) 只剩 MOTION 引用
  4 令牌表里没有声明了却没人用的项（死令牌比没令牌更误导）
  5 间距尺度自身合法：递增、以 4 的倍数为主、档位数 ≤8
  6 描边语义唯一：分隔线 / 控件 / 焦点环各自只有一个值

用法：python tools/check_style.py
退出码 0 = 通过。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TARGETS = ["ui.py", "wbn/widgets.py"]

FAIL: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'✓' if cond else '✗'} {label}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAIL.append(label)


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 1 间距

PAD = re.compile(r"pad[xy]\s*=\s*(\([^)]*\)|[^,)\s]+)")
BARE_NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])")


def check_padding() -> None:
    print("\n[1] padding 只剩尺度档位")
    for name in TARGETS:
        total, bad = 0, []
        for i, line in enumerate(read(name).splitlines(), 1):
            for m in PAD.finditer(line):
                total += 1
                for d in BARE_NUM.findall(m.group(1)):
                    if float(d) != 0.0:      # 0 是「不留白」，不是档位
                        bad.append((i, m.group(0)))
        ok(not bad, f"{name}：{total} 处 padding，非档位字面量 {len(bad)} 处",
           "；".join(f"L{i} {s}" for i, s in bad[:6]))


# ---------------------------------------------------------------- 2 字号

FONT_NUM = re.compile(r"theme\.font\([^)]*?,\s*(\d+)")


def check_font() -> None:
    print("\n[2] 字号只剩 TS 引用")
    for name in TARGETS:
        hits = [(i, m.group(1))
                for i, line in enumerate(read(name).splitlines(), 1)
                for m in [FONT_NUM.search(line)] if m]
        ok(not hits, f"{name}：theme.font 里直接写数字 {len(hits)} 处",
           "；".join(f"L{i} size={s}" for i, s in hits[:6]))


# ---------------------------------------------------------------- 3 动效

TWEEN_NUM = re.compile(r"anim\.tween\(\s*self\s*,\s*[\"'][^\"']+[\"']\s*,\s*(\d+)")


def check_motion() -> None:
    print("\n[3] 动效时长只剩 MOTION 引用")
    for name in TARGETS:
        hits = [(i, m.group(1))
                for i, line in enumerate(read(name).splitlines(), 1)
                for m in [TWEEN_NUM.search(line)] if m]
        ok(not hits, f"{name}：anim.tween 里直接写毫秒数 {len(hits)} 处",
           "；".join(f"L{i} {d}ms" for i, d in hits[:6]))


# ---------------------------------------------------------------- 4 死令牌

def check_dead_tokens() -> None:
    print("\n[4] 令牌表里没有死项")
    from wbn.tokens import _DIM, _SPACE, _STROKE

    src = "".join(read(n) for n in TARGETS)
    for ns, table in (("SP", _SPACE), ("DIM", _DIM), ("STK", _STROKE)):
        dead = [k for k in table if not re.search(rf"\b{ns}\.{k}\b", src)]
        ok(not dead, f"{ns}：{len(table)} 项全部在用", f"未使用 {dead}")


# ---------------------------------------------------------------- 5 尺度

def check_scale() -> None:
    print("\n[5] 间距尺度自身合法")
    from wbn.tokens import _SPACE

    vals = list(_SPACE.values())
    ok(len(vals) <= 8, f"档位数 {len(vals)} ≤ 8")
    ok(vals == sorted(vals) and len(set(vals)) == len(vals),
       "取值严格递增且不重复")
    off = [v for v in vals if v != 2 and v % 4]
    ok(not off, "除 2 之外都是 4 的倍数", f"例外 {off}")
    ok(min(vals) > 0, "没有 0 或负数档位")


# ---------------------------------------------------------------- 6 描边

def check_stroke() -> None:
    print("\n[6] 描边语义唯一")
    from wbn.tokens import _STROKE

    ok(_STROKE["rule"] == _STROKE["control"],
       f"分隔线与控件描边同为 {_STROKE['rule']}px"
       "（1px 在高 DPI 下才不会被渲染成两档）")
    ok(_STROKE["ring"] > _STROKE["control"], "焦点环比控件描边粗")
    ok(len({_STROKE["icon"], _STROKE["icon_thin"], _STROKE["icon_bold"]}) == 3,
       "图标笔画分三档且互不相同")


# ---------------------------------------------------------------- 7 变体

def check_variants() -> None:
    """主按钮唯一性由 audit.py 的 B1 保证，这里只看变体有没有被用起来。"""
    print("\n[7] 按钮变体清单")
    src = "".join(read(n) for n in TARGETS)
    used = {}
    for m in re.finditer(r'variant="([a-z]+)"', src):
        used[m.group(1)] = used.get(m.group(1), 0) + 1
    print(f"    使用情况：{used}")
    ok(used.get("primary", 0) >= 1, "有主按钮")
    ok(used.get("secondary", 0) >= 1, "有描边次按钮")
    ok(used.get("ghost", 0) >= 1, "有轻量按钮")
    if not used.get("danger"):
        print("    ! danger 变体当前无人使用（产品里还没有破坏性操作），保留为组件契约")


def main() -> None:
    print("样式纪律检查")
    print("=" * 56)
    check_padding()
    check_font()
    check_motion()
    check_dead_tokens()
    check_scale()
    check_stroke()
    check_variants()

    print("\n" + "=" * 56)
    if FAIL:
        print(f"失败 {len(FAIL)} 项：")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
