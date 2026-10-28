"""W3.5 截图启动器：可控窗口。

比 W3.4 那个多两件事：

1. **控制通道**。截图要在「操作进行到一半」的时候拍（保存 B 之后、
   切页之前），而外部进程没法直接调 `win.evaluate_js`。所以这里起一个线程
   盯 `--control` 目录里的 `cmd.js`：文件一变就执行，输出写回 `out.txt`。
   截图脚本只负责「写指令 → 截图 → 写指令」。

2. **页面内 console 收集器**（`--collect`）。WebView2 的 DevTools 窗口不好
   自动化（要手动切到 Console 标签），所以注入一段收集器，把 `console.error`
   / `window.onerror` / `unhandledrejection` 收进数组，再画到页面右上角一个
   浮层里——截的是真输出，浮层只是显示器。

    python tools/launch_w35.py --scenario drafts --page channels --control tmp/w35
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import w35_scenarios  # noqa: E402

# 收集器：覆盖三种错误来源，另外把 console.error 也记下来。
# 浮层用 fixed + 高 z-index，只为了截图能看见，不影响页面布局。
COLLECT_JS = r"""
(function () {
  if (window.__wbnLog) return;
  var box = [];
  window.__wbnLog = box;
  function push(kind, args) {
    try {
      box.push(kind + ': ' + Array.prototype.map.call(args, function (a) {
        if (a instanceof Error) return a.name + ': ' + a.message;
        if (typeof a === 'object') { try { return JSON.stringify(a); } catch (e) { return '[obj]'; } }
        return String(a);
      }).join(' '));
      paint();
    } catch (e) { /* 收集器自己不许炸 */ }
  }
  var panel = null;
  function paint() {
    if (!panel) {
      panel = document.createElement('pre');
      panel.id = '__wbn_log_panel';
      panel.style.cssText = 'position:fixed;right:8px;top:8px;z-index:99999;' +
        'max-width:520px;max-height:260px;overflow:auto;margin:0;' +
        'padding:8px 10px;border-radius:6px;font:11px/1.5 Consolas,monospace;' +
        'background:rgba(20,20,20,.92);color:#9ef;white-space:pre-wrap;' +
        'border:1px solid #3a3a3a;pointer-events:none';
      document.body.appendChild(panel);
    }
    panel.textContent = 'console (' + box.length + ' 条)\n' + (box.join('\n') || '（空）');
  }
  var orig = console.error;
  console.error = function () { push('error', arguments); return orig.apply(console, arguments); };
  var warn = console.warn;
  console.warn = function () { push('warn', arguments); return warn.apply(console, arguments); };
  window.addEventListener('error', function (e) { push('onerror', [e.message]); });
  window.addEventListener('unhandledrejection', function (e) {
    push('rejection', [(e.reason && e.reason.message) || e.reason]);
  });
  paint();
})();
"""


def _watch_control(win, ctrl: Path) -> None:
    """盯 `cmd.js`，变了就执行，结果写 `out.txt`。"""
    cmd = ctrl / "cmd.js"
    out = ctrl / "out.txt"
    last = 0.0
    while True:
        try:
            m = cmd.stat().st_mtime
        except OSError:
            time.sleep(0.2)
            continue
        if m > last:
            last = m
            code = cmd.read_text(encoding="utf-8")
            try:
                res = win.evaluate_js(code)
            except Exception as e:            # pragma: no cover - GUI 回调
                res = f"__ERROR__ {type(e).__name__}: {e}"
            try:
                out.write_text(
                    res if isinstance(res, str) else json.dumps(res, ensure_ascii=False),
                    encoding="utf-8")
            except Exception:
                pass
        time.sleep(0.2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="W3.5 截图启动器")
    ap.add_argument("--scenario", help="用 w35_scenarios 造一个场景")
    ap.add_argument("--dir", help="直接用已经准备好的目录（和 --scenario 二选一）")
    ap.add_argument("--page", default="status")
    ap.add_argument("--control", help="控制目录：轮询 cmd.js")
    ap.add_argument("--collect", action="store_true", help="注入 console 收集器")
    ap.add_argument("--delay", type=float, default=2.5,
                    help="窗口起来后多久注入/开始盯控制文件")
    args = ap.parse_args(argv)

    if args.scenario:
        tmp = w35_scenarios.build(args.scenario)
        print(f"场景 {args.scenario}: {tmp}", flush=True)
    elif args.dir:
        tmp = Path(args.dir)
        w35_scenarios._isolate(tmp)
    else:
        ap.error("要 --scenario 或 --dir")

    import app

    if args.page != "status":
        import webview

        _orig = webview.create_window

        def _patched(title, url, *a, **kw):
            return _orig(title, f"{url}#{args.page}", *a, **kw)

        webview.create_window = _patched

    if not args.collect and not args.control:
        return app.run_ui()

    import webview

    _orig_run = webview.start

    def _start(*a, **kw):
        def boot():
            wins = list(getattr(webview, "windows", []) or [])
            if not wins:
                return
            win = wins[0]
            time.sleep(args.delay)
            if args.collect:
                try:
                    win.evaluate_js(COLLECT_JS)
                except Exception as e:
                    print(f"注入收集器失败：{e}", flush=True)
            if args.control:
                ctrl = Path(args.control)
                ctrl.mkdir(parents=True, exist_ok=True)
                (ctrl / "out.txt").write_text("", encoding="utf-8")
                threading.Thread(target=_watch_control, args=(win, ctrl),
                                 daemon=True).start()
                print(f"控制目录: {ctrl}（写 cmd.js 执行）", flush=True)

        threading.Thread(target=boot, daemon=True).start()
        return _orig_run(*a, **kw)

    webview.start = _start
    return app.run_ui()


if __name__ == "__main__":
    sys.exit(main())
