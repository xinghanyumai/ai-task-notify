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
      state.ctx.call("channels_list")
    ]).then(function (res) {
      paint(res[0], res[1]);
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

  function paint(d, channels) {
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
