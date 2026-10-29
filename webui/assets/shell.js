/* 壳：主题 / 路由 / bridge / 侧栏 / 顶栏。
 *
 * 页面模块在 window.WBN_PAGES 上注册（见 pages/status.js），本文件在
 * DOMContentLoaded 后启动——那时候同级的页面脚本已经执行完了，所以
 * index.html 里 shell.js 放在 pages/*.js 之前也不会漏。
 *
 * **不用 ES module**：Chromium 在 file:// 下把 module 的 origin 当作 null，
 * import 会被 CORS 拦掉。所以全是经典脚本 + 一个全局命名空间。
 *
 * 三条不许破的规矩：
 *   1. 所有文本走 textContent（el() 是唯一的建节点入口），禁 innerHTML
 *   2. 每个 promise 都有 .catch —— 漏一个就是一条 Console 报错
 *   3. 颜色/字号/间距只从 tokens.css 取，这里不写任何字面色值
 */
(function () {
  "use strict";

  var ROUTES = ["status", "channels", "triggers", "templates", "guide"];
  var DEFAULT_ROUTE = "status";
  var THEME_KEY = "wbn-theme";
  var API_TIMEOUT = 8000;

  /* 侧栏顺序 ≠ ROUTES 顺序：运行状态是落地页，排第一 */
  var NAV = [
    { id: "status",    label: "运行状态", icon: "M3 12h4l3-8 4 16 3-8h4" },
    { id: "channels",  label: "通知通道", icon: "M4 12l16-8-6 16-3-6-7-2z" },
    { id: "triggers",  label: "触发规则", icon: "M13 3L4 14h7l-1 7 9-11h-7l1-7z" },
    { id: "templates", label: "消息模板", icon: "M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6" },
    { id: "guide",     label: "使用指南", icon: "M4 5a2 2 0 012-2h13v18H6a2 2 0 01-2-2zM8 7h7M8 11h7" }
  ];

  var PLACEHOLDER = {
    status:    "assets/pages/status.js 没加载出来。",
    channels:  "assets/pages/channels.js 没加载出来。",
    triggers:  "assets/pages/triggers.js 没加载出来。",
    templates: "assets/pages/templates.js 没加载出来。",
    guide:     "assets/pages/guide.js 没加载出来。"
  };

  var SVG_NS = "http://www.w3.org/2000/svg";

  var state = {
    route: null,
    mounted: {},
    currentPage: null,   /* 当前页的模块对象，切走时调它的 destroy() */
    theme: "system",
    enabled: true,
    msgTimer: 0
  };

  /* ---- DOM 小工具 -------------------------------------------------------- */

  function $(sel, root) { return (root || document).querySelector(sel); }

  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  /** 建节点。**这是全站唯一允许写文本的地方**，走 textContent。 */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  /** 1.5px 线性描边图标。内联 SVG，不引图标库、不引 CDN。 */
  function icon(d, cls) {
    var s = document.createElementNS(SVG_NS, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("fill", "none");
    s.setAttribute("stroke", "currentColor");
    s.setAttribute("stroke-width", "1.5");
    s.setAttribute("stroke-linecap", "round");
    s.setAttribute("stroke-linejoin", "round");
    s.setAttribute("aria-hidden", "true");
    if (cls) s.setAttribute("class", cls);
    var p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", d);
    s.appendChild(p);
    return s;
  }

  function navById(id) {
    for (var i = 0; i < NAV.length; i++) {
      if (NAV[i].id === id) return NAV[i];
    }
    return null;
  }

  /* ---- bridge ------------------------------------------------------------ */

  var apiPromise = null;

  /** 等 pywebview 注入 window.pywebview.api。8 秒没来就当 WebView2 挂了。 */
  function apiReady() {
    if (apiPromise) return apiPromise;

    apiPromise = new Promise(function (resolve, reject) {
      function tryGet() {
        if (window.pywebview && window.pywebview.api) {
          resolve(window.pywebview.api);
          return true;
        }
        return false;
      }
      if (tryGet()) return;

      var timer = setTimeout(function () {
        reject(new Error("bridge 8 秒没就绪（WebView2 可能没起来）"));
      }, API_TIMEOUT);

      window.addEventListener("pywebviewready", function () {
        if (tryGet()) clearTimeout(timer);
      }, { once: true });
    });

    /* 失败后允许下一次重试（错误态里的「重试」按钮要用） */
    apiPromise.catch(function () { apiPromise = null; });
    return apiPromise;
  }

  /** 调一个 bridge 方法，剥掉 {ok, data} 外壳；失败抛 Error。 */
  function call(name) {
    var args = Array.prototype.slice.call(arguments, 1);
    return apiReady().then(function (api) {
      var fn = api[name];
      if (typeof fn !== "function") {
        throw new Error("bridge 缺少方法 " + name);
      }
      return fn.apply(api, args);
    }).then(function (res) {
      if (!res || res.ok !== true) {
        throw new Error((res && res.error) || "未知错误");
      }
      return res.data;
    });
  }

  /* ---- 未捕获异常落日志 ---------------------------------------------------
   *
   * `theme.install_tk_exception_hook` 的等价物（W5 删 `theme.py` 时一起下线的）。
   * Tk 那版的理由是「回调异常默认只写 stderr，pythonw.exe 下 stderr 是 None，
   * 等于消失」；webview 里形状不同、结果一样：页面抛一个 uncaught error，
   * DevTools 里有，但用户不会开 DevTools，日志里什么都没有，现象还是
   * 「点了没反应」。
   *
   * **直接调 api，不走 call()。** call() 会先 await apiReady()，而 bridge 没就绪
   * 恰恰是最需要记日志的时候——那条路径会 reject 出第二个错误，转着圈报。
   * 返回的 promise 也要吃掉：它 reject 会触发 unhandledrejection，又回到这里。
   */
  function reportJsError(message, source) {
    try {
      var api = window.pywebview && window.pywebview.api;
      if (!api || typeof api.log_js_error !== "function") return;
      var p = api.log_js_error(String(message || ""), String(source || ""));
      if (p && typeof p.catch === "function") p.catch(function () {});
    } catch (e) {
      /* 记日志本身失败，不能再抛——否则就是「报告错误的代码自己出错」 */
    }
  }

  window.addEventListener("error", function (ev) {
    reportJsError(ev.message || "未知错误",
                  (ev.filename || "") + ":" + (ev.lineno || 0));
  });

  window.addEventListener("unhandledrejection", function (ev) {
    var r = ev.reason;
    reportJsError((r && (r.message || r)) || "未处理的 rejection", "promise");
  });

  /* ---- 异步回调（Python 侧主动推结果回来） --------------------------------
     `wbn/bridge.py` 的 `_push()` 调 `window.__wbnCb({id, ok, data})`。
     这个全局只能是**壳**提供的：它是传输层，不是某一页的业务。
     页面用 `ctx.nextCb(handler)` 领一个 id 传给 bridge，结果回来时
     handler(ok, data) 被调用一次，注册项随即删掉（不会泄漏）。 */

  var cbSeq = 1;
  var cbMap = {};

  function nextCb(handler) {
    var id = cbSeq++;
    cbMap[id] = handler;
    return id;
  }

  function dropCb(id) {
    delete cbMap[id];
  }

  window.__wbnCb = function (payload) {
    if (!payload || !payload.id) return;
    var h = cbMap[payload.id];
    if (!h) return;                 /* 已经超时放弃了，结果没人要 */
    delete cbMap[payload.id];
    try {
      h(!!payload.ok, payload.data || {});
    } catch (e) {
      /* 页面回调自己炸了不许影响下一次推送 */
    }
  };

  /* ---- 主题 -------------------------------------------------------------- */

  var mql = null;
  try { mql = window.matchMedia("(prefers-color-scheme: dark)"); } catch (e) { mql = null; }

  function storedTheme() {
    try { return localStorage.getItem(THEME_KEY); } catch (e) { return null; }
  }

  function applyTheme(mode, persist) {
    if (mode !== "light" && mode !== "dark") mode = "system";
    state.theme = mode;

    var dark = mode === "dark" || (mode === "system" && !!(mql && mql.matches));
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.documentElement.dataset.themeMode = mode;

    $$(".seg-item").forEach(function (b) {
      var on = b.dataset.mode === mode;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });

    if (!persist) return;

    /* localStorage 是快路径（theme-boot 首屏读它，避免 FOUC），
       config.json 的 ui.theme 是持久化正本（D4：和 tkinter 版同一字段）。 */
    try { localStorage.setItem(THEME_KEY, mode); } catch (e2) { /* file:// 下可能禁写 */ }
    call("config_save", { ui: { theme: mode } }).catch(function () {
      /* 存不进磁盘不影响本次会话显示，不打扰用户 */
    });
  }

  function wireTheme() {
    $$(".seg-item").forEach(function (b) {
      b.addEventListener("click", function () { applyTheme(b.dataset.mode, true); });
    });

    if (!mql) return;
    var onChange = function () {
      if (state.theme === "system") applyTheme("system", false);
    };
    if (mql.addEventListener) mql.addEventListener("change", onChange);
    else if (mql.addListener) mql.addListener(onChange);
  }

  /* ---- 顶栏：总开关 ------------------------------------------------------ */

  function paintSwitch() {
    var sw = $("#master-switch");
    if (!sw) return;
    sw.setAttribute("aria-checked", state.enabled ? "true" : "false");
  }

  function showMsg(text, level) {
    var n = $("#topbar-msg");
    if (!n) return;
    n.textContent = text;
    n.dataset.level = level === "ok" ? "ok" : "danger";
    n.hidden = false;
    clearTimeout(state.msgTimer);
    state.msgTimer = setTimeout(function () { n.hidden = true; }, 6000);
  }

  function wireSwitch() {
    var sw = $("#master-switch");
    if (!sw) return;

    sw.addEventListener("click", function () {
      var next = !state.enabled;
      sw.disabled = true;
      call("config_save", { enabled: next }).then(function () {
        state.enabled = next;
        paintSwitch();
        refreshChain();
        /* 刷当前页，不是写死状态页：触发页顶上的「总开关关着，这些规则
           不会生效」提示条要跟着变。写死 "status" 的话，用户在这一页拨开关，
           提示条要等切走再切回来才出现——界面在替旧状态背书。 */
        refreshPage(state.route);
      }).catch(function (e) {
        showMsg("没保存成功：" + e.message);
      }).then(function () {
        sw.disabled = false;
      });
    });
  }

  /* ---- 侧栏：链路陈述 ---------------------------------------------------- */

  function refreshChain() {
    var box = $("#chain");
    var text = $("#chain-text");
    if (!box || !text) return Promise.resolve();

    return call("chain_sentence").then(function (d) {
      box.dataset.level = d.level || "muted";
      text.textContent = d.text;
    }).catch(function (e) {
      box.dataset.level = "muted";
      text.textContent = "读不到配置：" + e.message;
    });
  }

  /* ---- 路由 -------------------------------------------------------------- */

  function routeFromHash() {
    var h = String(window.location.hash || "").replace(/^#/, "");
    return ROUTES.indexOf(h) >= 0 ? h : DEFAULT_ROUTE;
  }

  function go(name) {
    if (ROUTES.indexOf(name) < 0) name = DEFAULT_ROUTE;
    if (routeFromHash() === name) {
      render(name);                 /* hash 没变就不会有 hashchange */
    } else {
      window.location.hash = "#" + name;
    }
  }

  function render(name) {
    if (ROUTES.indexOf(name) < 0) name = DEFAULT_ROUTE;
    var changed = state.route !== name;
    state.route = name;

    $$(".nav-item").forEach(function (b) {
      var on = b.dataset.route === name;
      b.classList.toggle("is-active", on);
      if (on) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });

    $$("[data-page]").forEach(function (sec) {
      var on = sec.dataset.page === name;
      sec.hidden = !on;
      if (on && changed) {
        sec.classList.remove("page-enter");
        void sec.offsetWidth;       /* 强制回流，动画才能重放 */
        sec.classList.add("page-enter");
      }
    });

    var item = navById(name);
    var title = item ? item.label : name;
    var h1 = $("#page-title");
    if (h1) h1.textContent = title;
    document.title = title + " · AI 任务通知";

    /* 给 CSS 一个「现在在哪一页」的钩子：通道页是双栏，要突破 .col 的
       720px 限宽，其他页维持阅读宽度。 */
    document.body.dataset.route = name;

    /* 页面生命周期（W3.5 / F 项）。切走时让上一页收拾自己的烂摊子：
       在飞的回调、还没到点的定时器。没有这一步，异步结果会写进已经
       detach 的 DOM——不崩不报错，就是漏 + 幽灵更新。
       页面可以不实现 destroy()，缺了就跳过，不是必须项。 */
    var page = (window.WBN_PAGES || {})[name] || null;
    var prev = state.currentPage;
    if (changed && prev && prev !== page && typeof prev.destroy === "function") {
      try {
        prev.destroy();
      } catch (e) {
        /* 清理失败不许挡住切换：用户点的是导航，不是「让页面收拾干净」 */
      }
    }
    state.currentPage = page;

    mount(name);
  }

  function mount(name) {
    var sec = $('[data-page="' + name + '"]');
    if (!sec) return;

    var page = (window.WBN_PAGES || {})[name];

    if (!state.mounted[name]) {
      state.mounted[name] = true;
      if (page && typeof page.mount === "function") {
        try {
          page.mount(sec, ctx);
        } catch (e) {
          showPageError(sec, e);
        }
      } else {
        paintPlaceholder(sec, name);
      }
      return;
    }

    if (page && typeof page.show === "function") {
      try { page.show(); } catch (e2) { /* 页面自己会把错误画出来 */ }
    }
  }

  function refreshPage(name) {
    var page = (window.WBN_PAGES || {})[name];
    if (page && typeof page.show === "function") {
      try { page.show(); } catch (e) { /* 同上 */ }
    }
  }

  /* ---- 占位页 / 页面级错误 ------------------------------------------------ */

  function paintPlaceholder(sec, name) {
    sec.textContent = "";
    var item = navById(name);
    sec.appendChild(el("p", "page-sub", "这一页没加载出来。"));

    var box = el("div", "empty block");
    box.appendChild(icon("M4 5a2 2 0 012-2h13v18H6a2 2 0 01-2-2zM8 7h7M8 11h7", "empty-icon"));
    box.appendChild(el("p", "empty-title", (item ? item.label : name) + "加载失败"));
    box.appendChild(el("p", "empty-hint", PLACEHOLDER[name] || ""));
    sec.appendChild(box);
  }

  function showPageError(sec, err) {
    sec.textContent = "";
    sec.appendChild(el("p", "page-sub", "这一页加载失败。"));

    var box = el("div", "error-box block");
    box.appendChild(el("p", "error-title", "页面没渲染出来"));
    box.appendChild(el("p", "error-text", String((err && err.message) || err)));

    var btn = el("button", "btn btn-secondary", "重试");
    btn.type = "button";
    btn.addEventListener("click", function () {
      state.mounted[state.route] = false;
      sec.textContent = "";
      mount(state.route);
    });
    box.appendChild(btn);
    sec.appendChild(box);
  }

  /* ---- 给页面模块的上下文 ------------------------------------------------ */

  var ctx = {
    call: call,
    el: el,
    icon: icon,
    $: $,
    $$: $$,
    go: go,
    refreshChain: refreshChain,
    nextCb: nextCb,
    dropCb: dropCb,
    /* 顶栏那条临时提示。页面里要报「保存成功 / 还差什么」时用这个，
       不要自己 alert()（§3.4 明确禁止） */
    notify: showMsg
  };

  /* ---- 启动 -------------------------------------------------------------- */

  function buildNav() {
    var nav = $("#nav");
    if (!nav) return;
    nav.textContent = "";

    NAV.forEach(function (item) {
      var b = el("button", "nav-item");
      b.type = "button";
      b.dataset.route = item.id;
      b.appendChild(icon(item.icon, "nav-icon"));
      b.appendChild(el("span", "nav-label", item.label));
      b.addEventListener("click", function () { go(item.id); });
      nav.appendChild(b);
    });
  }

  function boot() {
    buildNav();
    wireTheme();
    wireSwitch();

    /* theme-boot.js 已经在首屏把 data-themeMode 定好了，这里只是把分段控件
       的选中态画出来，不重新决策一次，避免和首屏那帧不一致。 */
    applyTheme(document.documentElement.dataset.themeMode || "system", false);

    window.addEventListener("hashchange", function () {
      render(routeFromHash());
    });

    render(routeFromHash());

    call("config_get").then(function (d) {
      state.enabled = !!(d.cfg && d.cfg.enabled);
      paintSwitch();
      /* localStorage 里没有记录（首次启动，或 file:// 禁了它）才用磁盘那份 */
      if (!storedTheme() && d.theme) applyTheme(d.theme, false);
    }).catch(function (e) {
      showMsg("读配置失败：" + e.message);
    });

    refreshChain();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
