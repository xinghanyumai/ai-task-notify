/* 「消息模板」页（W4）—— 7 种类型的标题/正文 + 实时预览 + 占位符面板。
 *
 * 这一页是全站最危险的一块：预览框里显示的是「AI 任务产出的正文」，
 * 完全不可信。所以三条不许破：
 *
 *   1. **预览只许 `textContent`。** 不用 `innerHTML` 换 `**加粗**` 好看——
 *      一条 `<img src=x onerror=...>` 就能读到 `window.pywebview.api`，
 *      再 `config_get()` 拿到全部通道密钥（`app.py` 带 --no-sandbox）。
 *   2. **渲染只在后端。** 前端自己替换 `{project}` 是十行代码，但 `render.py`
 *      里有换行处理、`channels.plain_text` 里有 markdown 剥离、
 *      `render_message` 里有标题回落——抄一遍就是两套规则，
 *      用户会发现「预览是这样，手机上不是」（任务书 §2.2）。
 *   3. **占位符清单从 `render.PLACEHOLDERS` 来。** 任务书 §2.1 那 6 个里有
 *      `{title}` / `{detail}`，本项目**没有**这两个变量（那是微信模板消息的
 *      `{{title.DATA}}`）——照抄会让用户插入两个永远渲染成空的占位符。
 */
(function () {
  "use strict";

  window.WBN_PAGES = window.WBN_PAGES || {};

  var DEBOUNCE = 300;

  var state = {
    root: null,
    ctx: null,
    built: false,
    loading: false,
    saving: false,

    meta: null,             /* ui_meta 的 data：template_kinds / placeholders */
    kind: null,             /* 当前编辑的类型 */
    draft: { titles: {}, templates: {} },
    base: { titles: {}, templates: {} },

    seq: 0,                 /* 预览请求序号：只认最后一次（W4 审计 B10） */
    timer: 0,
    dirtySent: null,
    dirtyTimer: 0,
    nodes: {}
  };

  function copy(o) {
    var out = {};
    Object.keys(o || {}).forEach(function (k) { out[k] = o[k]; });
    return out;
  }

  function isDirty() {
    var kinds = (state.meta && state.meta.template_kinds) || [];
    for (var i = 0; i < kinds.length; i++) {
      var k = kinds[i].key;
      if (String(state.draft.titles[k] || "") !== String(state.base.titles[k] || "")) return true;
      if (String(state.draft.templates[k] || "") !== String(state.base.templates[k] || "")) return true;
    }
    return false;
  }

  function syncDirty() {
    var now = isDirty();
    if (now === state.dirtySent) return;
    if (state.dirtyTimer) {
      window.clearTimeout(state.dirtyTimer);
      state.dirtyTimer = 0;
    }
    function send() {
      state.dirtyTimer = 0;
      state.dirtySent = now;
      state.ctx.call("set_dirty", now, "templates").catch(function () {
        state.dirtySent = null;
      });
    }
    if (now) send();
    else state.dirtyTimer = window.setTimeout(send, 200);
  }

  /* ---- 骨架 -------------------------------------------------------------- */

  function build() {
    var ctx = state.ctx;
    state.root.textContent = "";
    state.nodes.sub = ctx.el("p", "page-sub",
      "改的是发到手机上的那句话。下面预览和实际推送用的是同一段渲染代码。");
    state.root.appendChild(state.nodes.sub);
    state.nodes.body = ctx.el("div", "page-body");
    state.root.appendChild(state.nodes.body);
    state.built = true;
  }

  function skeleton() {
    var ctx = state.ctx;
    state.nodes.body.textContent = "";
    var g = ctx.el("section", "group");
    g.appendChild(ctx.el("div", "sk sk-row"));
    var body = ctx.el("div", "body");
    body.appendChild(ctx.el("div", "sk sk-value"));
    body.appendChild(ctx.el("div", "sk sk-row"));
    g.appendChild(body);
    state.nodes.body.appendChild(g);
  }

  /* ---- 加载 -------------------------------------------------------------- */

  function load() {
    if (state.loading) return Promise.resolve();
    state.loading = true;
    skeleton();

    return Promise.all([
      state.ctx.call("ui_meta"),
      state.ctx.call("config_get")
    ]).then(function (res) {
      adopt(res[0], res[1].cfg || {});
      render();
      requestPreview();
    }).catch(function (e) {
      showError(e);
    }).then(function () {
      state.loading = false;
    });
  }

  function adopt(meta, cfg) {
    var dirty = isDirty();
    state.meta = meta;

    var nextT = copy(cfg.titles || {});
    var nextC = copy(cfg.templates || {});
    /* 缺键也要能显示：`deep_merge` 一般会补默认，但手改过的 config.json
       可能把整个 templates 写成 {}，这时候键不存在，补空串而不是 undefined。 */
    (meta.template_kinds || []).forEach(function (k) {
      if (nextT[k.key] == null) nextT[k.key] = "";
      if (nextC[k.key] == null) nextC[k.key] = "";
    });

    state.base = { titles: nextT, templates: nextC };
    if (!dirty) state.draft = { titles: copy(nextT), templates: copy(nextC) };

    if (!state.kind || !findKind(state.kind)) {
      var first = (meta.template_kinds || [])[0];
      state.kind = first ? first.key : null;
    }
  }

  function findKind(key) {
    var list = (state.meta && state.meta.template_kinds) || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].key === key) return list[i];
    }
    return null;
  }

  /* ---- 渲染 -------------------------------------------------------------- */

  function render() {
    if (!state.meta) return;
    var ctx = state.ctx;
    state.nodes.body.textContent = "";
    state.nodes.body.appendChild(segBar());

    if (!state.kind) {
      state.nodes.body.appendChild(emptyBox(
        "没有任何消息模板。",
        "配置里没有 templates，这一页没东西可改。先去配一条通道，发一条测试消息看看。",
        "M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6"));
      return;
    }

    state.nodes.body.appendChild(editorGroup());
    state.nodes.body.appendChild(placeholderGroup());
    state.nodes.body.appendChild(previewGroup());
    paintDirty();
    syncDirty();
  }

  function segBar() {
    var ctx = state.ctx;
    var seg = ctx.el("div", "seg");
    seg.setAttribute("role", "group");
    seg.setAttribute("aria-label", "模板类型");

    (state.meta.template_kinds || []).forEach(function (k) {
      var b = ctx.el("button", "seg-item", k.label);
      b.type = "button";
      b.dataset.kind = k.key;
      var on = k.key === state.kind;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.addEventListener("click", function () { switchKind(k.key); });
      seg.appendChild(b);
    });

    var wrap = ctx.el("div", "seg-wrap");
    wrap.appendChild(seg);
    return wrap;
  }

  function switchKind(key) {
    if (key === state.kind) return;
    state.kind = key;
    /* 只换编辑器里的值，不重建整页：重建会丢焦点、把滚动弹回顶部，
       而用户刚点的就是顶部的分段控件。 */
    if (state.nodes.title) state.nodes.title.value = state.draft.titles[key] || "";
    if (state.nodes.content) state.nodes.content.value = state.draft.templates[key] || "";
    paintSeg();
    paintDirty();
    requestPreview();
  }

  function paintSeg() {
    var items = state.nodes.body.querySelectorAll(".seg-item");
    Array.prototype.forEach.call(items, function (b) {
      var on = b.dataset.kind === state.kind;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function group(title, desc) {
    var ctx = state.ctx;
    var g = ctx.el("section", "group");
    g.appendChild(ctx.el("h2", null, title));
    if (desc) g.appendChild(ctx.el("p", "desc", desc));
    var body = ctx.el("div", "body");
    g.appendChild(body);
    return { root: g, body: body };
  }

  function editorGroup() {
    var ctx = state.ctx;
    var g = group("标题与正文", "改动即时反映到下面的预览");

    var titleField = ctx.el("div", "field");
    var l1 = ctx.el("label", "field-label", "标题");
    l1.setAttribute("for", "tpl-title");
    titleField.appendChild(l1);

    var input = ctx.el("input");
    input.id = "tpl-title";
    input.type = "text";
    input.spellcheck = false;
    input.dataset.tpl = "1";
    input.value = state.draft.titles[state.kind] || "";
    input.addEventListener("input", function () {
      onEdit("titles", input.value);
    });
    titleField.appendChild(input);

    /* 标题回落是 `render_message` 的既有行为，不许改（§6 禁止改渲染规则）。
       但必须说出来：用户清空标题，以为发出去没标题，实际发的是「任务完成」的
       那一句（W4 审计 B3）。 */
    titleField.appendChild(ctx.el("p", "field-hint",
      "留空会用「任务完成」的标题顶上——这是实际发出去的样子，预览里能看到。"));

    state.nodes.title = input;
    g.body.appendChild(titleField);

    var contentField = ctx.el("div", "field");
    var l2 = ctx.el("label", "field-label", "正文");
    l2.setAttribute("for", "tpl-content");
    contentField.appendChild(l2);

    var ta = ctx.el("textarea");
    ta.id = "tpl-content";
    ta.rows = 7;
    ta.spellcheck = false;
    ta.dataset.tpl = "1";
    ta.value = state.draft.templates[state.kind] || "";
    ta.addEventListener("input", function () {
      onEdit("templates", ta.value);
    });
    contentField.appendChild(ta);
    contentField.appendChild(ctx.el("p", "field-hint",
      "换行会原样保留。`**粗体**` 这类 markdown 记号在纯文本通道里会被去掉。"));
    state.nodes.content = ta;
    g.body.appendChild(contentField);

    var actions = ctx.el("div", "row-actions");
    state.nodes.save = ctx.el("button", "btn btn-primary", "保存配置");
    state.nodes.save.type = "button";
    state.nodes.save.addEventListener("click", save);
    actions.appendChild(state.nodes.save);

    state.nodes.dirty = ctx.el("span", "dirty-tag");
    actions.appendChild(state.nodes.dirty);
    g.body.appendChild(actions);

    return g.root;
  }

  function placeholderGroup() {
    var ctx = state.ctx;
    var g = group("可用占位符", "点一下插到光标处，发送前会被替换成真实内容");

    var box = ctx.el("div", "chips");
    (state.meta.placeholders || []).forEach(function (p) {
      var b = ctx.el("button", "chip");
      b.type = "button";
      b.title = p.desc;
      b.appendChild(ctx.el("span", "chip-token", p.token));
      b.appendChild(ctx.el("span", "chip-desc", p.desc));
      b.addEventListener("click", function () { insertToken(p.token); });
      box.appendChild(b);
    });
    g.body.appendChild(box);
    return g.root;
  }

  function previewGroup() {
    var ctx = state.ctx;
    var g = group("实时预览", "示例数据：项目名、耗时、主机名都是固定的假值，只有时间是此刻");

    var box = ctx.el("div", "preview");
    state.nodes.pTitle = ctx.el("div", "preview-title");
    state.nodes.pBody = ctx.el("div", "preview-body");
    box.appendChild(state.nodes.pTitle);
    box.appendChild(state.nodes.pBody);
    g.body.appendChild(box);

    /* 未知占位符只能在这里列：它们在渲染阶段已经被替换成空字符串，
       正文里没有位置可以标色（W4 审计 B6）。 */
    state.nodes.missing = ctx.el("p", "field-error");
    state.nodes.missing.dataset.level = "warn";
    state.nodes.missing.hidden = true;
    g.body.appendChild(state.nodes.missing);

    return g.root;
  }

  /* ---- 编辑 -------------------------------------------------------------- */

  function onEdit(which, value) {
    state.draft[which][state.kind] = value;
    paintDirty();
    syncDirty();
    schedulePreview();
  }

  /** 300ms 防抖。**不 `oninput` 直接打后端**：一段 200 字的正文是 200 次调用。 */
  function schedulePreview() {
    if (state.timer) window.clearTimeout(state.timer);
    state.timer = window.setTimeout(function () {
      state.timer = 0;
      requestPreview();
    }, DEBOUNCE);
  }

  function requestPreview() {
    if (!state.meta || !state.kind) return;
    var seq = ++state.seq;          /* 作废所有还在飞的旧请求 */
    var kind = state.kind;
    var title = state.draft.titles[kind] || "";
    var content = state.draft.templates[kind] || "";

    state.ctx.call("template_preview", kind, title, content).then(function (d) {
      if (seq !== state.seq) return;   /* 旧响应：它算的是上一次的文本（B10） */
      paintPreview(d);
    }).catch(function (e) {
      if (seq !== state.seq) return;
      paintPreviewError(e);
    });
  }

  function paintPreview(d) {
    var ctx = state.ctx;
    var title = String((d && d.title) || "");
    var body = String((d && d.body) || "");

    /* **只许 textContent。** 这里显示的是 AI 产出的正文，完全不可信。 */
    state.nodes.pTitle.textContent = title || "（标题为空）";
    state.nodes.pBody.textContent = body;

    if (!body) {
      state.nodes.pBody.textContent = "";
      state.nodes.pBody.appendChild(ctx.el("span", "preview-empty",
        "正文是空的，发出去只有标题。"));
    }

    var missing = (d && d.vars_missing) || [];
    if (missing.length) {
      state.nodes.missing.textContent = "";
      state.nodes.missing.appendChild(ctx.el("span", null,
        "未识别的占位符：" + missing.join("、") + " —— 发出去是空白"));
      state.nodes.missing.hidden = false;
    } else {
      state.nodes.missing.hidden = true;
      state.nodes.missing.textContent = "";
    }
  }

  function paintPreviewError(e) {
    state.nodes.pTitle.textContent = "（预览失败）";
    state.nodes.pBody.textContent = String((e && e.message) || e);
  }

  /** 插入到**光标处**，不是末尾（任务书 §2.3）。
   *  `document.execCommand('insertText')` 已废弃，用 `setRangeText`。 */
  function insertToken(token) {
    var active = document.activeElement;
    var target = (active && active.dataset && active.dataset.tpl === "1")
      ? active
      : state.nodes.content;
    if (!target) return;

    var start = target.selectionStart;
    var end = target.selectionEnd;
    if (start == null) { start = target.value.length; end = start; }

    target.setRangeText(token, start, end, "end");
    target.focus();

    if (target === state.nodes.content) onEdit("templates", target.value);
    else onEdit("titles", target.value);
  }

  function paintDirty() {
    var d = state.nodes.dirty;
    if (!d) return;
    var dirty = isDirty();
    d.textContent = dirty ? "有未保存的改动" : "";
    d.hidden = !dirty;
  }

  /* ---- 保存 -------------------------------------------------------------- */

  function save() {
    if (state.saving || !state.meta) return;

    /* 只提交**改过的类型**：`merge_save` 是读-改-写，没改的键不出现在 patch 里，
       就不会被内存里那份覆盖掉别人（hook 进程）写进去的值。 */
    var titles = {}, templates = {};
    var n = 0;
    (state.meta.template_kinds || []).forEach(function (k) {
      var t = String(state.draft.titles[k.key] || "");
      var c = String(state.draft.templates[k.key] || "");
      if (t !== String(state.base.titles[k.key] || "")) { titles[k.key] = t; n++; }
      if (c !== String(state.base.templates[k.key] || "")) { templates[k.key] = c; n++; }
    });

    if (!n) {
      state.ctx.notify("没有改动", "ok");
      return;
    }

    state.saving = true;
    /* 临时禁用：只改 aria-disabled，不动 `disabled`——给已聚焦的按钮设 `disabled`
       会立刻 blur，焦点掉到 body，键盘用户保存完就丢了位置（W6.4）。
       重入由函数第一行的 `if (state.saving) return;` 挡住，不靠禁用按钮。 */
    window.WBN_UI.setBusyAttr(state.nodes.save, true);
    state.nodes.save.textContent = "保存中…";

    var patch = {};
    if (Object.keys(titles).length) patch.titles = titles;
    if (Object.keys(templates).length) patch.templates = templates;

    state.ctx.call("config_save", patch).then(function (res) {
      adopt(state.meta, (res && res.cfg) || {});
      paintDirty();
      syncDirty();
      requestPreview();
      state.ctx.notify("模板已保存", "ok");
    }).catch(function (e) {
      state.ctx.notify("没保存成功：" + String((e && e.message) || e), "danger");
    }).then(function () {
      state.saving = false;
      window.WBN_UI.setBusyAttr(state.nodes.save, false);
      state.nodes.save.textContent = "保存配置";
    });
  }

  /* ---- 空态 / 错误态 ----------------------------------------------------- */

  function emptyBox(title, hint, iconPath) {
    var ctx = state.ctx;
    var box = ctx.el("div", "empty block");
    box.appendChild(ctx.icon(iconPath, "empty-icon"));
    box.appendChild(ctx.el("p", "empty-title", title));
    box.appendChild(ctx.el("p", "empty-hint", hint));
    return box;
  }

  function showError(err) {
    var ctx = state.ctx;
    state.root.textContent = "";
    state.root.appendChild(ctx.el("p", "page-sub", "读不到消息模板。"));

    var box = ctx.el("div", "error-box block");
    box.appendChild(ctx.el("p", "error-title", "读配置失败"));
    box.appendChild(ctx.el("p", "error-text", String((err && err.message) || err)));

    var actions = ctx.el("div", "row-actions");
    var retry = ctx.el("button", "btn btn-primary", "重试");
    retry.type = "button";
    retry.addEventListener("click", function () {
      state.built = false;
      build();
      load();
    });
    actions.appendChild(retry);
    box.appendChild(actions);

    state.root.appendChild(box);
    state.built = false;
  }

  /* ---- 对外接口 ---------------------------------------------------------- */

  window.WBN_PAGES.templates = {
    mount: function (root, ctx) {
      state.root = root;
      state.ctx = ctx;
      build();
      load();
    },
    show: function () {
      if (!state.built) { build(); load(); return; }
      if (!state.meta) { load(); return; }
      state.loading = true;
      state.ctx.call("config_get").then(function (d) {
        adopt(state.meta, d.cfg || {});
        /* 有草稿时 adopt 不动 draft，所以这里要把输入框重新对齐 draft */
        if (state.nodes.title) state.nodes.title.value = state.draft.titles[state.kind] || "";
        if (state.nodes.content) state.nodes.content.value = state.draft.templates[state.kind] || "";
        paintDirty();
        requestPreview();
      }).catch(function () {
        /* 读不到就维持现状 */
      }).then(function () {
        state.loading = false;
      });
    },
    /** 切走时：停定时器 + 作废在飞的预览请求。
     *  **不许报 `set_dirty(false)`**——草稿还在，报干净等于关窗不拦（A12）。 */
    destroy: function () {
      if (state.timer) {
        window.clearTimeout(state.timer);
        state.timer = 0;
      }
      if (state.dirtyTimer) {
        window.clearTimeout(state.dirtyTimer);
        state.dirtyTimer = 0;
      }
      state.seq++;
    }
  };
})();
