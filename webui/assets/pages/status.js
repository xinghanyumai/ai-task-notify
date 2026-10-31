/* 「运行状态」页 —— 全站样板页。
 *
 * 后面三页照抄这里的三段式：**骨架屏 → 数据 / 空态 → 错误态**。
 * 定稿标准只有一条：随便截一块 200×200 的区域，能看出是同一个应用。
 *
 * 规矩（W3 / W4 同样适用）：
 *   - 建节点只用 ctx.el()，文本只走 textContent，禁 innerHTML / insertAdjacentHTML
 *   - 每个 promise 都有 .catch，否则 Console 里会多一条报错
 *   - 颜色只从 token 取：这里只设 data-level / data-tone，具体色值在 shell.css
 */
(function () {
  "use strict";

  window.WBN_PAGES = window.WBN_PAGES || {};

  var ICON_INBOX = "M3 13h5l2 3h4l2-3h5M5 5h14l2 8v6H3v-6z";
  var ICON_SEND = "M4 12l16-8-6 16-3-6-7-2z";

  /* 四张卡的顺序是固定的：通知数 → 成功 → 失败 → 启用比例。
     前三张是「今天发生了什么」，最后一张是「现在有多少条路是通的」。 */
  var CARDS = [
    { key: "today",   label: "今日通知" },
    { key: "ok",      label: "通道成功" },
    { key: "fail",    label: "通道失败", danger: true },
    { key: "enabled", label: "启用通道", ratio: true }
  ];

  var state = {
    root: null,
    ctx: null,
    built: false,
    counted: false,   /* 数字滚动只在首次进入时做一次，切回来不重复 */
    loading: false,
    sandboxPref: false, /* 上次是否「关沙箱才起来的」，见 paintSandboxPref */
    nodes: {}
  };

  /* ---- 建骨架 ------------------------------------------------------------ */

  function build() {
    var ctx = state.ctx;
    var root = state.root;
    root.textContent = "";

    state.nodes.sub = ctx.el("p", "page-sub", "正在读取…");
    root.appendChild(state.nodes.sub);

    var cards = ctx.el("div", "cards");
    state.nodes.cardBoxes = [];
    CARDS.forEach(function (c) {
      var box = ctx.el("div", "stat");
      state.nodes.cardBoxes.push(box);
      cards.appendChild(box);
    });
    root.appendChild(cards);

    state.nodes.chList = block(ctx, root, "通道状态");
    state.nodes.asBox = block(ctx, root, "启动与自启");
    state.nodes.rcList = block(ctx, root, "最近通知");

    state.built = true;
  }

  /** 一个「小标题 + 容器」的块，返回容器。 */
  function block(ctx, root, title) {
    var sec = ctx.el("section", "block");
    sec.appendChild(ctx.el("h2", "block-title", title));
    var holder = ctx.el("div");
    sec.appendChild(holder);
    root.appendChild(sec);
    return holder;
  }

  /* ---- 加载 -------------------------------------------------------------- */

  function load() {
    if (state.loading) return Promise.resolve();
    state.loading = true;
    skeleton();

    return Promise.all([
      state.ctx.call("stats_today"),
      state.ctx.call("channels_list"),
      state.ctx.call("autostart_get"),
      /* 沙箱偏好是可选的：老后端没有这个方法，失败就当「没记过」，
         不许因此让整页报错（status 页的其它三块跟它无关）。 */
      state.ctx.call("sandbox_pref_get").catch(function () {
        return { prefer_no_sandbox: false };
      })
    ]).then(function (res) {
      paint(res[0], res[1], res[2]);
      paintSandboxPref(res[3]);
    }).catch(function (e) {
      showError(e);
    }).then(function () {
      state.loading = false;
    });
  }

  function skeleton() {
    var ctx = state.ctx;
    state.nodes.sub.textContent = "正在读取…";

    state.nodes.cardBoxes.forEach(function (box) {
      box.textContent = "";
      box.appendChild(ctx.el("div", "sk sk-label"));
      box.appendChild(ctx.el("div", "sk sk-value"));
    });

    state.nodes.chList.textContent = "";
    state.nodes.chList.appendChild(skeletonList(3));
    state.nodes.asBox.textContent = "";
    state.nodes.asBox.appendChild(skeletonList(1));
    state.nodes.rcList.textContent = "";
    state.nodes.rcList.appendChild(skeletonList(3));
  }

  function skeletonList(n) {
    var ctx = state.ctx;
    var list = ctx.el("div", "list");
    for (var i = 0; i < n; i++) {
      var row = ctx.el("div", "row");
      row.appendChild(ctx.el("div", "sk sk-row"));
      list.appendChild(row);
    }
    return list;
  }

  /* ---- 上色 -------------------------------------------------------------- */

  function paint(d, channels, autostart) {
    var ctx = state.ctx;
    var animate = !state.counted;

    state.nodes.sub.textContent = subtitle(d);

    CARDS.forEach(function (c, i) {
      var box = state.nodes.cardBoxes[i];
      box.textContent = "";
      box.appendChild(ctx.el("div", "stat-label", c.label));

      var v = ctx.el("div", "stat-value");
      box.appendChild(v);

      if (c.ratio) {
        v.textContent = d.enabled_channels + "/" + d.total_channels;
        return;
      }
      if (c.danger && d.fail > 0) v.dataset.tone = "danger";
      paintValue(v, d[c.key], animate);
    });

    state.counted = true;

    paintChannels(channels);
    paintAutostart(autostart);
    paintRecent(d.recent);
  }

  /** 数字从 0 滚到目标值，300ms ease-out。只做一次，之后直接落字。 */
  function paintValue(node, target, animate) {
    target = Number(target) || 0;

    if (!animate || !target || prefersReduced()) {
      node.textContent = String(target);
      return;
    }

    var dur = 300;
    var t0 = 0;

    function tick(now) {
      if (!node.isConnected) return;      /* 期间被重建了，别再写 */
      if (!t0) t0 = now;
      var p = Math.min(1, (now - t0) / dur);
      var eased = 1 - Math.pow(1 - p, 3);
      if (p < 1) {
        node.textContent = String(Math.round(target * eased));
        window.requestAnimationFrame(tick);
      } else {
        node.textContent = String(target);
      }
    }

    window.requestAnimationFrame(tick);
  }

  function prefersReduced() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (e) {
      return false;
    }
  }

  function subtitle(d) {
    if (!d.today) return "今天还没有发出通知。";
    if (!d.fail) {
      return "今天发出 " + d.today + " 条通知，通道投递 " + d.ok + " 次全部成功。";
    }
    return "今天发出 " + d.today + " 条通知，通道投递 " + d.ok + " 次成功、" +
           d.fail + " 次失败。";
  }

  function paintChannels(list) {
    var ctx = state.ctx;
    state.nodes.chList.textContent = "";

    if (!list || !list.length) {
      state.nodes.chList.appendChild(emptyBox({
        title: "还没配置任何通道。",
        hint: "通知得有出口才发得出去。去通道页填一个，填齐凭证就会自动启用。",
        action: "去配置通道",
        iconPath: ICON_SEND,
        onAction: function () { ctx.go("channels"); }
      }));
      return;
    }

    var box = ctx.el("div", "list");
    list.forEach(function (c) {
      var row = ctx.el("div", "row");
      row.appendChild(ctx.el("span", "row-name", c.label || c.type));

      var b = ctx.el("span", "badge", c.state);
      b.dataset.level = c.level || "muted";
      row.appendChild(b);

      row.appendChild(ctx.el("span", "row-meta", c.last || "未发送"));
      box.appendChild(row);
    });
    state.nodes.chList.appendChild(box);
  }

  function paintRecent(rows) {
    var ctx = state.ctx;
    state.nodes.rcList.textContent = "";

    if (!rows || !rows.length) {
      state.nodes.rcList.appendChild(emptyBox({
        /* 「还没收到通知」是假话：它曾经读 `notifications.log`，而那个文件
           只有「写入本地日志」通道在写，关掉之后这里永远为空，可用户明明
           已经发了 20 条。现在数的是事件汇总行，空就是真的今天还没发过。 */
        title: "今天还没有通知记录。",
        hint: "先确认通道配好了、事件勾上了，再发一条测试消息，看看链路通不通。",
        action: "去配置通道",
        iconPath: ICON_INBOX,
        onAction: function () { ctx.go("channels"); }
      }));
      return;
    }

    var box = ctx.el("div", "list");
    rows.forEach(function (r) {
      var row = ctx.el("div", "row");
      row.appendChild(ctx.el("span", "row-time", hhmm(r)));

      var dot = ctx.el("span", "res-dot");
      dot.dataset.ok = r.ok ? "1" : "0";
      row.appendChild(dot);

      row.appendChild(ctx.el("span", "row-text", r.summary || "（无标题）"));
      box.appendChild(row);
    });
    state.nodes.rcList.appendChild(box);
  }

  /* ---- 启动与自启 --------------------------------------------------------
   *
   * **控件必须是原生 `<input type="checkbox">` + `<label for>`。**
   * 通道页那个 `.switch` 是自绘的（`<button>` 上挂个 role 属性，不是原生控件），本页不用：
   * 原生控件白拿键盘 Tab / 空格切换、读屏播报、系统高对比度三件事，
   * 自绘一样都拿不到（本项目 46 条对比度 / 焦点守卫也管不到它）。
   *
   * 状态是**三态**：开 / 关 / 读不到启动目录。第三种绝不能画成「已关闭」——
   * 那是在替系统撒谎，用户会以为自己关过它，然后去别处找开关。
   */

  var AS_ID = "autostart-toggle";

  function autostartText(known, on) {
    if (!known) return "无法读取启动目录";
    return on ? "已开启（开机后托盘常驻）" : "已关闭";
  }

  function paintAutostart(o) {
    var ctx = state.ctx;
    var box = state.nodes.asBox;
    box.textContent = "";

    o = o || {};
    var known = o.ok === true;
    var on = o.enabled === true;
    var path = o.path || "";

    var row = ctx.el("div", "row");

    var cell = ctx.el("div", "text");
    var lab = ctx.el("label", "title", "开机自启");
    lab.setAttribute("for", AS_ID);
    cell.appendChild(lab);

    /* 副文案是启动文件夹里的真实路径。长路径用 title 兜全文、单行省略——
       折行会把行高撑开，整块看着像两行内容。 */
    var pathLine = ctx.el("div", "desc path-line", path || "（读不到路径）");
    if (path) pathLine.title = path;
    cell.appendChild(pathLine);
    row.appendChild(cell);

    var st = ctx.el("span", "row-meta", autostartText(known, on));
    st.id = "autostart-state";
    row.appendChild(st);

    var cb = ctx.el("input", "toggle-box");
    cb.type = "checkbox";
    cb.id = AS_ID;
    cb.checked = on;
    cb.disabled = !known;            /* 读不到就别让人点，点了也没法落地 */
    cb.setAttribute("aria-describedby", "autostart-state");
    cb.addEventListener("change", function () {
      applyAutostart(cb.checked);
    });
    row.appendChild(cb);

    box.appendChild(row);
    renderSandboxPref();
  }

  /** 「上次是关沙箱才起来的」那一行。**只在真的记着的时候出现**——
   *
   * 正常机器上永远看不到它。一旦出现，说明这台机器的 WebView2 带沙箱起不来，
   * 而程序已经替用户做了降级决定，就必须给他一个**撤销**的入口，
   * 否则那个记忆是他永远不知道、也永远改不掉的东西。
   */
  function paintSandboxPref(o) {
    state.sandboxPref = !!(o && o.prefer_no_sandbox === true);
    renderSandboxPref();
  }

  function renderSandboxPref() {
    var ctx = state.ctx;
    var box = state.nodes.asBox;
    if (!box) return;
    var old = box.querySelector('[data-role="sandbox-pref"]');
    if (old) old.remove();
    if (!state.sandboxPref) return;

    var row = ctx.el("div", "row");
    row.setAttribute("data-role", "sandbox-pref");

    var cell = ctx.el("div", "text");
    cell.appendChild(ctx.el("div", "title", "界面渲染方式"));
    cell.appendChild(ctx.el("div", "desc",
      "上次是关掉 Chromium 沙箱才把窗口画出来的，已记住，之后启动不再等 10 秒。"));
    row.appendChild(cell);

    var btn = ctx.el("button", "btn btn-secondary", "恢复默认（带沙箱）");
    btn.type = "button";
    btn.addEventListener("click", resetSandboxPref);
    row.appendChild(btn);

    box.appendChild(row);
  }

  function resetSandboxPref() {
    state.ctx.call("sandbox_pref_reset").then(function (o) {
      paintSandboxPref(o);
    }).catch(function (e) {
      showAutostartError(String((e && e.message) || e));
    });
  }

  /** 点一下 → 落盘 → **用后端重读到的状态覆盖控件**（不是用点击后的值）。
   *
   * 用户点了「开」但建快捷方式失败时，界面必须显示关着。把入参回显成
   * `checked` 是最容易写、也最容易撒谎的一种实现。
   *
   * **写入过程中用 `WBN_UI.setBusyAttr`，不用 `disabled`**：给一个**已聚焦**
   * 的元素设 `disabled` 会把它 blur 掉，焦点掉回 `body`——键盘用户按空格
   * 切完这个开关，下一个 Tab 就从页面开头重走（`tests/test_no_focus_killing_disabled.py`
   * 钉的就是这一类）。aria-disabled 只是「看着不可用」，焦点还在原地，
   * 真正的拦截靠下面那个 `asBusy` 卫兵。
   */
  var asBusy = false;

  function applyAutostart(next) {
    var ctx = state.ctx;
    if (asBusy) return;              /* 上一次还没回来，别连点 */

    var before = document.getElementById(AS_ID);
    /* 重画会把这一行整个换掉，旧节点上的焦点随之消失、掉回 body。
       键盘用户按空格切完开关，下一个 Tab 就从页面开头重走——和设 disabled
       丢焦点是同一个后果。所以记下「刚才焦点在不在开关上」，画完再放回去。 */
    var hadFocus = !!before && document.activeElement === before;

    asBusy = true;
    WBN_UI.setBusyAttr(before, true);

    function settle() {
      asBusy = false;
      var now = document.getElementById(AS_ID);
      WBN_UI.setBusyAttr(now, false);
      if (hadFocus && now) now.focus();
    }

    ctx.call("autostart_set", next).then(function (o) {
      paintAutostart(o);             /* 重画之后 cb 是新的，settle 兜一下 */
      settle();
      if (!o || o.ok !== true) {
        showAutostartError((o && o.msg) || "操作失败，状态未改变");
      }
    }).catch(function (e) {
      settle();
      /* 连 set 都没走通（bridge 挂了）：回去问一次真实状态，别把控件留在
         一个谁也不确定的位置上。 */
      ctx.call("autostart_get").then(paintAutostart).catch(function () {
        paintAutostart({ ok: false, enabled: false, path: "" });
      });
      showAutostartError(String((e && e.message) || e));
    });
  }

  function showAutostartError(msg) {
    if (!msg) return;
    state.nodes.asBox.appendChild(state.ctx.el("p", "row-error", msg));
  }

  /** 空状态三件套：一个图标 + 一句解释 + 一个出口（§3.3）。 */
  function emptyBox(o) {
    var ctx = state.ctx;
    var box = ctx.el("div", "empty");
    box.appendChild(ctx.icon(o.iconPath, "empty-icon"));
    box.appendChild(ctx.el("p", "empty-title", o.title));
    box.appendChild(ctx.el("p", "empty-hint", o.hint));

    var btn = ctx.el("button", "btn btn-secondary", o.action);
    btn.type = "button";
    btn.addEventListener("click", o.onAction);
    box.appendChild(btn);

    return box;
  }

  /** 「最近通知」的时间列。**跨天要标出来**（W3.5 / C 项）：
   *
   * 原来只取 `HH:MM`，昨天 23:50 和今天 00:10 在界面上看起来是同一类东西，
   * 用户会把昨晚的通知当成刚才的。后端给了 `day` 字段（`today` / `yesterday`），
   * 这里照着说人话。
   */
  function hhmm(r) {
    var s = String((r && r.time) || "");
    var t = s.length >= 5 ? s.slice(0, 5) : s;
    return r && r.day === "yesterday" ? "昨天 " + t : t;
  }

  /* ---- 错误态 ------------------------------------------------------------
     不许 alert()、不许白屏。整页换一条说明 + 一个「重试」。 */

  function showError(err) {
    var ctx = state.ctx;
    state.root.textContent = "";
    state.root.appendChild(ctx.el("p", "page-sub", "读不到运行状态。"));

    var box = ctx.el("div", "error-box block");
    box.appendChild(ctx.el("p", "error-title", "读配置失败"));
    box.appendChild(ctx.el("p", "error-text", String((err && err.message) || err)));

    var actions = ctx.el("div", "row-actions");
    var retry = ctx.el("button", "btn btn-primary", "重试");
    retry.type = "button";
    retry.addEventListener("click", function () {
      build();
      load();
    });
    actions.appendChild(retry);
    box.appendChild(actions);

    state.root.appendChild(box);
    state.built = false;
  }

  /* ---- 对外接口 ---------------------------------------------------------- */

  window.WBN_PAGES.status = {
    mount: function (root, ctx) {
      state.root = root;
      state.ctx = ctx;
      build();
      load();
    },
    show: function () {
      if (!state.built) build();
      load();
    }
  };
})();
