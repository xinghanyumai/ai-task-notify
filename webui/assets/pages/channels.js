/* 「推送通道」页（W3）——10 个通道、schema 驱动表单、异步测试。
 *
 * 三条硬约束（这一页是全书最容易被塞进 innerHTML 的地方）：
 *   1. **表单从 channels_meta 生成**，不写 10 套。加通道只改 CHANNEL_META 一处。
 *   2. **通道报错原文是不可信文本**（远端服务返回的 body），只许 textContent。
 *      这里是「某个通道返回恶意 body → 读出全部通道密钥」那条路的起点。
 *   3. 建节点只用 ctx.el()，文本只走 textContent，禁 innerHTML / insertAdjacentHTML
 *
 * 与任务书 §3.2 示例代码的差异：示例用 `innerHTML` + `esc()` 拼模板，
 * 这里改成 DOM API。理由：项目硬约束是「禁 innerHTML」，而且 esc() 少写一次
 * 就是一条 XSS——DOM API 从根上没有这个问题，不需要靠人记得转义。
 */
(function () {
  "use strict";

  window.WBN_PAGES = window.WBN_PAGES || {};

  /* CHANNEL_META 里的 icon 是短名，这里映射到路径。缺的回落成一个圆点，
     不报错——Python 侧以后加通道不用等前端先加图标。 */
  var ICONS = {
    chat: "M4 5h16v11H9l-5 4z",
    briefcase: "M4 8h16v11H4zM9 8V6h6v2M4 13h16",
    phone: "M8 3h8v18H8zM11 18h2",
    bolt: "M13 3L4 14h7l-1 7 9-11h-7l1-7z",
    mail: "M3 6h18v12H3zM3 7l9 6 9-6",
    link: "M10 13a4 4 0 006 .5l2-2a4 4 0 10-6-6l-1 1M14 11a4 4 0 00-6-.5l-2 2a4 4 0 106 6l1-1",
    file: "M6 3h8l4 4v14H6zM14 3v4h4M9 13h6M9 17h6",
    dot: "M12 9a3 3 0 100 6 3 3 0 000-6"
  };

  var ICON_EYE = "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 100 6 3 3 0 000-6";
  var ICON_EYE_OFF = "M3 3l18 18M10.6 10.6a3 3 0 004.2 4.2" +
                     "M9.4 5.3A10.6 10.6 0 0112 5c6 0 10 7 10 7a17 17 0 01-3.2 4" +
                     "M6.2 6.2A17 17 0 002 12s4 7 10 7a10.5 10.5 0 004-.8";
  var ICON_CHECK = "M4 12l5 5L20 6";
  var ICON_ALERT = "M12 3l9 17H3zM12 10v5M12 18v.5";

  /* ---- 前端兜底超时 ------------------------------------------------------
     Python 线程真挂了，不许让按钮永远转圈（W3 §3.4）。但**必须比后端长**：
     比后端短的话，后端还在跑、前端已经报「等待超时」，等真实结果回来时
     cbMap 里已经没有这个 id，被 `_push()` 静默丢弃——用户看到的失败是假的
     （W3.1 §2.1 那个反向说谎的 bug）。

     后端一次发送最坏要串两次请求：先换 access_token（`TOKEN_TIMEOUT`），
     再发消息（`advanced.http_timeout`）。两个值都由 `channels_meta().timeouts`
     下发，这里只做加法和下限，**不写死、不读 config**。

     下限 20s 而不是算式的 15s：http_timeout 被调小到 3 时，两段抖动叠起来
     仍可能超 15s，兜底不能比默认配置还激进。 */
  var TEST_TIMEOUT_FLOOR = 20000;
  var TEST_TIMEOUT_MARGIN = 2000;
  var testTimeout = TEST_TIMEOUT_FLOOR;   /* meta 到货前的保守值 */

  function deriveTestTimeout(meta) {
    var t = (meta && meta.timeouts) || null;
    if (!t) return TEST_TIMEOUT_FLOOR;    /* 后端没下发：退回下限，不猜 */
    var ms = ((Number(t.token) || 0) + (Number(t.send) || 0)) * 1000
             + TEST_TIMEOUT_MARGIN;
    return Math.max(TEST_TIMEOUT_FLOOR, ms);
  }

  var state = {
    root: null,
    ctx: null,
    built: false,
    loading: false,

    meta: null,        /* channels_meta 的 data：groups / meta / required */
    list: [],          /* channels_state 的 data：每个通道的状态 + 磁盘上的值 */
    labels: {},        /* {ctype: {key: 中文名}}，用于「还缺什么」 */
    draft: {},         /* {ctype: {key: value}} 用户正在编辑的（未保存） */
    base: {},          /* {ctype: {key: value}} 上次从磁盘读到的，用来判脏 */
    current: null,

    busy: false,
    busyWhat: "",      /* "save" | "test" | "openid" */
    result: null,      /* {ctype, tone, text} 上一次测试结果，切页回来还在 */
    timer: 0,          /* 异步兜底 / 成功提示淡出，同一时刻只有一个异步动作 */
    nodes: {}
  };

  /* ---- 小工具 ------------------------------------------------------------ */

  function copy(o) {
    var out = {};
    Object.keys(o || {}).forEach(function (k) { out[k] = o[k]; });
    return out;
  }

  function truthy(v) {
    return v === true || v === 1 || v === "1" || v === "true";
  }

  function stateOf(ctype) {
    for (var i = 0; i < state.list.length; i++) {
      if (state.list[i].type === ctype) return state.list[i];
    }
    return null;
  }

  function metaOf(ctype) {
    return (state.meta && state.meta.meta && state.meta.meta[ctype]) || null;
  }

  function fieldsOf(ctype) {
    var m = metaOf(ctype);
    return (m && m.fields) || [];
  }

  function requiredOf(ctype) {
    return (state.meta && state.meta.required && state.meta.required[ctype]) || [];
  }

  function hasField(ctype, key) {
    return fieldsOf(ctype).some(function (f) { return f.key === key; });
  }

  /** 必填项还缺什么，返回**中文名**（和 tkinter 版 `missing_fields` 一个口径）。
   *
   * 为什么不直接用 bridge 给的 `missing`：那是**磁盘上**的值算的，用户在输入框
   * 里刚敲进去的字它看不见，按钮就不会亮。所以这里必须按 draft 现算。
   * 重复的只有「空串算缺」这一句判定；「哪些字段必填」仍然只有
   * `channels.REQUIRED` 一份来源。 */
  function missingInDraft(ctype) {
    var v = state.draft[ctype] || {};
    var labels = state.labels[ctype] || {};
    return requiredOf(ctype)
      .filter(function (k) { return !String(v[k] == null ? "" : v[k]).trim(); })
      .map(function (k) { return labels[k] || k; });
  }

  function isDirty(ctype) {
    if (!ctype) return false;
    var a = state.draft[ctype] || {};
    var b = state.base[ctype] || {};
    var keys = Object.keys(a);
    for (var i = 0; i < keys.length; i++) {
      if (String(a[keys[i]]) !== String(b[keys[i]])) return true;
    }
    return false;
  }

  function firstType() {
    var groups = (state.meta && state.meta.groups) || [];
    for (var i = 0; i < groups.length; i++) {
      if (groups[i].types && groups[i].types.length) return groups[i].types[0];
    }
    return null;
  }

  function labelMap(metaData) {
    var out = {};
    Object.keys(metaData.meta || {}).forEach(function (ctype) {
      var m = {};
      (metaData.meta[ctype].fields || []).forEach(function (f) { m[f.key] = f.label; });
      out[ctype] = m;
    });
    return out;
  }

  /* ---- 骨架 -------------------------------------------------------------- */

  function build() {
    var ctx = state.ctx;
    var root = state.root;
    root.textContent = "";

    var wrap = ctx.el("div", "chan");

    state.nodes.list = ctx.el("div", "chan-list");
    state.nodes.pane = ctx.el("div", "chan-pane");

    wrap.appendChild(state.nodes.list);
    wrap.appendChild(state.nodes.pane);
    root.appendChild(wrap);

    state.built = true;
  }

  function skeleton() {
    var ctx = state.ctx;

    state.nodes.list.textContent = "";
    var head = ctx.el("div", "chan-list-head");
    head.appendChild(ctx.el("span", "chan-list-title", "全部通道"));
    head.appendChild(ctx.el("div", "sk sk-label"));
    state.nodes.list.appendChild(head);

    for (var i = 0; i < 6; i++) {
      var row = ctx.el("div", "chan-row");
      row.appendChild(ctx.el("div", "sk sk-row"));
      state.nodes.list.appendChild(row);
    }

    state.nodes.pane.textContent = "";
    var body = ctx.el("div", "chan-body");
    body.appendChild(ctx.el("div", "sk sk-value"));
    body.appendChild(ctx.el("div", "sk sk-row"));
    body.appendChild(ctx.el("div", "sk sk-row"));
    state.nodes.pane.appendChild(body);
  }

  /* ---- 加载 -------------------------------------------------------------- */

  function load() {
    if (state.loading) return Promise.resolve();
    state.loading = true;
    skeleton();

    return Promise.all([
      state.ctx.call("channels_meta"),
      state.ctx.call("channels_state")
    ]).then(function (res) {
      state.meta = res[0];
      testTimeout = deriveTestTimeout(res[0]);
      state.labels = labelMap(res[0]);
      adopt(res[1], null);

      if (!state.current || !metaOf(state.current)) state.current = firstType();

      paintList();
      paintDetail();
    }).catch(function (e) {
      showError(e);
    }).then(function () {
      state.loading = false;
    });
  }

  /** 把磁盘状态吃进本地。`keep` 指定哪个通道的 draft 要保留（用户正在改）。 */
  function adopt(list, keep) {
    state.list = list || [];
    state.list.forEach(function (c) {
      state.base[c.type] = copy(c.values);
      if (c.type !== keep) state.draft[c.type] = copy(c.values);
    });
  }

  /* ---- 左列表 ------------------------------------------------------------ */

  function paintList() {
    var ctx = state.ctx;
    var box = state.nodes.list;
    box.textContent = "";

    var on = state.list.filter(function (c) { return c.enabled; }).length;

    var head = ctx.el("div", "chan-list-head");
    head.appendChild(ctx.el("span", "chan-list-title", "全部通道"));
    head.appendChild(ctx.el("span", "chan-list-count", on + "/" + state.list.length));
    box.appendChild(head);

    ((state.meta && state.meta.groups) || []).forEach(function (g) {
      if (!g.types || !g.types.length) return;
      box.appendChild(ctx.el("div", "chan-group", g.label));
      g.types.forEach(function (ctype) {
        if (metaOf(ctype)) box.appendChild(rowNode(ctype));
      });
    });
  }

  function rowNode(ctype) {
    var ctx = state.ctx;
    var m = metaOf(ctype);
    var st = stateOf(ctype);

    var b = ctx.el("button", "chan-row");
    b.type = "button";
    b.dataset.type = ctype;
    if (ctype === state.current) {
      b.classList.add("is-active");
      b.setAttribute("aria-current", "true");
    }

    b.appendChild(ctx.icon(ICONS[m.icon] || ICONS.dot, "chan-icon"));

    var main = ctx.el("span", "chan-row-main");
    main.appendChild(ctx.el("span", "chan-row-name", m.short));
    /* 副标题直接用 insight.channel_state 的结论，前端不重新判断——
       判定逻辑只许有一份，否则两版界面会对同一份配置说两句话。 */
    main.appendChild(ctx.el("span", "chan-row-sub", (st && st.subtitle) || ""));
    b.appendChild(main);

    var dot = ctx.el("span", "chan-dot");
    dot.dataset.level = (st && st.level) || "muted";
    b.appendChild(dot);

    b.addEventListener("click", function () { select(ctype); });
    return b;
  }

  function select(ctype) {
    if (ctype === state.current) return;
    state.current = ctype;
    clearResult();
    paintList();
    paintDetail();
  }

  /* ---- 右表单 ------------------------------------------------------------ */

  function paintDetail() {
    var ctx = state.ctx;
    var pane = state.nodes.pane;
    var ctype = state.current;
    pane.textContent = "";

    if (!ctype) {
      pane.appendChild(ctx.el("p", "chan-none", "配置里没有任何通道。"));
      return;
    }

    var m = metaOf(ctype);
    var st = stateOf(ctype) || {};

    /* ---- 头：名字 + 一句话说明 + 独立开关（文档写「打开开关」，必须有） ---- */
    var head = ctx.el("div", "chan-head");
    var headMain = ctx.el("div", "chan-head-main");
    headMain.appendChild(ctx.el("h2", "chan-title", m.label));
    if (m.summary) headMain.appendChild(ctx.el("p", "chan-summary", m.summary));
    head.appendChild(headMain);

    var sw = ctx.el("button", "switch");
    sw.type = "button";
    sw.setAttribute("role", "switch");
    sw.setAttribute("aria-label", "启用 " + m.short);
    sw.setAttribute("aria-checked", st.enabled ? "true" : "false");
    sw.addEventListener("click", function () {
      var cur = stateOf(ctype);
      toggle(ctype, !(cur && cur.enabled));
    });
    head.appendChild(sw);
    pane.appendChild(head);

    /* ---- 体：字段 / 动作 / 结果 / help ---- */
    var body = ctx.el("div", "chan-body");

    if (!fieldsOf(ctype).length) {
      body.appendChild(ctx.el("p", "chan-none",
        "这个通道不需要填任何东西，打开开关就会生效。"));
    } else {
      var fields = ctx.el("div", "fields");
      fieldsOf(ctype).forEach(function (f) { fields.appendChild(fieldNode(ctype, f)); });
      body.appendChild(fields);
    }

    /* 禁用原因放在按钮**上面**：放下面的话，表单长一点就滚出视野，
       用户看到的仍然是一个灰按钮（§3.3 要的就是「看得见为什么」）。 */
    var why = ctx.el("p", "chan-why");
    why.hidden = true;
    body.appendChild(why);

    var actions = ctx.el("div", "chan-actions");
    var save = ctx.el("button", "btn btn-primary", "保存配置");
    save.type = "button";
    /* 「测试此通道」五个字**不许改**：docs/channels/ 下 10 篇逐字引用它 */
    var test = ctx.el("button", "btn btn-secondary", "测试此通道");
    test.type = "button";
    var dirty = ctx.el("span", "chan-dirty");
    dirty.hidden = true;
    actions.appendChild(save);
    actions.appendChild(test);
    actions.appendChild(dirty);

    if (hasField(ctype, "openid")) {
      actions.appendChild(ctx.el("span", "spacer"));
      var oid = ctx.el("button", "btn btn-secondary", "自动获取 openid");
      oid.type = "button";
      oid.addEventListener("click", function () { fetchOpenid(ctype); });
      actions.appendChild(oid);
      state.nodes.openid = oid;
    } else {
      state.nodes.openid = null;
    }
    body.appendChild(actions);

    var result = ctx.el("div", "chan-result");
    result.hidden = true;
    body.appendChild(result);

    if (m.help) body.appendChild(ctx.el("div", "chan-help", m.help));

    pane.appendChild(body);

    state.nodes.save = save;
    state.nodes.test = test;
    state.nodes.dirty = dirty;
    state.nodes.why = why;
    state.nodes.result = result;
    state.nodes.switch = sw;
    paintResult();

    save.addEventListener("click", function () { saveChannel(ctype); });
    test.addEventListener("click", function () { testChannel(ctype); });

    paintTestButton();
    refreshGates(ctype);
  }

  function fieldNode(ctype, f) {
    var ctx = state.ctx;
    var v = state.draft[ctype] || {};
    var value = v[f.key];
    var id = "f-" + ctype + "-" + f.key;
    var req = requiredOf(ctype).indexOf(f.key) >= 0;

    var wrap = ctx.el("div", "field");
    if (f.type === "textarea") wrap.dataset.span = "2";

    var label = ctx.el("label", "field-label", f.label);
    if (f.type !== "bool") label.setAttribute("for", id);
    if (req) {
      var star = ctx.el("i", "field-req", "*");
      star.setAttribute("aria-hidden", "true");
      label.appendChild(star);
      label.appendChild(ctx.el("span", "visually-hidden", "（必填）"));
    }
    wrap.appendChild(label);

    if (f.type === "bool") {
      wrap.appendChild(boolNode(ctype, f, value));
    } else if (f.type === "textarea") {
      var ta = ctx.el("textarea");
      ta.id = id;
      ta.rows = 4;
      ta.spellcheck = false;
      ta.value = String(value == null ? "" : value);
      if (f.default && !ta.value) ta.placeholder = String(f.default);
      ta.addEventListener("input", function () { onEdit(ctype, f.key, ta.value); });
      wrap.appendChild(ta);
    } else {
      var input = ctx.el("input");
      input.id = id;
      input.type = f.type === "password" ? "password" : "text";
      input.spellcheck = false;
      /* int 字段也走 type=text：config.json 里 port 存的是 "465" 字符串，
         type=number 会把它变成数字再回写，`git diff` 就多出一堆引号增删；
         而且非法输入时 value 直接变空串，用户看不到自己敲了什么。 */
      if (f.type === "int") input.inputMode = "numeric";
      input.value = String(value == null ? "" : value);
      if (f.default && !input.value) input.placeholder = String(f.default);
      input.addEventListener("input", function () { onEdit(ctype, f.key, input.value); });

      if (f.type === "password") wrap.appendChild(revealWrap(input, f.label));
      else wrap.appendChild(input);
    }

    if (f.hint) wrap.appendChild(ctx.el("p", "field-hint", f.hint));
    return wrap;
  }

  function boolNode(ctype, f, value) {
    var ctx = state.ctx;
    var box = ctx.el("div", "field-toggle");

    var sw = ctx.el("button", "switch");
    sw.type = "button";
    sw.setAttribute("role", "switch");
    sw.setAttribute("aria-label", f.label);
    sw.setAttribute("aria-checked", truthy(value) ? "true" : "false");

    var text = ctx.el("span", "field-toggle-text", truthy(value) ? "开" : "关");

    sw.addEventListener("click", function () {
      var next = !truthy((state.draft[ctype] || {})[f.key]);
      sw.setAttribute("aria-checked", next ? "true" : "false");
      text.textContent = next ? "开" : "关";
      onEdit(ctype, f.key, next);        /* 真布尔，不是 "true" 字符串 */
    });

    box.appendChild(sw);
    box.appendChild(text);
    return box;
  }

  /** 密码框 + 眼睛按钮。tkinter 版没有，用户没法核对自己粘的 secret。 */
  function revealWrap(input, label) {
    var ctx = state.ctx;
    var holder = ctx.el("div", "field-input");
    holder.appendChild(input);

    var btn = ctx.el("button", "field-reveal");
    btn.type = "button";
    btn.setAttribute("aria-label", "显示 " + label);
    btn.setAttribute("aria-pressed", "false");
    btn.appendChild(ctx.icon(ICON_EYE));

    btn.addEventListener("click", function () {
      var shown = input.type === "text";
      input.type = shown ? "password" : "text";
      btn.setAttribute("aria-pressed", shown ? "false" : "true");
      btn.setAttribute("aria-label", (shown ? "显示 " : "隐藏 ") + label);
      btn.textContent = "";
      btn.appendChild(ctx.icon(shown ? ICON_EYE : ICON_EYE_OFF));
      input.focus();
    });

    holder.appendChild(btn);
    return holder;
  }

  function onEdit(ctype, key, value) {
    var d = state.draft[ctype];
    if (!d) { d = {}; state.draft[ctype] = d; }
    d[key] = value;
    refreshGates(ctype);
  }

  /** 重算按钮可用性 / 禁用原因 / 未保存标记。每次敲键都跑，别做重活。 */
  function refreshGates(ctype) {
    if (!ctype || ctype !== state.current) return;
    var save = state.nodes.save;
    var test = state.nodes.test;
    var why = state.nodes.why;
    if (!save || !test || !why) return;

    var miss = missingInDraft(ctype);
    var ready = miss.length === 0;
    /* 禁用必须写明缺什么，而且要写在按钮旁边：只塞 title 等于没写，
       鼠标悬停才看得见（§3.3，上一版就踩过「按钮灰了不说为什么」）。 */
    var msg = ready ? "" : "还需要填写：" + miss.join("、");

    save.disabled = !ready || state.busy;
    save.title = msg;
    test.disabled = !ready || state.busy;
    test.title = msg;

    why.textContent = msg;
    why.hidden = !msg;

    if (state.nodes.dirty) {
      var dirty = isDirty(ctype);
      state.nodes.dirty.textContent = dirty ? "有未保存的改动" : "";
      state.nodes.dirty.hidden = !dirty;
    }

    if (state.nodes.openid) {
      var v = state.draft[ctype] || {};
      var ok = !!(String(v.appid || "").trim() && String(v.secret || "").trim());
      state.nodes.openid.disabled = !ok || state.busy;
      state.nodes.openid.title = ok ? "" : "填好 appID 和 appsecret 才能自动获取";
    }
  }

  function setBusy(flag, what) {
    state.busy = !!flag;
    state.busyWhat = flag ? (what || "save") : "";
    paintTestButton();
    refreshGates(state.current);
  }

  function paintTestButton() {
    var ctx = state.ctx;
    var test = state.nodes.test;
    if (!test) return;
    test.textContent = "";
    if (state.busy && state.busyWhat === "test") {
      test.setAttribute("aria-busy", "true");
      test.appendChild(spinner());
      test.appendChild(ctx.el("span", null, "发送中…"));
    } else {
      test.removeAttribute("aria-busy");
      test.textContent = "测试此通道";
    }
  }

  function spinner() {
    var s = document.createElement("span");
    s.className = "spin";
    s.setAttribute("aria-hidden", "true");
    return s;
  }

  /* ---- 动作：保存 -------------------------------------------------------- */

  function saveChannel(ctype) {
    if (state.busy) return;
    var miss = missingInDraft(ctype);
    if (miss.length) {
      state.ctx.notify("还需要填写：" + miss.join("、"));
      return;
    }

    /* 只提交**这一个通道**。merge_save 是读-改-写：它重新 load 磁盘，
       只覆盖 patch 里出现的通道/字段，别的通道（可能被 hook 进程改过）原样保留。 */
    var patch = { type: ctype };
    fieldsOf(ctype).forEach(function (f) {
      var v = (state.draft[ctype] || {})[f.key];
      patch[f.key] = v === undefined ? "" : v;
    });

    setBusy(true, "save");
    state.ctx.call("config_save", { channels: [patch] })
      .then(function (res) {
        return state.ctx.call("channels_state").then(function (list) {
          /* §4：**用磁盘返回的那份覆盖本地**，不是用自己提交的那份。
             auto_enable 可能在保存过程中把某个通道自动开了/关了，
             不刷新的话用户看不到，会以为没生效。 */
          adopt(list, null);
          paintList();
          paintDetail();
          announceSave(ctype, res);
          state.ctx.refreshChain();
        });
      })
      .catch(function (e) {
        showResult(ctype, "bad", "保存失败：" + String((e && e.message) || e));
      })
      .then(function () { setBusy(false); });
  }

  function announceSave(ctype, res) {
    var m = metaOf(ctype);
    var name = (m && m.short) || ctype;
    var text = "已保存";

    if (res && res.turned_on && res.turned_on.length) {
      text += "，已自动启用：" + res.turned_on.join("、");
    }
    if (res && res.turned_off && res.turned_off.length) {
      text += "，已自动关闭：" + res.turned_off.join("、");
    }
    state.ctx.notify(name + " " + text, "ok");
  }

  /* ---- 动作：每通道独立开关 ----------------------------------------------
     语义与 tkinter 版 `_on_channel_switch` 逐条对齐：
       - 打开时凭证不全 → 不开、回弹、告诉用户还差什么
       - 关掉时记 manual_off，自动启用逻辑不再插手（否则会「关了又自己开」） */

  function toggle(ctype, next) {
    if (state.busy) return;
    var st = stateOf(ctype);

    if (next) {
      var miss = missingInDraft(ctype);
      if (miss.length) {
        state.ctx.notify("还差 " + miss.join("、") + " 才能启用", "danger");
        paintDetail();          /* 开关回弹到关 */
        focusFirstMissing(ctype);
        return;
      }
    }

    var patch = { type: ctype, enabled: !!next };
    /* 开：清掉 manual_off（传 false 而不是删键——merge 是 update 语义，
       删不掉键；auto_enable 判的是真假值，false 等价于没有）。 */
    patch.manual_off = next ? false : true;

    setBusy(true, "save");
    state.ctx.call("config_save", { channels: [patch] })
      .then(function () {
        return state.ctx.call("channels_state").then(function (list) {
          adopt(list, isDirty(state.current) ? state.current : null);
          paintList();
          paintDetail();
          state.ctx.refreshChain();
          var m = metaOf(ctype);
          state.ctx.notify(
            ((m && m.short) || ctype) + (next ? " 已启用" : " 已关闭"),
            next ? "ok" : "danger");
        });
      })
      .catch(function (e) {
        state.ctx.notify("没保存成功：" + String((e && e.message) || e), "danger");
        paintDetail();
      })
      .then(function () { setBusy(false); });
  }

  function focusFirstMissing(ctype) {
    var v = state.draft[ctype] || {};
    var key = requiredOf(ctype).filter(function (k) {
      return !String(v[k] == null ? "" : v[k]).trim();
    })[0];
    if (!key) return;
    var node = document.getElementById("f-" + ctype + "-" + key);
    if (node && node.focus) node.focus();
  }

  /* ---- 动作：测试（异步，窗口不许冻） ------------------------------------ */

  function testChannel(ctype) {
    if (state.busy) return;
    clearResult();

    /* 测的是**表单里当前的值**，不是磁盘上那份：用户改完还没保存就点测试，
       要试的是他眼前这一份（tkinter 版 _collect() 之后再测，同语义）。 */
    var fields = {};
    fieldsOf(ctype).forEach(function (f) {
      fields[f.key] = (state.draft[ctype] || {})[f.key];
    });

    var cb = state.ctx.nextCb(function (ok, data) {
      stopTimer();
      setBusy(false);
      if (ok) {
        /* 「写入本地日志」是唯一一个本机通道，它不发到任何设备，
           说「去手机上看看」会让人拿着手机等一条永远不来的通知。 */
        showResult(ctype, "ok", ctype === "log"
          ? "已写入 logs/notifications.log"
          : "已发送，去手机上看看");
      } else {
        /* 这是远端返回的 body，不可信文本。只走 textContent。 */
        showResult(ctype, "bad", (data && data.msg) || "发送失败（没有返回原因）");
      }
    });

    setBusy(true, "test");
    state.timer = window.setTimeout(function () {
      state.timer = 0;
      state.ctx.dropCb(cb);
      setBusy(false);
      showResult(ctype, "bad", "等待超时，请检查网络或看日志");
    }, testTimeout);

    state.ctx.call("channel_test", ctype, fields, cb).catch(function (e) {
      stopTimer();
      state.ctx.dropCb(cb);
      setBusy(false);
      showResult(ctype, "bad", "没能开始测试：" + String((e && e.message) || e));
    });
  }

  function fetchOpenid(ctype) {
    if (state.busy) return;
    clearResult();

    var v = state.draft[ctype] || {};
    var fields = { appid: String(v.appid || ""), secret: String(v.secret || "") };

    var cb = state.ctx.nextCb(function (ok, data) {
      stopTimer();
      setBusy(false);

      if (!ok) {
        showResult(ctype, "bad", (data && data.error) || "没能取到 openid");
        return;
      }
      var ids = (data && data.ids) || [];
      if (!ids.length) {
        showResult(ctype, "bad", "关注者列表是空的：先用微信扫测试号页面上的二维码关注它");
        return;
      }
      onEdit(ctype, "openid", ids[0]);
      paintDetail();      /* 重画才能看到新值；此时 draft 里有 openid，标记为未保存 */
      showResult(ctype, "ok", "已填入 openid" +
        (ids.length > 1 ? "（共 " + ids.length + " 个关注者，取了第一个）" : ""));
    });

    setBusy(true, "openid");
    state.timer = window.setTimeout(function () {
      state.timer = 0;
      state.ctx.dropCb(cb);
      setBusy(false);
      showResult(ctype, "bad", "等待超时，请检查网络或看日志");
    }, testTimeout);

    state.ctx.call("channel_fetch_openid", fields, cb).catch(function (e) {
      stopTimer();
      state.ctx.dropCb(cb);
      setBusy(false);
      showResult(ctype, "bad", "没能开始获取：" + String((e && e.message) || e));
    });
  }

  function stopTimer() {
    if (state.timer) {
      window.clearTimeout(state.timer);
      state.timer = 0;
    }
  }

  /* ---- 结果块 ------------------------------------------------------------
     结果存一份在 state 里再画，而不是直接往 DOM 里塞：测试要跑 8 秒，
     这期间用户很可能切到别的页或点开别的通道，回来时 DOM 已经重建过，
     只写 DOM 的话结果就丢了。 */

  function showResult(ctype, tone, text) {
    state.result = { ctype: ctype, tone: tone, text: text };
    paintResult();

    /* 成功 3 秒后淡出；失败留着，用户要照着改（§3.4） */
    if (tone === "ok") {
      stopTimer();
      state.timer = window.setTimeout(function () {
        state.timer = 0;
        var box = state.nodes.result;
        if (!box) return;
        box.classList.add("is-fading");
        state.timer = window.setTimeout(function () {
          state.timer = 0;
          state.result = null;
          paintResult();
        }, 240);
      }, 3000);
    }
  }

  function paintResult() {
    var ctx = state.ctx;
    var box = state.nodes.result;
    if (!box) return;

    var r = state.result;
    box.textContent = "";
    box.classList.remove("is-fading");

    if (!r || r.ctype !== state.current) {
      box.hidden = true;
      return;
    }

    box.hidden = false;
    box.dataset.tone = r.tone;
    box.appendChild(ctx.icon(r.tone === "ok" ? ICON_CHECK : ICON_ALERT, "chan-result-icon"));
    /* 错误原文是远端返回的 body，不可信文本，只走 textContent */
    box.appendChild(ctx.el("span", "chan-result-text", r.text));

    try { box.scrollIntoView({ block: "nearest" }); } catch (e) { /* 老引擎没有这个 API */ }
  }

  function clearResult() {
    stopTimer();
    state.result = null;
    paintResult();
  }

  /* ---- 错误态 ------------------------------------------------------------ */

  function showError(err) {
    var ctx = state.ctx;
    state.root.textContent = "";
    state.root.appendChild(ctx.el("p", "page-sub", "读不到通道配置。"));

    var box = ctx.el("div", "error-box block");
    box.appendChild(ctx.el("p", "error-title", "读配置失败"));
    box.appendChild(ctx.el("p", "error-text", String((err && err.message) || err)));

    var actions = ctx.el("div", "row-actions");
    var retry = ctx.el("button", "btn btn-primary", "重试");
    retry.type = "button";
    retry.addEventListener("click", function () {
      state.built = false;
      state.meta = null;
      build();
      load();
    });
    actions.appendChild(retry);
    box.appendChild(actions);

    state.root.appendChild(box);
    state.built = false;
  }

  /* ---- 对外接口 ---------------------------------------------------------- */

  window.WBN_PAGES.channels = {
    mount: function (root, ctx) {
      state.root = root;
      state.ctx = ctx;
      build();
      load();
    },
    show: function () {
      if (!state.built) { build(); load(); return; }
      if (!state.meta) { load(); return; }
      refresh();
    }
  };

  /** 回到这一页时重读磁盘（hook 进程可能改过配置），但别冲掉用户正在改的字。 */
  function refresh() {
    if (state.loading) return;
    state.loading = true;
    state.ctx.call("channels_state").then(function (list) {
      adopt(list, isDirty(state.current) ? state.current : null);
      paintList();
      paintDetail();
    }).catch(function () {
      /* 读不到就维持现状，不打断用户 */
    }).then(function () {
      state.loading = false;
    });
  }
})();
