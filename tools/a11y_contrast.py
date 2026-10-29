"""扫交互控件的真实对比度。**这是守卫，不是一次性实测。**

W6 复核发现「浅色开关 ON 态只有 2.19:1」是**偶然**——当时正好量到了那个元素。
下次加一个控件，同样的问题会再漏一次。所以判据不是「W6 那两处修好了」，
而是「**这个脚本能扫出 W6 那两处**」。扫不出已知问题的扫描器，绿了也没意义。

## 量什么

每个交互控件（`button` / `input` / `select` / `textarea` / `[role=switch]` /
`[role=checkbox]` / `[role=radio]` / `a[href]`）量两项，都是 **`getComputedStyle`
的真实计算值**，不读令牌字面值：

**边界**（WCAG 1.4.11 非文本，要 ≥3:1）——「能不能认出这里有个控件」

    有可见边框   → 边框色，分别对**外侧底色**和**自身底色**算，取最差
    无边框 + 无文字 → 自身底色对**外侧底色**（填充是它唯一的身份信号）

**填充**（只报告，不判定）

    自身底色对**外侧底色**。带文字的控件靠文字表达身份，填充只是分组 /
    悬停提示，不承担「识别控件」。`.chip` 就是这种：`--bg-subtle` 对页面底
    1.15:1，但它有 `chip-token` + `chip-desc` 两行文字（16:1），
    要求它的填充 3:1 是把装饰当信息。

**文字**（WCAG 1.4.3，要 ≥4.5:1）——「文字读不读得清」

    文字色对**自身底色**（自身透明时向上找第一个不透明底）

「外侧底色」= 向上找第一个不透明背景，**不是硬编码 `--bg`**。控件在卡片里和在
页面底，压着的底色不同，比值也不同——这一条有专门的验证法（见任务书 §10.2 第 3 条）。

## 四条不算的，都是有意为之

1. **`box-shadow` 不算边界**。开关开态有一个 `inset 0 0 0 1px var(--brand-ink)`
   的内描边，按 box-shadow 算能得 5.16:1，但 1px 内描边在正常距离上看不出
   开/关的区别。PM 裁定「按填充算」（2.19 是缺陷），所以这里只认 `border`。
2. **透明底 + 无边框 + 有文字的元素只查文字**。侧栏导航项、`.chip`、
   `.seg-item` 的选中态都属这类：身份由文字表达，文字 7–16:1 达标，
   选中态的那点底色差是装饰。
3. **`:disabled` 元素跳过**。WCAG 1.4.3 / 1.4.11 对「失效的控件」豁免，
   而 `.btn:disabled { opacity: .5 }` 会把边界冲淡到 1.5 左右——算进来全是噪音。
   （`aria-disabled` **不跳过**：它只是语义标记，元素仍然可聚焦、可读。）
4. **填充单独一列，只报告不判定**。见上。

## 用法

    python tools/a11y_contrast.py              # 自己起应用，扫完关掉
    python tools/a11y_contrast.py --attach     # 连已经在跑的（CDP 9333）
    python tools/a11y_contrast.py --json out.json

有任一不达标 → 退出码 1。

**依赖**：`pip install playwright`（不下载浏览器，连系统已装的 Edge / WebView2）。
只在跑这个脚本时需要，运行程序不需要，也不要打进 exe。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ROUTES = ["status", "channels", "triggers", "templates", "guide"]
THEMES = ["light", "dark"]

# 非文本对比度门槛（WCAG 1.4.11）与文字门槛（1.4.3）
NEED_BOUNDARY = 3.0
NEED_TEXT = 4.5

# 交互控件选择器。`a[href]` 现在一个都没有，留着是为了以后加链接时自动纳入。
SELECTOR = (
    "button, input, select, textarea, "
    "[role=switch], [role=checkbox], [role=radio], a[href]"
)

# 在页面里跑的测量函数。**整段是一个函数表达式**，方便 `evaluate` 直接调。
MEASURE_JS = r"""
(sel) => {
  const parse = (s) => {
    const m = String(s || "").match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(",").map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const hex = (c) => c ? "#" + [c.r, c.g, c.b].map(
    (v) => Math.round(v).toString(16).padStart(2, "0")).join("") : "—";
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const ratio = (a, b) => {
    if (!a || !b) return null;
    const l1 = lum(a), l2 = lum(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  };
  // 把半透明色叠到不透明底上
  const over = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  });
  // 向上找第一个不透明背景（含自身）。**这就是「实际压着的背景」。**
  const stack = (el) => {
    const layers = [];
    let cur = el;
    while (cur) {
      const c = parse(getComputedStyle(cur).backgroundColor);
      if (c && c.a > 0) {
        layers.push(c);
        if (c.a >= 0.999) break;
      }
      cur = cur.parentElement;
    }
    let out = { r: 255, g: 255, b: 255, a: 1 };  // 一路透明就到白纸
    for (let i = layers.length - 1; i >= 0; i--) out = over(layers[i], out);
    return out;
  };
  const behind = (el) => {
    let cur = el.parentElement;
    while (cur) {
      const c = parse(getComputedStyle(cur).backgroundColor);
      if (c && c.a >= 0.999) return c;
      if (c && c.a > 0) {
        const base = behind(cur);
        return over(c, base);
      }
      cur = cur.parentElement;
    }
    return { r: 255, g: 255, b: 255, a: 1 };
  };
  const sig = (el) => {
    const cls = (typeof el.className === "string" ? el.className : "")
      .trim().split(/\s+/).filter(Boolean).join(".");
    const role = el.getAttribute("role");
    const state = el.getAttribute("aria-checked") === "true" ? ":on" : "";
    return el.tagName.toLowerCase() + (cls ? "." + cls : "")
      + (role ? "[" + role + "]" : "") + state;
  };
  const visibleText = (el) => {
    const t = (el.tagName === "INPUT" || el.tagName === "TEXTAREA")
      ? (el.value || "") : (el.textContent || "");
    return t.trim();
  };

  const rows = [];
  for (const el of document.querySelectorAll(sel)) {
    if (el.disabled) continue;                       // 失效控件豁免
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;        // 不可见 / 零尺寸
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none") continue;
    if (parseFloat(cs.opacity) === 0) continue;

    const ownBg = parse(cs.backgroundColor);
    const ownOpaque = ownBg && ownBg.a >= 0.999;
    const outBg = behind(el);
    const inBg = ownOpaque ? ownBg : stack(el);

    // ---- 边界：有边框认边框；无边框且**没有文字**才认填充
    const txt = visibleText(el);
    const isSwitch = el.getAttribute("role") === "switch";
    const bw = parseFloat(cs.borderTopWidth) || 0;
    const bc = parse(cs.borderTopColor);
    const fillVsOut = ownOpaque ? ratio(ownBg, outBg) : null;
    let boundary = null, bWhy = "";
    if (bw > 0 && bc && bc.a > 0) {
      const vsOut = ratio(bc, outBg);
      const vsIn = ratio(bc, inBg);
      boundary = Math.min(vsOut, vsIn);
      bWhy = "border " + hex(bc);
    } else if (!txt && ownOpaque && fillVsOut !== null && fillVsOut > 1.02) {
      // 没有文字也没有边框 —— 填充就是它唯一的身份信号（开关就是这种）
      boundary = fillVsOut;
      bWhy = "fill " + hex(ownBg);
    }

    // ---- 文字
    const text = (!txt || isSwitch) ? null : ratio(parse(cs.color), inBg);

    rows.push({
      sig: sig(el),
      route: null,
      boundary: boundary === null ? null : +boundary.toFixed(2),
      boundaryWhy: bWhy,
      boundaryFg: bw > 0 && bc && bc.a > 0 ? hex(bc) : (ownOpaque ? hex(ownBg) : "—"),
      boundaryBg: hex(outBg),
      fill: fillVsOut === null ? null : +fillVsOut.toFixed(2),
      text: text === null ? null : +text.toFixed(2),
      textFg: hex(parse(cs.color)),
      textBg: hex(inBg),
      sample: txt.slice(0, 18),
    });
  }
  return rows;
}
"""


# ---------------------------------------------------------------- 起应用


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _launch(port: int) -> subprocess.Popen:
    """起一个真实应用，并把 CDP 端口开在 `port`。

    **不改 app.py**：`WBN_BENCH=1` 跳过单实例锁（`bench_ui.py` 用的就是这条），
    `REMOTE_DEBUGGING_PORT` 是 pywebview 自己的设置项
    （`webview/__init__.py` 默认 None，`platforms/edgechromium.py` 读到非 None
    就拼进 `AdditionalBrowserArguments`）。所以用 `-c` 把它塞进去再 import app。
    """
    code = (
        "import sys;"
        f"sys.path.insert(0, {str(ROOT)!r});"
        "import webview;"
        f"webview.settings['REMOTE_DEBUGGING_PORT'] = {port};"
        "import app;"
        "sys.exit(app.run_ui())"
    )
    env = dict(os.environ, WBN_BENCH="1")
    return subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_port(port: int, timeout: float = 40.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if _port_open(port):
            return True
        time.sleep(0.3)
    return False


# ---------------------------------------------------------------- 扫


def _connect(port: int):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("缺少 playwright。装一下：\n    pip install playwright\n（不下载浏览器，连系统已装的 Edge / WebView2）")
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    return pw, browser


def _goto(page, route: str) -> None:
    page.evaluate("(r) => { location.hash = '#' + r; }", route)
    page.wait_for_timeout(420)


def _set_theme(page, theme: str) -> None:
    page.evaluate(
        "(t) => { document.documentElement.dataset.theme = t;"
        " if (document.body) document.body.dataset.theme = t; }",
        theme,
    )
    page.wait_for_timeout(260)


def scan(page) -> list[dict]:
    rows: list[dict] = []
    for theme in THEMES:
        _set_theme(page, theme)
        for route in ROUTES:
            _goto(page, route)
            got = page.evaluate(MEASURE_JS, SELECTOR)
            for r in got:
                r["theme"] = theme
                r["route"] = route
            rows.extend(got)
    return rows


def collapse(rows: list[dict]) -> list[dict]:
    """同一个签名 + 同一个比值 + 同一个底色的，合并成一行带计数。

    不合并的话 5 个页面 × 10 个通道开关会有几十行完全一样的记录；
    合并过头（只按签名）又会把「卡片内的按钮」和「页面底的按钮」并成一行，
    那正好毁掉「背景是实际压着的」这条判据——它们比值不同，必须各占一行。
    """
    seen: dict[tuple, dict] = {}
    for r in rows:
        key = (r["theme"], r["sig"], r["boundary"], r["boundaryBg"],
               r["fill"], r["text"], r["textBg"], r["boundaryWhy"])
        if key in seen:
            seen[key]["count"] += 1
            if r["route"] not in seen[key]["routes"]:
                seen[key]["routes"].append(r["route"])
        else:
            seen[key] = dict(r, count=1, routes=[r["route"]])
    return list(seen.values())


def verdicts(rows: list[dict]) -> list[dict]:
    for r in rows:
        fails = []
        if r["boundary"] is not None and r["boundary"] < NEED_BOUNDARY:
            fails.append("boundary")
        if r["text"] is not None and r["text"] < NEED_TEXT:
            fails.append("text")
        r["fails"] = fails
    return rows


def report(rows: list[dict]) -> None:
    for theme in THEMES:
        sub = [r for r in rows if r["theme"] == theme]
        bad = [r for r in sub if r["fails"]]
        print(f"\n{'=' * 82}\n{theme}  —  {len(sub)} 行，{len(bad)} 处不达标\n{'=' * 82}")
        print(f"{'元素':<32}{'边界':>7}{'填充':>7}{'文字':>7}  底色      判定")
        for r in sorted(sub, key=lambda x: (not x["fails"], x["boundary"] or 99,
                                            x["text"] or 99)):
            b = "—" if r["boundary"] is None else f"{r['boundary']:.2f}"
            f = "—" if r["fill"] is None else f"{r['fill']:.2f}"
            t = "—" if r["text"] is None else f"{r['text']:.2f}"
            mark = "FAIL " + ",".join(r["fails"]) if r["fails"] else "ok"
            n = f" ×{r['count']}" if r["count"] > 1 else ""
            print(f"{r['sig'][:31]:<32}{b:>7}{f:>7}{t:>7}  {r['boundaryBg']:<9} {mark}{n}")
            if r["fails"]:
                why = r["boundaryWhy"] or "—"
                print(f"{'':<32}{'':>7}{'':>7}{'':>7}  ↑ {why}  fg={r['boundaryFg']}"
                      f"  文字 fg={r['textFg']} bg={r['textBg']}"
                      f"  {'/'.join(r['routes'])}")


def main() -> int:
    ap = argparse.ArgumentParser(description="扫交互控件的真实对比度")
    ap.add_argument("--attach", action="store_true",
                    help="连已经在跑的应用（CDP 端口已开），不自己起")
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--json", type=Path, default=None, help="把明细写成 JSON")
    args = ap.parse_args()

    proc = None
    if args.attach:
        if not _port_open(args.port):
            sys.exit(f"{args.port} 端口没人监听。要么去掉 --attach，要么先起应用")
    else:
        if _port_open(args.port):
            sys.exit(f"{args.port} 已被占用。先关掉那个实例，或加 --attach 复用它")
        proc = _launch(args.port)
        if not _wait_port(args.port):
            proc.terminate()
            sys.exit("应用没起来（CDP 端口没开）。看看 app.py 的日志")

    pw = None
    try:
        pw, browser = _connect(args.port)
        page = browser.contexts[0].pages[0]
        page.wait_for_timeout(1200)          # 等首屏挂载完
        rows = verdicts(collapse(scan(page)))
        report(rows)
        bad = [r for r in rows if r["fails"]]
        print(f"\n合计 {len(rows)} 行，{len(bad)} 处不达标")
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(
                json.dumps({"rows": rows, "need": {"boundary": NEED_BOUNDARY,
                                                   "text": NEED_TEXT}},
                           ensure_ascii=False, indent=1),
                encoding="utf-8")
            print(f"明细已写入 {args.json}")
        return 1 if bad else 0
    finally:
        # **不能 `browser.close()`**：CDP 连的就是应用本身，close 会把真实窗口
        # 一起关掉。停 playwright 就够，进程由下面 terminate。
        if pw is not None:
            pw.stop()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
