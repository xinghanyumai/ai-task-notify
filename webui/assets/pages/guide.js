/* 「使用指南」页（W4）—— 长文，不是表单。
 *
 * 判据：**从上往下读得下去，不需要中间停下来操作**（任务书 §3.1）。
 * 所以没有卡片、没有折叠、没有「第一步/第二步」的按钮，只有
 * 序号圆点 + 标题 + 正文的纵向流。
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
 * `tests/test_guide_copy.py` 会逐段比对：`ui.py` 里 `_page_guide` 的每个
 * 字符串都必须原样出现在本文件里（四处指代除外，测试里显式排除）。
 */
(function () {
  "use strict";

  window.WBN_PAGES = window.WBN_PAGES || {};

  var state = { root: null, ctx: null, built: false };

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
    { name: "企业微信 · 自建应用",
      need: "要填：企业 ID（corpid）、应用 Secret、AgentId、收件人（填 @all 就发给可见范围内所有人）。",
      where: "去哪拿：企业微信后台 → 我的企业 → 复制「企业 ID」；应用管理 → 自建 → " +
             "创建应用，详情页里有 AgentId 和 Secret；同一页的「可见范围」把自己加进去，" +
             "否则发不出去。" },
    { name: "企业微信 · 群机器人",
      need: "要填：Webhook 地址（一长串 URL，不需要别的）。",
      where: "去哪拿：企业微信群里 → 右上角「…」→ 群机器人 → 添加 → 复制 Webhook 地址。" +
             "没有企业的话可以在 work.weixin.qq.com 免费注册一个自己用。" },
    { name: "钉钉 · 群机器人",
      need: "要填：Webhook 地址；如果建机器人时勾了「加签」，再填加签密钥（secret）。",
      where: "去哪拿：钉钉群 → 群设置 → 智能群助手 → 添加机器人 → 自定义 → " +
             "复制 Webhook；加签密钥在同一步的「安全设置」里，只在勾了加签时才有。" },
    { name: "飞书 · 群机器人",
      need: "要填：Webhook 地址。",
      where: "去哪拿：飞书群 → 群设置 → 群机器人 → 添加 → 自定义机器人 → 复制 Webhook 地址。" },
    { name: "Bark（iPhone）",
      need: "要填：设备 Key（那串字母数字，不是整条 URL）。",
      where: "去哪拿：App Store 装 Bark，打开 App 首页会显示 " +
             "https://api.day.app/XXXXXXXX，斜杠后面那一段就是 key。" +
             "用自建服务器的话把「服务器」改成自己的地址。" },
    { name: "ntfy（跨平台）",
      need: "要填：Topic（自己起一个别人猜不到的名字，越随机越好）。",
      where: "去哪拿：不用注册。手机装 ntfy App 后订阅同一个 topic 名；" +
             "电脑装 ntfy 桌面客户端订阅同名 topic 就能弹 Windows 通知。" },
    { name: "邮件（SMTP）",
      need: "要填：SMTP 服务器、端口、账号（完整邮箱地址）、密码。" +
            "**密码要填授权码，不是登录密码。**",
      where: "去哪拿：QQ 邮箱 → 设置 → 账户 → 开启「SMTP 服务」→ 生成授权码；" +
             "服务器填 smtp.qq.com、端口 465、勾上 SSL。收件人留空就发给自己。" },
    { name: "自定义 Webhook",
      need: "要填：URL；请求体想自己定就写 body_template，可用 {title} {content} " +
            "{kind} {project} 占位。",
      where: "去哪拿：不用拿——这是给你自己的服务用的。默认按 POST JSON 发 " +
             "{\"title\":…,\"content\":…}，对方能收 JSON 就行。" },
    { name: "本机落盘",
      need: "不用填任何东西，开关打开就生效。",
      where: "作用：只往 logs/ 写一份记录，不发到任何地方。用来留档、也用来排查" +
             "「到底有没有触发」——它是唯一不会因为网络问题失败的通道。" }
  ];

  /* ---- 渲染 -------------------------------------------------------------- */

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

    /* 开场：一段承诺。它不是标题（顶栏已经有了），是正文的第一段。 */
    state.root.appendChild(ctx.el("p", "page-sub", INTRO));

    /* ---- 四步 ---- */
    var g1 = group("四步走通", "");
    STEPS.forEach(function (s, i) {
      var row = ctx.el("div", "step");

      var num = ctx.el("div", "step-num", String(i + 1));
      num.setAttribute("aria-hidden", "true");
      row.appendChild(num);

      var body = ctx.el("div", "step-body");
      var h = ctx.el("h3", "step-title", s.title);
      /* 序号在视觉上是圆点，读屏读不到——所以标题本身带「第 N 步」 */
      h.setAttribute("aria-label", "第 " + (i + 1) + " 步：" + s.title);
      body.appendChild(h);
      body.appendChild(ctx.el("p", "step-text", s.text));

      var link = ctx.el("button", "step-link", s.link);
      link.type = "button";
      link.addEventListener("click", function () { ctx.go(s.route); });
      body.appendChild(link);

      row.appendChild(body);
      g1.body.appendChild(row);
    });
    state.root.appendChild(g1.root);

    /* ---- 其他通道 ---- */
    var gOther = group("其他通道怎么配",
      "不走微信的话看这里。字段名和「通知通道」页上的一致，照着填即可。");
    OTHERS.forEach(function (o) {
      gOther.body.appendChild(ctx.el("h3", "sub-head", o.name));
      gOther.body.appendChild(ctx.el("p", "step-text", o.need));
      gOther.body.appendChild(ctx.el("p", "step-text", o.where));
    });
    state.root.appendChild(gOther.root);

    /* ---- 手机弹窗 ---- */
    var g2 = group("手机弹窗怎么开", POPUP_DESC);
    POPUP.forEach(function (block) {
      g2.body.appendChild(ctx.el("h3", "sub-head", block.name));
      var ul = ctx.el("ul", "bullets");
      block.items.forEach(function (t) { ul.appendChild(ctx.el("li", null, t)); });
      g2.body.appendChild(ul);
    });
    state.root.appendChild(g2.root);

    /* ---- 电脑弹窗 ---- */
    var g3 = group("电脑上也要弹窗", DESKTOP_DESC);
    DESKTOP.forEach(function (p) {
      g3.body.appendChild(ctx.el("h3", "sub-head", p.name));
      g3.body.appendChild(ctx.el("p", "step-text", p.text));
    });
    state.root.appendChild(g3.root);

    /* ---- 排查 ---- */
    var g4 = group("收不到？按这个顺序查", "");
    /* 最后一个分组不用额外加留白：`.group` 的 margin-bottom 就是 --s6，
       再往下是滚动容器的内边距，和 tkinter 版一样。 */
    CHECKS.forEach(function (c, i) {
      var row = ctx.el("div", "step");

      var num = ctx.el("div", "step-num", String(i + 1));
      num.setAttribute("aria-hidden", "true");
      row.appendChild(num);

      var body = ctx.el("div", "step-body");
      body.appendChild(ctx.el("h3", "step-title", c.title));
      body.appendChild(ctx.el("p", "step-text", c.text));

      row.appendChild(body);
      g4.body.appendChild(row);
    });
    state.root.appendChild(g4.root);

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
      if (!state.built) build();
    }
  };
})();
