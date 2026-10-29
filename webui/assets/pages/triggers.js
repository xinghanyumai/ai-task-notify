/* 「触发规则」页（W4）—— 六个事件开关 + 十个过滤字段。
 *
 * 三条硬约束：
 *   1. **结构从 `ui_meta()` 来**：6 个事件、10 个字段的键序与中文名都在 Python
 *      侧有唯一定义处，前端写死就是第二份，迟早和 tkinter 版说两样话。
 *   2. **保存走 `triggers_save()`**：过滤器的类型转换（`"20"` → 20、
 *      `"a,b"` → ["a","b"]）只在 `insight.normalize_filters` 里做一次。
 *      前端自己 `Number()` / `split()` 一遍，`projects_include` 存成字符串时
 *      `_match_any` 会逐字符匹配，过滤静默失效（W4 审计 A8）。
 *   3. **开关用 `!!v`，不用 `truthy()`**：`channels.js` 那个 `truthy()` 只认
 *      `true/1/"1"/"true"`，而 `filters.check` 判的是 Python 真值——手改配置
 *      写成字符串 `"false"` 时，前者显示「关」、后者照样发（W4 审计 A1）。
 *
 * 页面还会主动说两件「规则全开也不生效」的事（A9 / A10）：总开关关着、
 * 一条通道都没启用。这两句是 W3.4 那条教训的延伸——界面不许替失败背书。
 */
(function () {
  "use strict";

  window.WBN_PAGES = window.WBN_PAGES || {};

  var ICON_ALERT = "M12 3l9 17H3zM12 10v5M12 18v.5";
  var ICON_BOLT = "M13 3L4 14h7l-1 7 9-11h-7l1-7z";

  var state = {
    root: null,
    ctx: null,
    built: false,
    loading: false,
    saving: false,

    meta: null,             /* ui_meta 的 data：kinds / fields / enabled / … */
    draft: { triggers: {}, filters: {} },   /* 用户正在编辑的 */
    base: { triggers: {}, filters: {} },    /* 上次从磁盘读到的，用来判脏 */
    lastGood: {},           /* 数字字段「上一个合法值」，失焦校正用 */

    errors: {},             /* {key: {level, text}} 字段级提示 */
    qhActive: false,        /* 静默时段：按当前填的值，现在是否处于静默 */

    dirtySent: null,
    dirtyTimer: 0,
    nodes: {}
  };

  /* ---- 小工具 ------------------------------------------------------------ */

  function copy(o) {
    var out = {};
    Object.keys(o || {}).forEach(function (k) { out[k] = o[k]; });
    return out;
  }

  /** 字段在输入框里的字符串形态。list 用逗号拼——和 `ui.py` 一致。 */
  function draftStr(f) {
    var v = (state.draft.filters || {})[f.key];
    if (f.kind === "list") {
      return Array.isArray(v) ? v.join(", ") : (v == null ? "" : String(v));
    }
    return v == null ? "" : String(v);
  }

  function baseStr(f) {
    var v = (state.base.filters || {})[f.key];
    if (f.kind === "list") {
      return Array.isArray(v) ? v.join(", ") : (v == null ? "" : String(v));
    }
    return v == null ? "" : String(v);
  }

  /** 有没有未保存的改动。**必须比 base，不能比「提交过的那份」**——
   *  W3.5 的 D 项就是栽在「先写 base 再算脏」上，那样永远算不出脏。 */
  function isDirty() {
    if (!state.meta) return false;
    var kinds = state.meta.kinds || [];
    for (var i = 0; i < kinds.length; i++) {
      if (!!state.draft.triggers[kinds[i].key] !== !!state.base.triggers[kinds[i].key]) {
        return true;
      }
    }
    var fields = state.meta.fields || [];
    for (var j = 0; j < fields.length; j++) {
      var f = fields[j];
      if (f.kind === "bool") {
        if (!!state.draft.filters[f.key] !== !!state.base.filters[f.key]) return true;
      } else if (draftStr(f) !== baseStr(f)) {
        return true;
      }
    }
    return false;
  }

  /** 把「本页脏不脏」同步给后端。只报自己这一格（source="triggers"），
   *  别的页面脏着仍然拦关窗（W4 审计 A12）。 */
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
      state.ctx.call("set_dirty", now, "triggers").catch(function () {
        state.dirtySent = null;   /* 同步不上不影响编辑，下次再试 */
      });
    }

    if (now) send();                              /* 变脏立刻报：晚 200ms 就可能被关窗 */
    else state.dirtyTimer = window.setTimeout(send, 200);
  }

  /* ---- 骨架 -------------------------------------------------------------- */

  function group(title, desc) {
    var ctx = state.ctx;
    var g = ctx.el("section", "group");
    g.appendChild(ctx.el("h2", null, title));
    if (desc) g.appendChild(ctx.el("p", "desc", desc));
    var body = ctx.el("div", "body");
    g.appendChild(body);
    return { root: g, body: body };
  }

  function build() {
    var ctx = state.ctx;
    state.root.textContent = "";

    state.nodes.sub = ctx.el("p", "page-sub",
      "决定什么情况下给你发通知。选得越多越吵——多数人只需要前两项。");
    state.root.appendChild(state.nodes.sub);

    state.nodes.notices = ctx.el("div");
    state.root.appendChild(state.nodes.notices);

    state.nodes.body = ctx.el("div", "page-body");
    state.root.appendChild(state.nodes.body);

    state.built = true;
  }

  function skeleton() {
    var ctx = state.ctx;
    state.nodes.body.textContent = "";
    var g = group("正在读取…", "");
    for (var i = 0; i < 6; i++) {
      var row = ctx.el("div", "row");
      row.appendChild(ctx.el("div", "sk sk-row"));
      g.body.appendChild(row);
    }
    state.nodes.body.appendChild(g.root);
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
      /* 静默时段一进来就校验一次：上次填错的段会在打开时重新指出来，
         而不是等用户再去失焦一次才看见（W4 审计 A6 的闭环）。 */
      var qh = String((state.draft.filters || {}).quiet_hours || "");
      if (qh.trim()) checkQuiet(qh);
    }).catch(function (e) {
      showError(e);
    }).then(function () {
      state.loading = false;
    });
  }

  /** 把磁盘配置吃进本地。**先算脏、再覆盖 base**——顺序反了就永远不脏。 */
  function adopt(meta, cfg) {
    var dirty = isDirty();

    state.meta = meta;
    var srcT = cfg.triggers || {};
    var srcF = cfg.filters || {};

    var nextT = {};
    (meta.kinds || []).forEach(function (k) {
      /* `!!` 而不是 `truthy()`：和 Python 的 `bool()` 同义（W4 审计 A1） */
      nextT[k.key] = !!srcT[k.key];
    });

    var nextF = {};
    (meta.fields || []).forEach(function (f) {
      var v = srcF[f.key];
      nextF[f.key] = f.kind === "bool" ? !!v : v;
    });

    state.base = { triggers: nextT, filters: nextF };
    if (!dirty) state.draft = { triggers: copy(nextT), filters: copy(nextF) };

    /* lastGood 只对数字字段有意义：失焦时非法就回到它 */
    state.lastGood = {};
    (meta.fields || []).forEach(function (f) {
      if (f.kind !== "int") return;
      var v = (state.base.filters || {})[f.key];
      state.lastGood[f.key] = v == null ? "" : String(v);
    });
  }

  /* ---- 渲染 -------------------------------------------------------------- */

  function render() {
    if (!state.meta) return;
    var ctx = state.ctx;

    paintNotices();
    state.nodes.body.textContent = "";
    state.errors = {};
    state.nodes.input = {};
    state.nodes.err = {};
    state.nodes.switch = {};
    state.nodes.quiet = null;     /* 重建前先断掉旧引用，否则会写到已 detach 的节点上 */

    if (!(state.meta.kinds || []).length) {
      state.nodes.body.appendChild(emptyBox(
        "读不到事件类型。",
        "配置文件里没有任何触发条件，这一页没东西可改。",
        ICON_BOLT));
      return;
    }

    /* ---- 组 1：六个事件开关 ---- */
    var g1 = group("推送哪些消息", "选得越多越吵，多数人只需要前两项。");
    (state.meta.kinds || []).forEach(function (k) {
      g1.body.appendChild(kindRow(k));
    });
    state.nodes.body.appendChild(g1.root);

    /* ---- 组 2：过滤与限流 ---- */
    var g2 = group("过滤与限流", "用这些规则压掉噪音，避免手机被刷屏");
    var ints = [], lists = [], bools = [];
    (state.meta.fields || []).forEach(function (f) {
      if (f.kind === "bool") bools.push(f);
      else if (f.kind === "list") lists.push(f);
      else ints.push(f);
    });

    /* 「阈值」和「范围」两个小标题和 tkinter 版一致：6 个数字是一个问题，
       3 个列表是另一个问题，混在一屏里读者要自己分类（ui.py 就是这么分的）。 */
    if (ints.length) {
      g2.body.appendChild(ctx.el("h3", "sub-head", "阈值"));
      g2.body.appendChild(fieldGrid(ints));
    }
    if (lists.length) {
      g2.body.appendChild(ctx.el("h3", "sub-head", "范围"));
      g2.body.appendChild(fieldGrid(lists));
    }
    bools.forEach(function (f) { g2.body.appendChild(boolNode(f)); });

    var actions = ctx.el("div", "row-actions");
    state.nodes.save = ctx.el("button", "btn btn-primary", "保存配置");
    state.nodes.save.type = "button";
    state.nodes.save.addEventListener("click", save);
    actions.appendChild(state.nodes.save);

    state.nodes.dirty = ctx.el("span", "dirty-tag");
    actions.appendChild(state.nodes.dirty);
    g2.body.appendChild(actions);

    state.nodes.body.appendChild(g2.root);

    paintDirty();
    paintQuietHint();
    syncDirty();
  }

  /** 两列字段网格。**奇数个时最后一个占满整行**——否则右边会空出一格，
   *  看起来像少了一个字段（`ui.py` 的 `columnspan=2` 是同一个处理）。 */
  function fieldGrid(fields) {
    var ctx = state.ctx;
    var grid = ctx.el("div", "fields");
    fields.forEach(function (f, i) {
      var node = fieldNode(f);
      if (fields.length % 2 === 1 && i === fields.length - 1) {
        node.dataset.span = "2";
      }
      grid.appendChild(node);
    });
    return grid;
  }

  function kindRow(k) {
    var ctx = state.ctx;
    var row = ctx.el("div", "row");

    var text = ctx.el("div", "text");
    text.appendChild(ctx.el("div", "title", k.label));
    if (k.desc) text.appendChild(ctx.el("div", "desc", k.desc));
    row.appendChild(text);

    var sw = ctx.el("button", "switch");
    sw.type = "button";
    sw.setAttribute("role", "switch");
    sw.setAttribute("aria-label", k.label);
    sw.setAttribute("aria-checked", state.draft.triggers[k.key] ? "true" : "false");
    sw.addEventListener("click", function () {
      state.draft.triggers[k.key] = !state.draft.triggers[k.key];
      sw.setAttribute("aria-checked",
        state.draft.triggers[k.key] ? "true" : "false");
      paintDirty();
      syncDirty();
    });
    state.nodes.switch[k.key] = sw;
    row.appendChild(sw);

    return row;
  }

  function fieldNode(f) {
    var ctx = state.ctx;
    var id = "trig-" + f.key;

    var wrap = ctx.el("div", "field");
    var label = ctx.el("label", "field-label", f.label);
    label.setAttribute("for", id);
    wrap.appendChild(label);

    var input = ctx.el("input");
    input.id = id;
    input.spellcheck = false;
    if (f.kind === "int") {
      /* type=number + min/max 挡负数和非数字；失焦再兜一次（W4 §1.2②） */
      input.type = "number";
      input.min = String(f.lo);
      input.max = String(f.hi);
      input.step = "1";
      input.inputMode = "numeric";
    } else {
      input.type = "text";
    }
    input.value = draftStr(f);
    input.addEventListener("input", function () {
      onEdit(f, input.value);
    });
    input.addEventListener("blur", function () {
      onBlur(f, input);
    });
    wrap.appendChild(input);

    if (f.hint) wrap.appendChild(ctx.el("p", "field-hint", f.hint));

    /* 静默时段多一行「现在正处于静默」——只在真的处于静默时显示。
       它说的是「按哪份值算的」，不是实时状态（W4 审计 A7）。 */
    if (f.key === "quiet_hours") {
      var active = ctx.el("p", "field-hint");
      active.hidden = true;
      wrap.appendChild(active);
      state.nodes.quiet = active;
    }

    var err = ctx.el("p", "field-error");
    err.hidden = true;
    wrap.appendChild(err);

    state.nodes.input[f.key] = input;
    state.nodes.err[f.key] = err;
    return wrap;
  }

  function boolNode(f) {
    var ctx = state.ctx;
    var wrap = ctx.el("label", "checkbox");
    var box = ctx.el("input");
    box.type = "checkbox";
    box.checked = !!state.draft.filters[f.key];
    box.addEventListener("change", function () {
      onEdit(f, box.checked);
    });
    wrap.appendChild(box);
    wrap.appendChild(ctx.el("span", null, f.label));
    state.nodes.input[f.key] = box;
    return wrap;
  }

  /* ---- 提示条 ------------------------------------------------------------ */

  /** 页面级「现在这套设置不会生效」。
   *
   * 为什么必须有（W4 审计 A9 / A10）：总开关关着时 `filters.check` 第一句就
   * `return False`，一条通道都没启用时也没有出口——两种情况页面上六个开关
   * 都是绿的，用户改完以为通了。状态页那张「启用通道 0/10」的卡在这里看不见。
   *
   * 只说「有没有」，不说数字：数字在状态页有且只有一个出处（A10）。 */
  function paintNotices() {
    var ctx = state.ctx;
    var box = state.nodes.notices;
    box.textContent = "";
    var m = state.meta;
    if (!m) return;

    if (!m.enabled) {
      box.appendChild(notice("warn",
        "顶栏的通知总开关现在是关的，下面这些规则一条都不会生效。",
        "打开总开关", function () {
          var sw = document.getElementById("master-switch");
          if (sw) sw.click();
        }));
    }

    if (!m.any_channel_on) {
      box.appendChild(notice("warn",
        "还没有启用任何通道。规则全开也发不出去——得先有一条路是通的。",
        "去配置通道", function () { ctx.go("channels"); }));
    }
  }

  function notice(level, text, actionText, onAction) {
    var ctx = state.ctx;
    var n = ctx.el("div", "notice");
    n.dataset.level = level;
    n.appendChild(ctx.icon(ICON_ALERT, "notice-icon"));
    n.appendChild(ctx.el("span", "notice-text", text));
    if (actionText) {
      var b = ctx.el("button", "notice-action", actionText);
      b.type = "button";
      b.addEventListener("click", onAction);
      n.appendChild(b);
    }
    return n;
  }

  /* ---- 编辑与校验 -------------------------------------------------------- */

  function onEdit(f, value) {
    if (f.kind === "bool") state.draft.filters[f.key] = !!value;
    else state.draft.filters[f.key] = value;
    paintDirty();
    syncDirty();
  }

  function showErr(key, level, text) {
    state.errors[key] = { level: level, text: text };
    var node = state.nodes.err[key];
    if (!node) return;
    node.textContent = text;
    node.dataset.level = level;
    node.hidden = false;
  }

  function clearErr(key) {
    delete state.errors[key];
    var node = state.nodes.err[key];
    if (!node) return;
    node.textContent = "";
    node.hidden = true;
  }

  function onBlur(f, input) {
    if (f.kind === "int") return blurInt(f, input);
    if (f.key === "quiet_hours") return checkQuiet(input.value);
  }

  /** 数字失焦校正。三件事（W4 §1.2② / 审计 A4）：
   *   ① 空 / 非数字 / 负数 → 回到**上一个合法值**（不是 base：用户可能已经
   *      连着改了两次，回到磁盘那份会把他上一次的修改也吃掉）；
   *   ② 超过上限 → **不改值**，只提醒。手改过的 config.json 里可能真有
   *      99999，静默夹到 3600 等于替用户改配置；
   *   ③ 一律用字段下面那一行说明，不 `alert()`。 */
  function blurInt(f, input) {
    var raw = String(input.value).trim();
    var n = Number(raw);
    var bad = raw === "" || !isFinite(n) || Math.floor(n) !== n || n < f.lo;

    if (bad) {
      var back = state.lastGood[f.key] == null ? "" : state.lastGood[f.key];
      input.value = back;
      onEdit(f, back);
      showErr(f.key, "danger",
        "只能填 " + f.lo + " 以上的整数，已恢复为 " + (back === "" ? "空" : back));
      return;
    }

    state.lastGood[f.key] = String(n);
    if (String(n) !== raw) {
      input.value = String(n);      /* "007" → "7"，落盘的和你看到的一致 */
      onEdit(f, String(n));
    }

    if (n > f.hi) {
      showErr(f.key, "warn", "超过建议上限 " + f.hi + "，会按 " + n + " 保存");
    } else {
      clearErr(f.key);
    }
  }

  /** 静默时段校验。**规则在 Python 侧**（`filters._parse_hhmm`），
   *  这里只负责把「哪几段看不懂」翻译成一句话（W4 §1.2③ / 审计 A6）。 */
  function checkQuiet(spec) {
    state.ctx.call("quiet_hours_check", String(spec == null ? "" : spec))
      .then(function (d) {
        if (!d.ok) {
          state.qhActive = false;
          showErr("quiet_hours", "danger",
            "这几段看不懂，会被整段忽略：" + d.bad.join("、") +
            "。格式如 23:00-08:00，多段用逗号分隔");
          paintQuietHint();
          return;
        }
        clearErr("quiet_hours");
        state.qhActive = !!d.active_now;
        paintQuietHint();
      })
      .catch(function () {
        /* 校验本身失败不该挡住保存：后端 normalize 会原样收下这段字符串 */
      });
  }

  /** 「现在处于静默」只说一次、且写明是按哪份值算的：
   *  正在编辑时按**输入框里**的值算（保存后才生效），不脏时按磁盘那份算。
   *  这样它既不会假装实时，也不会在用户改了还没保存时撒谎（A7）。 */
  function paintQuietHint() {
    var node = state.nodes.quiet;
    if (!node) return;
    if (!state.qhActive) {
      node.textContent = "";
      node.hidden = true;
      return;
    }
    node.textContent = isDirty()
      ? "按这个时段，现在处于静默——保存后生效。"
      : "按已保存的时段，现在处于静默，不会推送。";
    node.hidden = false;
  }

  function paintDirty() {
    var d = state.nodes.dirty;
    if (!d) return;
    var dirty = isDirty();
    d.textContent = dirty ? "有未保存的改动" : "";
    d.hidden = !dirty;
    paintQuietHint();
  }

  /* ---- 保存 -------------------------------------------------------------- */

  function save() {
    if (state.saving || !state.meta) return;

    var triggers = {};
    (state.meta.kinds || []).forEach(function (k) {
      triggers[k.key] = !!state.draft.triggers[k.key];
    });

    /* 过滤器的原始字符串直接送过去，**由 Python 转类型**。
       这里是「第二套转换规则」唯一的入口，必须保持它是字符串。 */
    var raw = {};
    (state.meta.fields || []).forEach(function (f) {
      var v = state.draft.filters[f.key];
      raw[f.key] = f.kind === "bool" ? !!v : String(v == null ? "" : v);
    });

    state.saving = true;
    state.nodes.save.disabled = true;
    state.nodes.save.textContent = "保存中…";

    state.ctx.call("triggers_save", triggers, raw).then(function (res) {
      applySaved(res.triggers || {}, res.filters || {});
      var extra = "";
      if (res.turned_on && res.turned_on.length) {
        extra = "，已自动启用：" + res.turned_on.join("、");
      }
      state.ctx.notify("触发规则已保存" + extra, "ok");
      state.ctx.refreshChain();
      /* 保存会顺带跑 `auto_enable`：可能刚把某条通道打开。提示条说的是
         「有没有一条通道是通的」，所以要用新的 `ui_meta` 重算一次。 */
      state.ctx.call("ui_meta").then(function (m) {
        state.meta.enabled = m.enabled;
        state.meta.any_channel_on = m.any_channel_on;
        paintNotices();
      }).catch(function () { /* 重算失败不影响「已保存」这件事 */ });
    }).catch(function (e) {
      state.ctx.notify("没保存成功：" + String((e && e.message) || e), "danger");
    }).then(function () {
      state.saving = false;
      state.nodes.save.disabled = false;
      state.nodes.save.textContent = "保存配置";
      paintDirty();
    });
  }

  /** 保存成功后**用后端规整后的值回填**（`"1e3"` → `1000`、`"a, b"` → `["a","b"]`）。
   *
   *  只改输入框的值、不重建 DOM：重建会把滚动位置弹回顶部，而用户刚点的
   *  就是页面底部的保存按钮。 */
  function applySaved(triggers, filters) {
    state.base = { triggers: copy(triggers), filters: copy(filters) };
    state.draft = { triggers: copy(triggers), filters: copy(filters) };

    (state.meta.fields || []).forEach(function (f) {
      var node = state.nodes.input[f.key];
      if (!node) return;
      if (f.kind === "bool") node.checked = !!filters[f.key];
      else node.value = draftStr(f);
      if (f.kind === "int") {
        state.lastGood[f.key] = filters[f.key] == null ? "" : String(filters[f.key]);
      }
    });

    (state.meta.kinds || []).forEach(function (k) {
      var sw = state.nodes.switch[k.key];
      if (sw) {
        sw.setAttribute("aria-checked", triggers[k.key] ? "true" : "false");
      }
    });

    var qh = String(filters.quiet_hours || "");
    if (qh.trim()) checkQuiet(qh);
    else {
      state.qhActive = false;
      clearErr("quiet_hours");
    }

    syncDirty();
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
    state.root.appendChild(ctx.el("p", "page-sub", "读不到触发规则。"));

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

  window.WBN_PAGES.triggers = {
    mount: function (root, ctx) {
      state.root = root;
      state.ctx = ctx;
      build();
      load();
    },
    /** 回到这一页时重读磁盘（hook 进程可能改过配置），但别冲掉正在改的字。
     *  `adopt()` 里先算脏再覆盖 base，所以有草稿时草稿留着。 */
    show: function () {
      if (!state.built) { build(); load(); return; }
      if (!state.meta) { load(); return; }
      state.loading = true;
      Promise.all([
        state.ctx.call("ui_meta"),
        state.ctx.call("config_get")
      ]).then(function (res) {
        adopt(res[0], res[1].cfg || {});
        render();
        var qh = String((state.draft.filters || {}).quiet_hours || "");
        if (qh.trim()) checkQuiet(qh);
      }).catch(function () {
        /* 读不到就维持现状，不打断用户 */
      }).then(function () {
        state.loading = false;
      });
    },
    /** 切走时只停定时器。**不许在这里报 `set_dirty(false)`**——
     *  用户的草稿还在，报干净就等于关窗不拦、直接丢（W4 审计 A12）。 */
    destroy: function () {
      if (state.dirtyTimer) {
        window.clearTimeout(state.dirtyTimer);
        state.dirtyTimer = 0;
      }
    }
  };
})();
