# 架构

> ⚠️ **技术选型已变更（2026-10-27）。** 本文的**目录结构**与**核心链路**描述仍然有效；
> 但「为什么用 Python 加 Tkinter」一节已作废，见 ADR-007，UI 层改用 pywebview + 系统 WebView2，
> 见 [`adr/ADR-007-pywebview-over-tkinter.md`](adr/ADR-007-pywebview-over-tkinter.md)。
> 目录结构会在 W5 删掉旧 UI 后重画，本文暂不预写未来结构。

这份文档讲这个工具怎么知道「任务结束了」，以及为什么这样拆。

## 目标与边界

目标只有一个：agent 停下来的时候，手机上收到一条能看懂的消息。

边界写在这里，省得读代码时猜：

- 单机单用户，不考虑多用户隔离和远程部署
- 只服务 WorkBuddy 和 CodeBuddy 两个宿主，别的 agent 工具没测过
- Windows 10/11，用到了 `icacls`、`os.startfile`、`SPI_GETCLIENTAREAANIMATION` 这些平台特性
- 不装服务、不写注册表、不需要管理员权限

## 怎么知道任务完成了

宿主都是「跑完一轮就停下等你下一句」的形态，判断结束有三条路。

| 方案 | 原理 | 问题 |
|---|---|---|
| 定时截图 / OCR | 轮询窗口文字 | 脆弱，耗资源，识别不准 |
| 监视会话日志 | tail `transcript.jsonl` 猜状态 | 要靠推理判断，容易误判 |
| 挂 hooks | 宿主在生命周期节点主动回调脚本 | 官方机制，事件语义明确 |

选了 hooks。这是唯一由宿主主动告诉你「我停了 / 我失败了 / 我要你确认」的通道，
不需要猜。为什么不用轮询，见 [ADR-001](adr/ADR-001-hooks-over-log-polling.md)。

## 两个宿主用同一套 hook 协议

实测确认，WorkBuddy 与 CodeBuddy 的 hooks 和 Claude Code 同构：

- 配置位置是 `settings.json` 里的 `hooks` 字段
  - WorkBuddy 用 `~/.workbuddy-ai/settings.json`
  - CodeBuddy 用 `~/.codebuddy/settings.json`
- 结构是 `事件名 → [{ matcher?, hooks: [{ type: "command", command, timeout }] }]`
- 宿主把事件 JSON 通过 stdin 交给脚本
- 退出码 0 表示正常，2 表示阻塞并回传原因，其它值非阻塞

所以一套代码能同时服务两个宿主，只靠 `--app` 参数区分显示名。
WorkBuddy 自己注册 `PreToolUse` 审批钩子用的也是这个结构，
说明这条路径是它自己走的主干道。

安装器往两个 `settings.json` 里各写 9 个事件：
`SessionStart`、`UserPromptSubmit`、`Stop`、`StopFailure`、`PostToolUseFailure`、
`PermissionRequest`、`Notification`、`TaskCompleted`、`SessionEnd`。
每个事件单独设 timeout，`Stop` 给 15 秒，其余 10 秒。

## 27 个事件收敛成 5 种语义

宿主能发的事件很多（`PreToolUse`、`PostToolUse`、`PreCompact`、`TeammateIdle` 等等），
用户只关心五件事。中间加一层语义归类，代码在 `wbn/events.py`。

| 语义 | 触发事件 | 说明 |
|---|---|---|
| `task_done` | `Stop`、`TaskCompleted` | 主 agent 一轮结束 |
| `task_failed` | `StopFailure`、`SubagentStopFailure` | 明确失败 |
| `need_input` | `PermissionRequest`、`Notification`(permission_prompt / idle_prompt) | 要授权或要你回答 |
| `tool_failed` | `PostToolUseFailure` | 连续失败到阈值才发 |
| `session_start` / `session_end` | `SessionStart`、`SessionEnd` | 默认关闭 |

归类时踩过两个坑，代码里已经处理：

`Stop` 的 `stop_hook_active` 必须过滤。当 hook 决定让 agent 继续时 `Stop` 会二次触发，
不过滤就会重复通知。

`Stop` 之后助手可能在提问。这时事件名说的「停了」其实是「等你回答」。代码会检查最后一句
是不是问句（`？`、`吗`、`请确认` 之类），命中就改归成「等你确认」。

## 两段式：hook 只做搬运

最朴素的做法是让 hook 脚本直接发 HTTP。这会把 agent 卡住，网络慢 8 秒你的对话就卡 8 秒。

所以拆成两段：

```
┌──────────────────┐  stdin(JSON)   ┌────────────────────┐
│ WorkBuddy        │ ─────────────► │ hook.py            │
│ CodeBuddy        │                │ ① 读事件            │
└──────────────────┘                │ ② 状态打点          │
                                    │ ③ 管道投递给子进程   │
                                    │ ④ 立刻退出 (~70ms)  │
                                    └─────────┬──────────┘
                                              │ stdin 管道（不落盘）
                                              ▼
                                    ┌────────────────────┐
                                    │ dispatch.py (脱离)  │
                                    │ 语义归类 → 过滤     │
                                    │ → 模板渲染 → 去重   │
                                    │ → 多通道并发发送    │
                                    │ → 失败入队重试      │
                                    └─────────┬──────────┘
                                              ▼
                          微信测试号 / 企业微信 / Bark / ntfy / 钉钉 / 飞书 / SMTP / Webhook
```

前台那段只做四件轻活：读 stdin、写状态、`Popen` 拉起 `dispatch.py`、退出。
网络请求、模板渲染、重试全在后台进程里。后台进程用 `DETACHED_PROCESS` 起，
宿主退出也不会带走它。

### 为什么用管道而不是临时文件

临时文件方案必然要「创建 + 删除」。实测这台机器上删除一个文件约 700ms，
删除操作会被安全软件拦一下。一个事件两次文件操作，等于每条通知白等 1.4 秒。

改成 stdin 管道投递后全程不落盘，hook 端耗时从 2.02s 降到 0.07s。
测量方法和完整对比见 [ADR-002](adr/ADR-002-stdin-pipe-over-temp-file.md)。

## 触发条件

不是每个事件都值得吵你。判定顺序是这样，任一步不通过就静默丢弃并记日志：

```
总开关 → 事件开关 → 静默时段 → 项目黑白名单 → 关键词
       → 最短耗时门槛 → 频率闸门（分/时） → 内容去重
```

默认值都写在 `config.json` 的 `filters` 里：

| 项 | 默认 | 为什么 |
|---|---|---|
| `min_duration_seconds` | 20 | 你问「你好」它答「你好」，不值得推 |
| `question_as_need_input` | 开 | 最后一句是问句就归成等你确认 |
| `dedup_seconds` | 180 | 同样内容不重复推 |
| `max_per_minute` | 4 | 微信模板消息接口有分钟级频次限制，留足余量 |
| `max_per_hour` | 20 | 同上 |
| `quiet_hours` | 空 | 填 `23:00-08:00`，多段用逗号分隔 |
| `tool_fail_threshold` | 3 | 工具连续失败到第 3 次才发 |

去重按内容指纹算，不按事件 ID，因为同一个任务可能被两个 hook 各报一次。
判定逻辑在 `wbn/filters.py`，去重和限流计数在 `wbn/store.py`。

## fail-open

hook 是同步执行的，它崩了或卡住，对话就废了。所以整条链路按「出任何问题都不影响你干活」设计：

- 所有异常都吞掉，永远 `exit 0`
- 永远不写 stdout。`SessionStart` 和 `UserPromptSubmit` 的 stdout 会被注入模型上下文，
  多打一个字都是污染
- stdin 读取放在守护线程里，3 秒超时兜底，防止宿主不投递 stdin 时永久阻塞
- 推送失败只影响后台进程，同时入队，下次事件时重试，最多 5 次
- `state/runtime.json`、`state/queue.jsonl` 被手改坏时，所有数字读取走宽容转换

## 为什么用 Python 加 Tkinter（已作废，见 ADR-007）

> ⚠️ 本节论证的是 2026-10 的选型，已被 ADR-007 推翻。保留原因：它解释了
> 「核心链路零第三方依赖」这条原则的来处——这条原则**仍然有效**，只是 UI 层例外。

核心链路零第三方依赖，只用标准库（`json`、`urllib`、`smtplib`、`hashlib`、`tkinter`）。
hook 进程启动快，也不会因为某个包缺失就整个失效。

Tkinter 是标准库，配置界面不用装 Qt 或 Electron，双击即开。
代价是 Tk 8.6 在 Windows 上不支持彩色 emoji，界面里的 ✅ 会显示成方框勾。
这个代价可以接受，因为推到手机上的是正常彩色 emoji。

只有托盘图标用了 `pystray` 和 `Pillow`，缺了自动降级成直接开设置窗口。

## 目录结构

```
.
├── app.py             唯一入口：ui（默认）/ tray / hook / dispatch / install / selftest
├── hook.py            宿主调用的入口，约 70ms 返回
├── dispatch.py        异步派发进程
├── ui.py              设置中心（tkinter 版，W5 下线，退路靠 tag tkinter-final）
├── tray.py            托盘常驻（可选）
├── install.py         安装器：写 hooks、建快捷方式
├── selftest.py        自检：模拟 4 类事件，验证到底能不能收到
├── config.json        你的配置，首次运行自动生成，含凭证，权限已收紧
├── webui/             pywebview 的页面层（W1 起）
│   ├── index.html     页面入口，用 Path.as_uri() 加载；带 9 条 CSP 指令
│   └── assets/
│       ├── tokens.css 设计 token（颜色/字号/间距/圆角/动效），唯一色值来源
│       ├── shell.css  壳布局 + 通用组件，不出现字面色值
│       ├── theme-boot.js <head> 里的同步脚本，首屏定主题，防 FOUC
│       ├── shell.js   主题 / hash 路由 / bridge / 侧栏 / 顶栏
│       └── pages/status.js 「运行状态」页，全站样板页
├── tools/             开发期脚本，不参与运行
│   ├── audit.py       审查：对比度、键盘可达、动效、三档窗口宽度
│   ├── shot.py        开发用截图（浅色深色、任意页面、任意窗口尺寸）
│   ├── shot_webview.py WebView2 窗口截图（WGC，ImageGrab 只能拍到黑块）
│   ├── make_icon.py   生成多尺寸图标 assets/icon.ico
│   ├── bench_ui.py    切页 / 启动耗时基线
│   ├── check_style.py 尺度与令牌纪律检查
│   └── check_responsive.py 发送期间停顿、Tab 顺序检查
├── wbn/               核心引擎
│   ├── config.py      默认值、深合并、原子读写、文件权限收紧
│   ├── events.py      事件归类 + transcript 摘要提取
│   ├── filters.py     触发条件判定（时段/项目/关键词/限流）
│   ├── render.py      模板渲染（{占位符} 替换）
│   ├── channels.py    10 个推送通道实现
│   ├── sanitize.py    日志脱敏、URL 协议校验、邮件头防注入
│   ├── store.py       去重、限流计数、会话计时、失败队列
│   ├── pipeline.py    流水线编排 + 测试发送
│   ├── insight.py     config/日志 → 前端可直接渲染的数据（零 UI 依赖，两套界面共用）
│   ├── bridge.py      暴露给 JS 的 API（config_get / config_save），返回值统一 {ok,data,error}
│   ├── theme.py       设计令牌（配色 / 圆角 / 字体 / DPI / 对比度计算）
│   ├── widgets.py     自绘控件（按钮/开关/竖脊导航/分节/时间轴/手机预览）
│   └── anim.py        补间动画，支持减弱动效
├── tests/             pytest 用例
├── state/             运行时状态（去重表、会话计时、失败队列、token 缓存）
├── logs/              运行日志，按天切分，默认保留 14 天
└── assets/            图标与界面截图
```

`config.json`、`state/`、`logs/` 全部基于 `wbn/config.py` 里的
`ROOT = Path(__file__).parent.parent` 推导。所以这个项目刻意不做成可安装包：
一旦 `pip install` 到 site-packages，`ROOT` 会变成 Python 安装目录，
配置和日志就写到不该去的地方。解压到哪里就在哪里跑。

## 踩过的坑：本机 WebView2 曾经一片黑（2026-10-27，已解决）

**症状**：`python app.py` 开出窗口，原生边框、标题、尺寸、最小尺寸全对，
但内容区是一整块黑（`background_color` 设成浅色时是一整块浅色）。

**根因：Chromium 的渲染进程沙箱在这台机器上起不来，浏览器进程起来后立刻退出。**
pywebview 完全没有察觉——初始化回调报 `IsSuccess=True`，前端照样能调通
bridge，Python 侧看不出任何异常，只是画面一次都没合成过。

**解法**：给 WebView2 加 `--no-sandbox`（`app.py` 里通过
`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` 注入）。这个窗口只加载本地
`webui/index.html`，不加载任何远端页面，关沙箱的实际暴露面很小。
**一旦以后要在这个窗口里打开外部网页，必须重新评估。**
想恢复沙箱：设环境变量 `WBN_WEBVIEW_SANDBOX=1`。

排查时排除过的方向（都验过，别重复走）：

| 怀疑 | 验证 | 结果 |
|---|---|---|
| 截图方式不对 | BitBlt / PrintWindow / Windows Graphics Capture 三种都拍 | 内容区都是纯色，不是截图问题 |
| pywebview 版本 | 6.2.1 与 5.4 各装一份 | 一样黑 |
| 我们的页面/JS 有问题 | 换成 `html="<h1>hi</h1>"` | 一样黑 |
| 中文路径 | 换成纯 ASCII 路径 | 一样黑 |
| DPI 200% | 强制 `SetProcessDpiAwareness(0)` | 一样黑 |
| 沙箱环境 | 关掉外层沙箱重跑 | 一样黑 |
| pywebview 外壳坏了 | 同一份代码换 `gui="mshtml"` | **正常渲染** → 外壳没问题，坏的是 WebView2 那一层 |

顺带记两条会反复踩的：

- **WebView2 是 DirectComposition 硬件合成，`BitBlt`/`ImageGrab` 拍到的是一整块黑**，
  `PrintWindow` 只能拍到 WinForms 父窗口的背景色。要截 WebView2 的内容必须用
  Windows Graphics Capture（`pip install windows-capture`）。
- **pywebview 6.x 从非 GUI 线程调 `evaluate_js` 会死锁**（`Semaphore` 等不到
  `ContinueWith` 的 continuation），而且死锁时连 GIL 一起占住，看门狗线程都跑不了。
  自动化验收别用 `evaluate_js` 驱动，直接对真窗口发鼠标事件更可靠。

## 前端：三层文件，一条铁律

```
index.html      壳的骨架 + 9 条 CSP 指令
tokens.css      唯一色值来源（57 个变量，亮/暗各一套）
shell.css       壳布局 + 通用组件，只写 var(--x)，不写字面色值
theme-boot.js   <head> 里的同步脚本，首屏定 data-theme，防 FOUC
shell.js        主题 / hash 路由 / bridge 封装 / 侧栏 / 顶栏
pages/*.js      页面模块，挂在 window.WBN_PAGES 上
```

**铁律：`tokens.css` 之外不许出现字面色值。** 自检（输出必须为空）：

```bash
grep -rnE ':\s*(#[0-9a-fA-F]{3,8}|rgba?\()' webui/assets/shell.css webui/index.html
```

三条不能破的：

1. **`script-src 'self'`，不带 `'unsafe-inline'`。** 这条总闸废掉 `<img onerror>`、
   内联 `<script>`、`javascript:` 三种注入。所以主题脚本是外部文件而不是内联——
   内联要么放宽这条，要么维护 sha256 白名单，后者必被遗忘。
2. **渲染文本只走 `textContent`。** `el()` 是唯一的建节点入口。禁 `innerHTML` /
   `insertAdjacentHTML` / `outerHTML` / `new Function` / `eval`。
   `sanitize.redact()` 是**脱敏**（抹掉 key），不是**转义**（抹掉 `<`），不能互相替代。
3. **每个 promise 都要 `.catch`。** 漏一个就是一条 Console 报错，
   而「Console 干净」是验收项。

**不用 ES module**：Chromium 在 `file://` 下把 module 的 origin 当 null，
`import` 会被 CORS 拦掉。全是经典脚本 + 一个全局命名空间。

## 踩过的坑：壳布局被 grid 行高撑爆（2026-10-28，已解决）

**症状**：侧栏底部那句话凭空消失，内容列表在窗口底部被切断，既看不到滚动条
也不报错，看上去像「元素根本没渲染」。

**根因**：`.shell` 是 grid，只写了 `grid-template-columns`，行高是 `auto`。
`auto` 行按内容撑开，而 `.content` 的自然高度是整页内容的高度——于是行高
远大于 `100vh`，`.main` 比窗口高，`.content` 的 `overflow-y: auto` 永远不触发，
最后被 `.shell` 的 `overflow: hidden` 一刀切掉。侧栏作为同一行的 grid item
也被拉到同样的高度，`flex: 1 1 auto` 的 `.nav` 顺势吃掉全部空间，
把 `flex: 0 0 auto` 的底部推出可视区。

**解法**（三条缺一不可，见 `shell.css` 顶部注释）：

```css
.shell   { grid-template-rows: minmax(0, 1fr); height: 100vh; overflow: hidden; }
.main    { min-height: 0; }   /* flex 纵向容器，不设就按内容长 */
.sidebar { min-height: 0; }
```

**教训**：`overflow: hidden` + grid/flex 是「静默截断」的经典组合。
看不见滚动条又确实有内容时，先怀疑行高被内容撑开，而不是元素没渲染。

## 本机 CSS 视口是 973×621，不是 1180×780

窗口按 `width=1180, height=780` 创建，但实测 `window.innerWidth = 973`、
`devicePixelRatio = 2.4`：物理客户区 2338px 宽，DPR 2.4 → CSS 视口 973。

推论（调布局时会用到）：

- 内容列 `max-width: 720` 在本机永远达不到上限（973 − 侧栏 240 − 两侧 32×2 = 669）
- 「880×600 窗口」对应的 CSS 视口是 **733**，不是 880
- 四张统计卡的断点因此定在 `max-width: 900px`（四列需要内容列 ≥ 676）

## WebView2 的 devicePixelRatio 是 2.4，不是 2.0（2026-10-28 实测，不是 bug）

W2 发现窗口按 1180×780 建出来，CSS 视口只有 973×621。查过，结论是**这台机器的
Windows「文本大小」设成了 120%**，WebView2 会把它乘进 DPR：

| 观测 | 值 | 说明 |
|---|---|---|
| 帧缓冲分辨率 | 3120 × 2080 | `EnumDisplaySettings` |
| Windows 逻辑屏幕 | 1560 × 1040 | `GetSystemMetrics(SM_CXSCREEN)` |
| DPI 缩放 | **2.0** | 3120 / 1560 |
| `HKCU\Software\Microsoft\Accessibility\TextScaleFactor` | **120** | 设置 → 辅助功能 → 文本大小 |
| WebView2 `devicePixelRatio` | **2.4** | = 2.0 × 1.2 |
| WebView2 `screen.width` | 1300 | = 1560 / 1.2 |

**不是 DPI 感知的问题。** 实测过：不设感知、设 `SetProcessDpiAwareness(2)` 两种跑法，
`dpr` / `innerWidth` 四个数字**完全相同**（2.4 / 973 / 621 / 192）。所以别去改
`enable_dpi_awareness()` 或加 `permonitorv2` 清单——那条路走不通，改了也不会变。

**为什么会这样**：`create_window(width=1180)` 的 1180 是逻辑像素，pywebview 按真实
DPI（2.0）算出 2338 物理像素的窗口；WebView2 再按 2.4 把它折回 CSS 像素，得到 973。
两个比例不一致，于是**页面比设计值大 20%，视口比设计值窄 21%**。

**要不要"修"**：不修。120% 文本大小是用户自己设的辅助功能选项，尊重它是对的。
要修的是我们的**测量口径**：

- 验收一律用 **CSS 视口宽度**，不用窗口尺寸。本机「880×600 窗口」= 733 CSS 宽，
  换一台 `TextScaleFactor=100` 的机器就是 880。**两个是不同的测试。**
- 任何布局断点、截图对照，报告里必须同时写 `devicePixelRatio` 和 `innerWidth`。

## 运行环境

| 项 | 要求 | 说明 |
|---|---|---|
| 系统 | Windows 10 / 11 | 托盘气泡用了 Windows API |
| Python | 3.10+，实测 3.13.5 | 建虚拟环境的命令见 README |
| 第三方包 | `pystray`、`Pillow` | 只有托盘需要，缺了自动降级 |
| 网络 | 能访问所选推送服务 | 微信测试号 / 企业微信 / Bark / ntfy 国内直连即可 |
| 权限 | 普通用户 | 不写注册表、不装服务 |
| 文本缩放 | 任意 | **会被乘进 `devicePixelRatio`**，见上一节。100% 与 120% 下 CSS 视口差 20% |

## 测试与 CI

`tests/` 下有 234 个 pytest 用例，覆盖折行、脱敏、事件归类、过滤链、平台差异，
以及 W0 抽取出来的 `insight` 纯函数、W1 的 bridge 返回值契约。
默认跳过 `network` 标记的用例，`gui` 类用例靠 `sys.platform` 自己跳过，
所以 Windows 上跑的是全量。

```bash
.venv\Scripts\python.exe -m pytest -q
```

GitHub Actions 跑 2 个 OS 乘 3 个 Python 版本共 6 个 job，检查顺序是
ruff、`compileall`、pytest，先快后慢。
