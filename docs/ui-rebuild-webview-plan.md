# WorkBuddy 通知 · UI 重构方案
## 从 tkinter 迁到 pywebview + Web 前端

> **📌 本文已被审查，结论与修正见 [`prompts/README.md`](prompts/README.md) 第 2 节。**
>
> 技术方向（pywebview + 系统 WebView2、不引框架、抄 shadcn token、分阶段）**全部采纳**。
> 但有 8 处必须修正，其中一处是硬伤：
>
> 1. **「业务代码一行不改」不成立** —— `ui.py` 里有 31 个零 UI 引用的方法、约 309 行真业务
>    （`_stats` 60 行日志统计、`_chain_sentence` 40 行、`_collect_into` 35 行、测试通道 54 行…）。
>    不搬，bridge 就是空壳。→ 新增 W0 先抽取。
> 2. **Phase 0 就 `frameless=True` 与 1.2 节自相矛盾** —— resize / 贴边 / Aero Snap 全要自己写。
>    → 先用原生边框，W5 单独做实验。
> 3. **Tailwind CDN 不可接受** —— 断网即白屏。→ 必须本地文件。
> 4. 没提单实例（现在点两次托盘会开出两个窗口，互相覆盖配置）。
> 5. 没提跨进程配置竞争（`config_save` 必须读-改-写，不能整体覆盖）。
> 6. `file://` 中文路径要 `Path.as_uri()`，不是手拼。
> 7. WebView2 缺失应降级，不是让用户去装东西。
> 8. 删工期估算，改成每阶段的验收条件。
>
> 另外本文 1.3 节列的 9 条「土」里，**第 5 条（背景死白）和第 7 条（品牌色占比太高）已经过时**，
> 作者看的是 `redesign-base` 之前的截图。其余 7 条成立。
>
> **执行请走 [`prompts/README.md`](prompts/README.md) 的 W0 → W6，不要直接照本文第 5 节的 Phase 走。**

> 版本：v1.0 · 2026-10-27
> 目标：在**不动任何业务逻辑**的前提下，把现在这套"tkinter 手搓 UI"换成真正的大厂网页质感。

---

## 0. 一句话结论

业务代码（hook / channels / pipeline / config / store）一行不改；把 `ui.py` + `wbn/widgets.py` 那 ~21 万字符的 tkinter 皮扒掉，换成 **pywebview 壳 + HTML/Tailwind 前端**，4~5 天能出第一版能用的东西。

---

## 1. 为什么现在长得丑：tkinter 的天花板

### 1.1 tkinter 是什么

- Python 1991 年出生时就捆绑的标准 GUI 库，底层是更老的 Tcl/Tk（1991 年初）。
- 它**自己不画像素**，直接调操作系统的原生控件。Windows 上那个灰按钮、直角输入框，是 Windows 画的，不是你程序画的。
- 它存在的唯一理由：Python 装完就能用，不用 pip install。除此之外没有优势。

### 1.2 它做不到什么（这才是丑的根因）

| 大厂 UI 标配能力 | tkinter 能不能做 |
|---|---|
| 抗锯齿圆角（8px / 12px / 16px） | ❌ 原生直角，要自绘 Canvas，性能差 |
| 卡片浮起阴影（多层 drop-shadow） | ❌ 不支持 |
| 背景毛玻璃 / backdrop-blur | ❌ 不支持 |
| focus 时光标周围的光环（ring） | ❌ 没有 |
| 自定义窗口边框（自绘最小化/关闭） | ⚠️ 能做但极痛苦，拖拽、缩放、贴边全要自己写 |
| hover / transition 动效（150ms ease-out） | ⚠️ 能勉强做，但帧率低、掉帧 |
| 字体字距微调（letter-spacing） | ❌ 不支持 |
| 真正的 CSS 级样式表 | ❌ 只有 `option_add` 那点东西 |
| 响应式布局（窗口任意拉伸都不乱） | ⚠️ 要手动绑 `<Configure>`，写起来痛苦 |

**结论**：再堆 10 万行 `widgets.py` 去手搓，天花板也就是"2015 年的 Electron"。Linear / Notion / Raycast / Arc 那种质感，tkinter 物理上画不出来。

### 1.3 截图里一眼就土的地方（对照现状）

1. 窗口右上角是 Windows 原生的最小化/最大化/关闭——一个细节就暴露"这是个 Python 脚本"。
2. 输入框是直角 1px 灰框，focus 时没有光环。
3. 按钮是灰边矩形，hover 无过渡。
4. 统计数字（19 / 19 / 19 / 2|10）直接裸排在白背景上，没有卡片、没有阴影。
5. 浅色模式背景是死白 `#ffffff`，大厂都用 `#fafafa`。
6. 字体是默认雅黑，字距紧、笔画糊。
7. 品牌青绿在 logo / 开关 / badge / 状态点到处都是，占比太高。
8. 侧栏挤在左 1/5，主内容居中，两侧空一大块，留白节奏乱。
9. 图标风格不统一（实心方底 vs 线性描边混着用）。

---

## 2. 技术选型

### 2.1 候选方案对比

| 方案 | 质感上限 | 体积 | 学习曲线 | 对现有业务代码的侵入 | 结论 |
|---|---|---|---|---|---|
| **pywebview + HTML/Tailwind** | 真·大厂网页感 | 30MB 内（复用系统 WebView2） | 低（前端你已经会） | **零侵入** | ✅ 选这个 |
| Electron | 真·大厂网页感 | 150MB+ | 中 | 零侵入 | ❌ 太重，一个通知工具不配 |
| Tauri (Rust) | 最好 | 10MB | 高（要写 Rust） | 要起 sidecar 包 Python | ❌ 现阶段不划算 |
| PySide6 + QSS | 精致原生桌面，到不了网页丝滑 | 50MB | 中 | 要重绑信号槽 | ❌ QSS 不是 CSS |
| customtkinter / ttkbootstrap | 比现状好一点 | 不变 | 低 | 继续在 tkinter 泥潭里 | ❌ 天花板还是低 |

### 2.2 为什么 pywebview

- Windows 10 1803 以上**自带 WebView2**，不用打包 Chromium。
- Python 后端一行不改，前端就是普通网页。
- 有原生的 `window.pywebview.api.*` 双向桥，不用起 HTTP 端口，不用处理 CORS。
- 开发时可以直接 `webview.start(debug=True)` 开 Chrome DevTools，元素审查、改样式即时生效。
- 现在的"目录 + 双击 pythonw 运行"模式继续保留，不强制打包成单 exe。

---

## 3. 新架构

### 3.1 目录结构（重构后）

```
D:\Code\Work buddy 通知\
├── hook.py                  ← 不动
├── dispatch.py              ← 不动
├── tray.py                  ← 改 1 行（open_ui 指向新入口）
├── install.py               ← 不动
├── selftest.py              ← 不动
├── app.py                   ← 新入口（~30 行）：起 pywebview 窗口
│
├── wbn\
│   ├── channels.py          ← 不动（56KB，10 个推送通道）
│   ├── pipeline.py          ← 不动
│   ├── events.py            ← 不动
│   ├── filters.py           ← 不动
│   ├── render.py            ← 不动
│   ├── store.py             ← 不动
│   ├── config.py            ← 不动
│   ├── sanitize.py          ← 不动
│   └── bridge.py            ← 新增：暴露给前端的 API 类
│
├── webui\                   ← 全新前端
│   ├── index.html           ← 壳（侧栏 + 内容区 + 顶栏）
│   ├── assets\
│   │   ├── app.css          ← Tailwind 配置 + shadcn token
│   │   ├── app.js           ← 路由 + bridge 调用 + 页面渲染
│   │   └── fonts\           ← Inter woff2
│   └── pages\
│       ├── status.html      ← 运行状态
│       ├── channels.html    ← 推送通道
│       ├── triggers.html    ← 触发条件
│       ├── templates.html   ← 消息模板
│       └── guide.html       ← 使用教程
│
├── assets\                  ← 图标保留
├── logs\  state\            ← 不动
└── docs\                    ← 不动

# 删除：
#   ui.py
#   wbn/widgets.py
#   wbn/theme.py
#   wbn/tokens.py
#   wbn/anim.py
#   wbn/splash.py
```

### 3.2 进程模型（和现在等价）

- **hook.py** 还是被 WorkBuddy 调，stdin 管道投递事件 → dispatch.py → pipeline.process()，和 UI 完全无关。
- **tray.py** 还是 pystray 托盘，点"打开设置"时 `subprocess.Popen([pythonw, app.py])`。
- **app.py** 是一个独立进程：起 WebView2 窗口，加载 `webui/index.html`。关闭窗口即退出。
- UI 和通知链路**不共享进程**，UI 崩了不影响通知发送——这和现在是一样的。

### 3.3 bridge.py 暴露给前端的 API

前端调用 `window.pywebview.api.xxx(...)`，Python 这边就一个类：

```python
class Bridge:
    def config_get(self) -> dict: ...
    def config_save(self, cfg: dict) -> dict: ...        # 返回 {ok, error?}
    def channels_list(self) -> list[dict]: ...           # 所有通道+状态
    def channel_test(self, channel_type: str, fields: dict) -> dict: ...
        # ⚠️ 必须后台线程跑，完成后 window.evaluate_js("on_test_done(...)")
    def stats_today(self) -> dict: ...                  # {today, success, fail, enabled}
    def recent_logs(self, limit: int = 20) -> list[dict]: ...
    def open_logs_dir(self): ...
    def open_config_dir(self): ...
    def get_theme(self) -> str: ...                      # "light" | "dark" | "system"
    def set_enabled(self, on: bool): ...
    def app_quit(self): ...
```

桥接约定：
- 所有方法都 try/except 包一层，返回 `{ok: bool, data?: ..., error?: str}`，不让异常炸到前端。
- 阻塞操作（channel_test、HTTP 请求）一律 `threading.Thread` 跑，结果用 `window.evaluate_js` 回调。
- 启动时把 `bridge` 实例传给 webview：`webview.start(debug=True, http_server=True)`，JS 里 `window.pywebview.api.config_get().then(...)`。

---

## 4. 设计系统（直接抄 shadcn/ui，别自己发明）

### 4.1 色板

**浅色模式**（CSS 变量）：
```
--background: #fafafa;     ← 不是纯白！
--foreground: #18181b;
--card:       #ffffff;
--muted:      #f4f4f5;
--muted-fg:   #71717a;
--border:     #e4e4e7;
--primary:    #10b981;     ← 就是你现在那个青绿，保留
--primary-fg: #ffffff;
--danger:     #ef4444;
--ring:       rgba(16,185,129,.35);   ← focus 光环
```

**深色模式**：
```
--background: #09090b;
--foreground: #fafafa;
--card:       #111113;
--muted:      #1c1c1f;
--muted-fg:   #a1a1aa;
--border:     #27272a;
--primary:    #10b981;
--ring:       rgba(16,185,129,.4);
```

### 4.2 字体

- 打包 Inter（`assets/fonts/Inter.var.woff2`），中文回退 `"Microsoft YaHei UI", "PingFang SC"`。
- 标题字重 600，正文 400，数字用 `font-variant-numeric: tabular-nums`。
- 全局 `letter-spacing: -0.01em`。

### 4.3 圆角 / 阴影 / 间距

```
圆角：输入框/按钮 8px；卡片 12px；弹窗 16px；badge 9999px（胶囊）
阴影（卡片浮起）：
  0 1px 2px rgb(0 0 0 / 0.04),
  0 4px 12px rgb(0 0 0 / 0.06)
间距：全部 8 的倍数（8 / 16 / 24 / 32 / 48），禁止出现 13px、17px 这种魔数
```

### 4.4 动效

```
hover:      150ms ease-out  (背景色 / 透明度)
页面切换:   200ms ease      (淡入 + 5px 上移)
数字滚动:   300ms ease-out  (统计数字从 0 滚到目标值)
开关切换:   200ms cubic-bezier(.2,.8,.2,1)
```

### 4.5 必须有的组件（别漏）

- Sidebar：当前项左 3px 主色竖条 + 浅灰底，hover 微亮。
- Card：白底、12px 圆角、上面那个浮起阴影。
- Input：8px 圆角、focus 时主色 1.5px 描边 + 1px 外发光（`--ring`）。
- Button：主按钮实心 `--primary`、次按钮幽灵（灰边白底）、危险按钮红。
- Switch：200ms 滑动动画。
- Badge：胶囊形，"已启用" 绿底浅字、"待配置" 灰底浅字。
- Empty state：最近通知空的时候要有一句人话"还没收到通知，发条测试看看"，不要空白。

---

## 5. 迁移阶段（每一步都能跑、能截图、能验收）

> 原则：**不要一口气重写完**。每个 Phase 结束都要有一个能打开的窗口、能给用户看一眼。

### Phase 0 · 打个最小壳（0.5 天）
- 新建 `app.py`，pip install pywebview。
- 开一个 1200×800 窗口，无边框（`frameless=True`），加载 `webui/index.html` 写死一句 hello。
- 验证：能开窗、能拖、能关、能看到 hello。
- 验收：截图给我看。

### Phase 1 · 打通 Python ↔ JS 桥（0.5 天）
- 写 `wbn/bridge.py`，先实现 `config_get()` 和 `config_save(cfg)`。
- 前端读出来显示成 `<pre>` 一段 JSON，加一个输入框改任意字段点保存。
- 打开 `config.json` 确认真的写进去了。
- 验收：改一个字段，重启窗口还在。

### Phase 2 · 壳布局（1 天）
- 左侧 Sidebar：推送通道 / 触发条件 / 消息模板 / 运行状态 / 使用教程。
- 顶部自绘窗口控制条（最小化 / 最大化 / 关闭），右上角放"跟随/浅色/深色"切换 + 总开关。
- 内容区用占位文字，点击 Sidebar 切换内容区文字。
- 验收：5 个导航都能切，深色切换即时生效。

### Phase 3 · 先做"运行状态"页（1 天）
- 这页最容易出彩，先把设计语言在这页定死。
- 4 张统计卡（今日通知 / 成功 / 失败 / 启用通道），数字滚动动画。
- 通道状态列表 + 最近通知流。
- **这页定稿后，后面所有页照着它的色板/间距/字体抄。**

### Phase 4 · 推送通道页（1.5 天，最复杂）
- 左：通道列表（10 个），分组"自建推送 / 第三方 / 本机"。
- 右：选中通道的配置表单。
- 每个字段：label + input + 说明文字 + 必填星号。
- "测试此通道"按钮 → bridge.channel_test → 后台线程 → 回调前端显示结果。

### Phase 5 · 剩下三页（1 天）
- 触发条件：checkbox + 阈值数字输入。
- 消息模板：textarea + 变量占位提示。
- 使用教程：富文本排版，四步走通那套。

### Phase 6 · 收尾（0.5 天）
- `tray.py` 里 `open_ui()` 把 `ui.py` 改成 `app.py`。
- 空状态 / 错误态 / loading 态补齐。
- 对照现状截图逐项过一遍，老功能一个不能少。
- 删除 `ui.py` / `wbn/widgets.py` / `wbn/theme.py` / `wbn/tokens.py` / `wbn/anim.py` / `wbn/splash.py`。

**总工期：约 5 个工作日。**

---

## 6. 必踩的坑（提前写出来，别再摔一次）

1. **channel_test 是阻塞调用**（HTTP 超时 8 秒），bridge 里必须开线程，结果用 `window.evaluate_js(...)` 回调，不然整个窗口冻死。
2. **WebView2 缺失检测**：启动时 `webview.guess_env()`，找不到就弹个提示框带链接（微软 Evergreen Bootstrapper，2MB）。Win10 1803+ / Win11 自带。
3. **中文路径**：`D:\Code\Work buddy 通知` 这种带空格和中文的路径，pywebview 加载 `file://` 时 URL 必须 `urllib.parse.quote`，不然白屏。或者直接 `http_server=True` 起本地 HTTP 服务，绕开这个问题。
4. **自定义窗口拖拽**：pywebview 用 CSS 属性 `-webkit-app-region: drag` 在顶栏上，按钮上要 `no-drag`。
5. **不要引 React/Vue**：5 个页面，原生 JS + Alpine.js 足够，加框架只会让打包变重。
6. **配置 schema 要兼容老 config.json**：`config_save` 不要覆盖整个文件，要读出来 merge，不然老用户的 10 个通道配置全丢。

---

## 7. 验收清单（重构完成后逐项打勾）

- [ ] 窗口是无边框自绘，右上角三个按钮是自己画的。
- [ ] 浅色模式背景是 `#fafafa` 不是纯白。
- [ ] 输入框 focus 时能看到主色光环。
- [ ] hover 按钮有 150ms 过渡。
- [ ] 深色模式切换即时生效，不需要重启。
- [ ] 老的 `config.json` 打开还在，通道配置没丢。
- [ ] 点"发送测试"按钮，8 秒内不卡死，结果异步显示。
- [ ] 通知链路过一遍：WorkBuddy 跑个任务 → 手机微信收到（这条和 UI 无关，但要回归测）。
- [ ] 托盘点"打开设置"能弹出来新窗口。
- [ ] 窗口拖到 800×600 小尺寸，布局不崩。

---

## 8. 投喂给 AI（Cursor / Trae / WorkBuddy）的提示词

> 复制下面整段，粘到新会话开头。已经按"身份 / 约束 / 分支 / 三重校验 / 验收"组织好。

---

```
# 角色
你是一名资深桌面应用架构师 + 前端设计系统工程师，做过 Electron 和 pywebview 应用，
熟悉 shadcn/ui 设计语言。现在要把一个 Python 通知工具的 UI 从 tkinter 重构为 pywebview。

# 项目位置
D:\Code\Work buddy 通知
当前是一个"目录 + 双击 pythonw 运行"的便携工具，不是可 pip 安装的包。

# 重构铁律（违反任何一条都算失败）
1. 业务层一行不改：hook.py / dispatch.py / tray.py / install.py / selftest.py
   / wbn/channels.py / wbn/pipeline.py / wbn/events.py / wbn/filters.py
   / wbn/render.py / wbn/store.py / wbn/config.py / wbn/sanitize.py 全部保留。
2. 旧的 config.json 格式不能变，config_save 必须读出来 merge，不能整体覆盖。
3. 通知链路回归：改完后，hook 被 WorkBuddy 触发到手机微信收到消息这条链路不能断。
4. 不要引入 React/Vue/打包工具链。前端用原生 HTML + Tailwind（CDN 或本地文件）
   + 一点 Alpine.js。
5. 设计系统严格按 docs/ui-rebuild-webview-plan.md 第 4 节的 token 抄：
   背景 #fafafa、主色 #10b981、圆角 8/12/16、阴影两层、Inter 字体。

# Git 分支规范
- 不要直接在 main/master 上改。
- 新建分支：feat/pywebview-ui
- 每个 Phase 一个 commit，commit message 用中文：
  "Phase0: 最小 pywebview 窗口"
  "Phase1: 打通 config bridge"
  ...
- 不要做 --amend，不要 push --force。

# 分阶段执行（不要一口气写完）
严格按 docs/ui-rebuild-webview-plan.md 第 5 节的 Phase 0 → Phase 6 顺序。
每完成一个 Phase，停一下，告诉我：
  1) 改了哪些文件
  2) 怎么运行验证（具体命令）
  3) 截图路径
等我确认后再进下一个 Phase。

# 三重校验机制（每个 Phase 结束前自己跑一遍）
- 独立作答：先写代码，不要参考旧 tkinter 实现的"巧思"。
- 复盘校验：对着本文件第 7 节验收清单逐项打勾。
- 反向验证：故意找一个会让 UI 崩的边界（空配置、网络断开、超长字段），
  确认不会白屏、不会卡死进程。

# 输出要求
- 纯文本，不要用软回车，不要 markdown 装饰过度。
- 代码注释用中文，但只在关键逻辑处加，不要每行都注。
- 遇到和本方案冲突的现实情况（比如 pywebview 某个 API 改名了），
  先说明冲突点，再给替代方案，不要静默改设计。

# 现在
先读这几个文件摸清现状，再开始 Phase 0：
  - docs/ui-rebuild-webview-plan.md（本方案）
  - hook.py / dispatch.py / wbn/pipeline.py（业务，只读不改）
  - tray.py（只改 open_ui 那一行）
  - config.json（看现在配置长什么样）
读完之后先给我一份"Phase 0 要新建/修改的文件清单"，我点头了你再动手。
```

---

## 9. 我（用户）需要先确认的事

在让 AI 动手之前，你自己拍板：

1. **要不要保留旧 tkinter UI 作为 fallback？** 我的建议是不保留，分支上直接删，干净。如果怕翻车，就保留 `ui_legacy.py` 一个版本，旧入口注释掉。
2. **是否打包成单 exe？** 现在是目录运行，继续这样最简单。要做单 exe 就加 PyInstaller 配置，那是 Phase 6 之后的事。
3. **深色模式默认跟随系统还是浅色？** 建议跟随系统。
