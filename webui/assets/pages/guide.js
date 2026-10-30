/* 「使用指南」页（W4 长文 → W7.6 总栏 + 单项单选切换）。
 *
 * 结构：一条常驻的 tab 条（6 项：总栏 + 5 节）+ 一个面板。
 * **面板是条件渲染**——每次只把当前那一节 append 进 DOM，不是 5 节全渲染再
 * 用 CSS 藏起来。后者只是「看起来对」：读屏仍可能读到、Ctrl+F 仍能搜到，
 * DOM 里 5 节都在。判据见 `tests/test_guide_nav.py` 的 `test_panel_is_rendered_conditionally`。
 *
 * 状态只有一个字段：`state.section`。**不写 `location.hash`**（`shell.js` 的
 * `routeFromHash()` 把 hash 当路由名，不认识的值直接落回状态页——W7.4 踩过），
 * 也不写 localStorage：指南是查阅型页面，每次进来回到总栏比「停在上次看的那节」
 * 更可预期，还省掉一层持久化耦合。
 *
 * 无障碍按 WAI-ARIA APG 的 tabs 模式做全套：role=tablist/tab/tabpanel、
 * aria-selected、aria-controls、roving tabindex、←/→/Home/End。
 * 切换后焦点**留在 tab 上**，不搬进面板（W6 那批焦点问题就是这么来的）。
 *
 * 文案逐字照搬 `ui.py:_page_guide`（那一版是打磨过的），**只改四处控件指代**：
 *
 *   | 原文 | 改成 | 为什么 |
 *   |---|---|---|
 *   | 打开「推送通道」 | 打开「通知通道」 | web 版导航叫「通知通道」 |
 *   | 点右下角「发送测试」 | 点「测试此通道」 | web 版没有「发送测试」，按钮名和位置都不同 |
 *   | 点「发送测试」报错 | 点「测试此通道」报错 | 同上 |
 *   | 点左下角「日志」，logs/… | 打开 logs/… | web 版没有日志按钮 |
 *
 * 其余一字未动。留一句「点右下角「发送测试」」的话，用户会去找一个不存在的
 * 按钮，然后判定「这软件坏了」——这比措辞不一致严重得多。
 *
 * `tests/test_guide_copy.py` 会逐段比对：快照里每一段都必须原样出现在本文件里
 * （四处指代除外，测试里显式排除）。拆成 5 节只是换了显示方式，文案一个字没改。
 */
(function () {
  "use strict";

  window.WBN_PAGES = window.WBN_PAGES || {};

  /* `section` 就是全部状态。初值必须是 "index"（总栏）——守卫会查这一行。 */
  var state = { root: null, ctx: null, built: false, section: "index", tabEls: {}, panel: null };

  /* ---- 文案（逐字来自 ui.py:_page_guide） -------------------------------- */

  var INTRO = "四步走完，全程免费，第一次配置大约三分钟。推荐路线是微信测试号：" +
              "微信官方的开发者测试号，扫码即得凭证，不用实名、不用付费、不限条数。";

  var STEPS = [
    {
      title: "选一条通道",
      text: "打开「通知通道」，左边选「微信测试号」。它是微信官方的开发者测试号，" +
            "扫码即得 appID / appsecret，不用实名、不用付费、不限条数。",
      link: "去「通知通道」",
      route: "channels"
    },
    {
      title: "填三个值并保存",
      text: "appID、appsecret、模板 ID。模板内容照抄这四行，每行前面的「标签：」不能省，" +
            "省了那一行就是空白：状态：{{title.DATA}} ／ 项目：{{project.DATA}} ／ " +
            "详情：{{detail.DATA}} ／ 时间：{{time.DATA}}。" +
            "想让详情多占几行，就再加「补充：{{detail2.DATA}}」这样的行，一个变量占一行。" +
            "openid 留空，点「自动获取 openid」程序会自己拉。" +
            "填齐后开关会自动打开——不用手动拨。",
      link: "去「通知通道」填值",
      route: "channels"
    },
    {
      title: "让手机真的弹出来",
      text: "见下面「手机弹窗怎么开」一节。微信本身的通知权限、系统的横幅与锁屏权限，" +
            "两个都开才会在锁屏上看到。",
      link: "去「通知通道」发一条测试",
      route: "channels"
    },
    {
      title: "验证一次",
      text: "点「测试此通道」。微信聊天列表里应该立刻出现一条消息；" +
            "没出现就去「运行状态」页看日志里的具体错误码。",
      link: "去「运行状态」",
      route: "status"
    }
  ];

  var POPUP_DESC = "通道配好只保证「消息发出去了」，能不能弹出来由系统通知权限决定";

  var POPUP = [
    { name: "微信内", items: [
      "我 → 设置 → 新消息通知：打开「接收新消息通知」「通知显示消息详情」",
      "找到「微信测试号」这条会话，确认没有开启「消息免打扰」",
      "长按会话 → 置顶，避免被折叠到列表底部"
    ] },
    { name: "iPhone", items: [
      "设置 → 通知 → 微信：允许通知 + 横幅 + 声音 + 在锁定屏幕上显示",
      "控制中心里确认「专注模式」没有拦掉微信",
      "设置 → 微信 → 后台 App 刷新：打开（关掉会延迟几分钟）"
    ] },
    { name: "安卓", items: [
      "设置 → 应用 → 微信 → 通知管理：允许通知 + 横幅/浮动通知 + 锁屏通知",
      "设置 → 电池 → 应用耗电管理：微信设为「不受限制」",
      "小米 / 华为 / OPPO 还需要在「自启动管理」里放行微信，否则息屏后收不到"
    ] }
  ];

  var DESKTOP_DESC = "本程序本身不弹窗（宿主已有自己的提示）。想让 Windows 也弹，" +
                     "配一个带桌面客户端的通道即可。";

  var DESKTOP = [
    { name: "方案 A · ntfy（最轻）",
      text: "通道选「ntfy」，起一个别人猜不到的 topic 填进去。电脑装 ntfy 桌面客户端" +
            "（Microsoft Store 搜 ntfy，或 GitHub binwiederhier/ntfy 的 Windows 版），" +
            "订阅同一个 topic。之后每条通知都是原生 Windows 通知横幅，还会进操作中心。" +
            "完全免费、无需注册。" },
    { name: "方案 B · 邮件",
      text: "通道选「邮件（SMTP）」，用 QQ 邮箱的授权码当密码。桌面端用 Outlook、" +
            "网易邮箱大师之类的客户端收信，开启它的桌面通知。" +
            "好处是手机上也会同时收到邮件推送。" },
    { name: "方案 C · 企业微信 / 钉钉 / 飞书",
      text: "已经在用这三个之一的话，配对应的自建应用或群机器人。桌面客户端收到消息时" +
            "本来就会弹窗，而且手机上同一个 App 也能收到，等于一箭双雕。" }
  ];

  var CHECKS = [
    { title: "先看「运行状态」页的「今日通知」",
      text: "是 0 → 说明根本没发出去，问题在触发条件或限流（比如最短耗时 20 秒、静默时段）。" },
    { title: "有计数但手机没动静",
      text: "说明已经发出去了，纯粹是通知权限问题，回到上面「手机弹窗怎么开」逐条对。" },
    { title: "点「测试此通道」报错",
      text: "错误信息会直接说明原因：40125 是 appsecret 填错，40037 是模板 ID 不对，" +
            "43004 是没关注测试号，45009 是调用太频繁（等 10 分钟）。" },
    { title: "想看完整请求记录",
      text: "打开 logs/notify-日期.log，里面每条都有 ok=True/False 和原始返回。" }
  ];

  /* 「其他通道怎么配」（W7.0）。原来这一页只有微信测试号四步，其余 9 个通道
     只在「电脑上也要弹窗」里被顺带提了一句——用户配 bark / 钉钉时这一页等于没有。
     每个通道两段：**要填什么**（字段名和通道页一致）+ **去哪拿**（具体到菜单路径）。
     不写「按提示填写」：通道页的 help 已经很长，这一页的价值就是摊开对比。 */
  var OTHERS = [
    { key: "wxwork-app", name: "企业微信 · 自建应用",
      need: "要填：企业 ID（corpid）、应用 Secret、AgentId、收件人（填 @all 就发给可见范围内所有人）。",
      where: "去哪拿：企业微信后台 → 我的企业 → 复制「企业 ID」；应用管理 → 自建 → " +
             "创建应用，详情页里有 AgentId 和 Secret；同一页的「可见范围」把自己加进去，" +
             "否则发不出去。" },
    { key: "wxwork-bot", name: "企业微信 · 群机器人",
      need: "要填：Webhook 地址（一长串 URL，不需要别的）。",
      where: "去哪拿：企业微信群里 → 右上角「…」→ 群机器人 → 添加 → 复制 Webhook 地址。" +
             "没有企业的话可以在 work.weixin.qq.com 免费注册一个自己用。" },
    { key: "dingtalk", name: "钉钉 · 群机器人",
      need: "要填：Webhook 地址；如果建机器人时勾了「加签」，再填加签密钥（secret）。",
      where: "去哪拿：钉钉群 → 群设置 → 智能群助手 → 添加机器人 → 自定义 → " +
             "复制 Webhook；加签密钥在同一步的「安全设置」里，只在勾了加签时才有。" },
    { key: "feishu", name: "飞书 · 群机器人",
      need: "要填：Webhook 地址。",
      where: "去哪拿：飞书群 → 群设置 → 群机器人 → 添加 → 自定义机器人 → 复制 Webhook 地址。" },
    { key: "bark", name: "Bark（iPhone）",
      need: "要填：设备 Key（那串字母数字，不是整条 URL）。",
      where: "去哪拿：App Store 装 Bark，打开 App 首页会显示 " +
             "https://api.day.app/XXXXXXXX，斜杠后面那一段就是 key。" +
             "用自建服务器的话把「服务器」改成自己的地址。" },
    { key: "ntfy", name: "ntfy（跨平台）",
      need: "要填：Topic（自己起一个别人猜不到的名字，越随机越好）。",
      where: "去哪拿：不用注册。手机装 ntfy App 后订阅同一个 topic 名；" +
             "电脑装 ntfy 桌面客户端订阅同名 topic 就能弹 Windows 通知。" },
    { key: "smtp", name: "邮件（SMTP）",
      need: "要填：SMTP 服务器、端口、账号（完整邮箱地址）、密码。" +
            "注意：密码要填授权码，不是登录密码。",
      where: "去哪拿：QQ 邮箱 → 设置 → 账户 → 开启「SMTP 服务」→ 生成授权码；" +
             "服务器填 smtp.qq.com、端口 465、勾上 SSL。收件人留空就发给自己。" },
    { key: "webhook", name: "自定义 Webhook",
      need: "要填：URL；请求体想自己定就写 body_template，可用 {title} {content} " +
            "{kind} {project} 占位。",
      where: "去哪拿：不用拿——这是给你自己的服务用的。默认按 POST JSON 发 " +
             "{\"title\":…,\"content\":…}，对方能收 JSON 就行。" },
    { key: "log", name: "本机落盘",
      need: "不用填任何东西，开关打开就生效。",
      where: "作用：只往 logs/ 写一份记录，不发到任何地方。用来留档、也用来排查" +
             "「到底有没有触发」——它是唯一不会因为网络问题失败的通道。" }
  ];

  /* tab 与面板的 id **写成字面量**，不拼字符串。
   *
   * 守卫（`test_tab_and_panel_ids_are_one_to_one`）要在源码里把这两组 id 抠出来
   * 比对「各 6 个、一一对应」。写成 `"panel-" + key` 的话源码里只剩一个值，
   * 守卫就退化成「看起来对」——W7.4 那次 `href="#` 假绿就是这么来的。 */
  var SECTIONS = [
    { key: "index",   label: "总栏",       tab: "tab-index",   panel: "panel-index" },
    { key: "wxmp",    label: "微信测试号", tab: "tab-wxmp",    panel: "panel-wxmp" },
    { key: "others",  label: "其他通道",   tab: "tab-others",  panel: "panel-others" },
    { key: "phone",   label: "手机弹窗",   tab: "tab-phone",   panel: "panel-phone" },
    { key: "desktop", label: "电脑弹窗",   tab: "tab-desktop", panel: "panel-desktop" },
    { key: "trouble", label: "排查",       tab: "tab-trouble", panel: "panel-trouble" }
  ];

  /* 总栏 = 目录概览，5 张卡片（不是把全部内容平铺）。
   * 每张：标题 + 一句说明 + 右侧元信息 + →。整卡可点，点了进对应那一节。
   * 说明尽量复用本节已有的那句 desc，别为同一件事写两遍话。 */
  var CARDS = [
    { key: "wxmp", title: "四步走通，全程免费", meta: STEPS.length + " 步",
      desc: "扫码即得凭证，不用实名、不用付费、不限条数。" },
    { key: "others", title: "其他通道怎么配", meta: OTHERS.length + " 个通道",
      desc: "不走微信的话看这里。字段名和「通知通道」页上的一致，照着填即可。" },
    { key: "phone", title: "手机弹窗怎么开", meta: "",
      desc: POPUP_DESC },
    { key: "desktop", title: "电脑上也要弹窗", meta: "",
      desc: DESKTOP_DESC },
    { key: "trouble", title: "收不到？按这个顺序查", meta: "",
      desc: "一条都没发出去、发出去了没弹、测试报错——三种情况各查一处。" }
  ];

  /* ---- 渲染 -------------------------------------------------------------- */

  function specOf(key) {
    for (var i = 0; i < SECTIONS.length; i++) {
      if (SECTIONS[i].key === key) return SECTIONS[i];
    }
    return SECTIONS[0];
  }

  /** 一个分组：h2 + 可选一句说明 + 正文容器。每节只有**一个** .group。 */
  function group(panel, title, desc) {
    var ctx = state.ctx;
    var g = ctx.el("section", "group");
    g.appendChild(ctx.el("h2", null, title));
    if (desc) g.appendChild(ctx.el("p", "desc", desc));
    var body = ctx.el("div", "body");
    g.appendChild(body);
    panel.appendChild(g);
    return body;
  }

  /** 滚到某个锚点。不动焦点——把焦点搬到目标段落会让键盘用户丢失位置。 */
  function jump(id) {
    var target = document.getElementById(id);
    if (target) target.scrollIntoView({ block: "start" });
  }

  /** 二级索引的一枚 chip（页内滚动，不切视图）。文字就是去处，不再加 aria-label。 */
  function chip(label, target, extra) {
    var b = state.ctx.el("button", "guide-chip" + (extra ? " " + extra : ""), label);
    b.type = "button";
    b.setAttribute("data-goto", target);
    b.addEventListener("click", function () { jump(target); });
    return b;
  }

  /** 切到某一节。`moveFocus` 只在键盘导航和卡片点击时为 true。 */
  function select(key, moveFocus) {
    state.section = key;
    paint();
    if (moveFocus) {
      var t = state.tabEls[key];
      /* 焦点留在 tab 上，**不搬进面板**：搬过去会打断键盘用户的位置感。 */
      if (t) t.focus();
    }
  }

  function backButton() {
    var b = state.ctx.el("button", "guide-back", "← 总栏");
    b.type = "button";
    b.addEventListener("click", function () { select("index", true); });
    return b;
  }

  function card(c) {
    var ctx = state.ctx;
    var b = ctx.el("button", "guide-card");
    b.type = "button";
    b.addEventListener("click", function () { select(c.key, true); });

    var main = ctx.el("span", "guide-card-main");
    main.appendChild(ctx.el("span", "guide-card-title", c.title));
    main.appendChild(ctx.el("span", "guide-card-desc", c.desc));
    b.appendChild(main);

    var side = ctx.el("span", "guide-card-side");
    if (c.meta) side.appendChild(ctx.el("span", "guide-card-meta", c.meta));
    var arrow = ctx.el("span", "guide-card-arrow", "→");
    arrow.setAttribute("aria-hidden", "true");
    side.appendChild(arrow);
    b.appendChild(side);
    return b;
  }

  /* ---- 五个单项 ---------------------------------------------------------- */

  function renderWxmp(panel) {
    var ctx = state.ctx;
    var body = group(panel, "四步走通", "");
    STEPS.forEach(function (s, i) {
      var row = ctx.el("div", "step");

      var num = ctx.el("div", "step-num", String(i + 1));
      num.setAttribute("aria-hidden", "true");
      row.appendChild(num);

      var box = ctx.el("div", "step-body");
      var h = ctx.el("h3", "step-title", s.title);
      /* 序号在视觉上是圆点，读屏读不到——所以标题本身带「第 N 步」 */
      h.setAttribute("aria-label", "第 " + (i + 1) + " 步：" + s.title);
      box.appendChild(h);
      box.appendChild(ctx.el("p", "step-text", s.text));

      var link = ctx.el("button", "step-link", s.link);
      link.type = "button";
      link.addEventListener("click", function () { ctx.go(s.route); });
      box.appendChild(link);

      row.appendChild(box);
      body.appendChild(row);
    });
  }

  function renderOthers(panel) {
    var ctx = state.ctx;
    var body = group(panel, "其他通道怎么配",
      "不走微信的话看这里。字段名和「通知通道」页上的一致，照着填即可。");

    /* 二级索引：9 张卡片各在哪儿。文字直接取 `OTHERS[].name`，
       和下面的卡片标题是**同一个字段**——两处分别写一遍迟早对不上。
       它是**页内滚动**，不切视图，所以样式弱于主 tab。 */
    var index = ctx.el("div", "guide-index");
    OTHERS.forEach(function (o) {
      index.appendChild(chip(o.name, "guide-ch-" + o.key, "is-sub"));
    });
    body.appendChild(index);

    OTHERS.forEach(function (o) {
      var head = ctx.el("h3", "sub-head", o.name);
      head.id = "guide-ch-" + o.key;
      body.appendChild(head);
      body.appendChild(ctx.el("p", "step-text", o.need));
      body.appendChild(ctx.el("p", "step-text", o.where));
    });
  }

  function renderPhone(panel) {
    var ctx = state.ctx;
    var body = group(panel, "手机弹窗怎么开", POPUP_DESC);
    POPUP.forEach(function (block) {
      body.appendChild(ctx.el("h3", "sub-head", block.name));
      var ul = ctx.el("ul", "bullets");
      block.items.forEach(function (t) { ul.appendChild(ctx.el("li", null, t)); });
      body.appendChild(ul);
    });
  }

  function renderDesktop(panel) {
    var ctx = state.ctx;
    var body = group(panel, "电脑上也要弹窗", DESKTOP_DESC);
    DESKTOP.forEach(function (p) {
      body.appendChild(ctx.el("h3", "sub-head", p.name));
      body.appendChild(ctx.el("p", "step-text", p.text));
    });
  }

  function renderTrouble(panel) {
    var ctx = state.ctx;
    var body = group(panel, "收不到？按这个顺序查", "");
    CHECKS.forEach(function (c, i) {
      var row = ctx.el("div", "step");

      var num = ctx.el("div", "step-num", String(i + 1));
      num.setAttribute("aria-hidden", "true");
      row.appendChild(num);

      var box = ctx.el("div", "step-body");
      box.appendChild(ctx.el("h3", "step-title", c.title));
      box.appendChild(ctx.el("p", "step-text", c.text));

      row.appendChild(box);
      body.appendChild(row);
    });
  }

  function renderIndex(panel) {
    var ctx = state.ctx;
    panel.appendChild(ctx.el("p", "page-sub", INTRO));
    var list = ctx.el("div", "guide-cards");
    CARDS.forEach(function (c) { list.appendChild(card(c)); });
    panel.appendChild(list);
  }

  /* ---- 状态 → 界面 ------------------------------------------------------- */

  /* tab 条**只建一次**，切换时只重画面板。这样焦点天然留在 tab 上，
     不用「先 render 再 focus」那种容易抖的补偿写法。 */
  function buildTabs() {
    var ctx = state.ctx;
    var bar = ctx.el("div", "guide-tabs");
    bar.setAttribute("role", "tablist");
    bar.setAttribute("aria-label", "指南分节");
    state.tabEls = {};
    SECTIONS.forEach(function (item, i) {
      var t = ctx.el("button", "guide-tab", item.label);
      t.type = "button";
      t.id = item.tab;
      t.setAttribute("role", "tab");
      t.setAttribute("aria-controls", item.panel);
      t.addEventListener("click", function () { select(item.key); });
      t.addEventListener("keydown", function (e) { onTabKey(e, i); });
      state.tabEls[item.key] = t;
      bar.appendChild(t);
    });
    return bar;
  }

  /** APG：←/→ 切相邻、Home/End 到首尾。别的键一律不拦（Tab 要能出去）。 */
  function onTabKey(e, i) {
    var n = SECTIONS.length;
    var next = -1;
    if (e.key === "ArrowRight") next = (i + 1) % n;
    else if (e.key === "ArrowLeft") next = (i - 1 + n) % n;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = n - 1;
    if (next < 0) return;
    e.preventDefault();
    select(SECTIONS[next].key, true);
  }

  function paint() {
    var spec = specOf(state.section);

    SECTIONS.forEach(function (item) {
      var t = state.tabEls[item.key];
      var on = item.key === state.section;
      t.setAttribute("aria-selected", on ? "true" : "false");
      /* roving tabindex：只有选中的那项能被 Tab 停住，其余 -1 */
      t.tabIndex = on ? 0 : -1;
      if (on) t.classList.add("is-active");
      else t.classList.remove("is-active");
    });

    var panel = state.panel;
    panel.id = spec.panel;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", spec.tab);
    panel.setAttribute("tabindex", "0");
    panel.textContent = "";

    if (state.section === "index") {
      renderIndex(panel);
      return;
    }
    panel.appendChild(backButton());
    if (state.section === "wxmp") renderWxmp(panel);
    else if (state.section === "others") renderOthers(panel);
    else if (state.section === "phone") renderPhone(panel);
    else if (state.section === "desktop") renderDesktop(panel);
    else renderTrouble(panel);
  }

  function build() {
    var ctx = state.ctx;
    state.root.textContent = "";
    state.root.appendChild(buildTabs());
    state.panel = ctx.el("section", "guide-panel");
    state.root.appendChild(state.panel);
    paint();
    state.built = true;
  }

  /* ---- 对外接口 ----------------------------------------------------------
     这一页不读任何配置，所以**没有空态、也没有错误态**：不会因为读不到数据
     而空白（W4 审计 C6）。 */

  window.WBN_PAGES.guide = {
    mount: function (root, ctx) {
      state.root = root;
      state.ctx = ctx;
      build();
    },
    show: function () {
      if (!state.built) { build(); return; }
      /* 回到总栏：指南是查阅型页面，从别处切回来时「上次看到哪」比「从头开始」
         更让人意外。只在离开过某节时才重画，停在总栏就什么都不做。 */
      if (state.section !== "index") {
        state.section = "index";
        paint();
      }
    }
  };
})();
