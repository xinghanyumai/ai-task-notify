/* 首屏主题同步脚本。**必须放在 <head> 里，并且是普通同步脚本。**

   放 body 之后或加 defer 都会闪一下白：样式已经生效、body 已经画出来，
   这段还没跑，等它把 data-theme 改成 dark 时用户已经看到一帧浅色了。

   为什么不用内联 <script>：CSP 的 script-src 是 'self'，不带 'unsafe-inline'
   （W2 §2.4 的总闸）。内联要么放宽 CSP、要么给 sha256 白名单——后者每改一次
   这段代码就得重算一次哈希，是个必然会忘记维护的坑。一个 12 行的外部文件更省事。

   localStorage 在 file:// 下可能抛 SecurityError（Chromium 对 opaque origin
   的处理不完全一致），所以全程 try/catch：拿不到就退回「跟随系统」，
   主题的持久化还有 config.json 的 ui.theme 兜底（见 shell.js 的 applyFromConfig）。
*/
(function () {
  "use strict";


  var mode = "system";
  try {
    var saved = localStorage.getItem("wbn-theme");
    if (saved === "light" || saved === "dark" || saved === "system") mode = saved;
  } catch (e) {
    /* file:// 下拿不到 localStorage 不算错误，静默降级 */
  }

  var dark = mode === "dark";
  if (mode === "system") {
    try {
      dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    } catch (e2) {
      dark = false;
    }
  }

  var root = document.documentElement;
  root.dataset.theme = dark ? "dark" : "light";
  root.dataset.themeMode = mode;
})();
