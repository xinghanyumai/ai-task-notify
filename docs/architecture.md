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
channels.css    通道页的两栏布局（240 列表 + 表单），同样只用 token
theme-boot.js   <head> 里的同步脚本，首屏定 data-theme，防 FOUC
shell.js        主题 / hash 路由 / bridge 封装 / 异步回调分发 / 侧栏 / 顶栏
pages/*.js      页面模块，挂在 window.WBN_PAGES 上
```

页面模块拿到的 `ctx` 有 `call / el / icon / $ / $$ / go / refreshChain /
nextCb / dropCb / notify`。`nextCb` 是异步回调：Python 侧用
`evaluate_js("window.__wbnCb({...})")` 推结果回来，页面拿一个 id 等它。
这个全局只能由壳提供——它是传输层，不是某一页的业务。

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

## 踩过的坑：js_api 上一个公开属性就炸掉整个 bridge（2026-10-28，已解决）

**症状**：窗口开出来了、页面也画了，但一直停在骨架屏（「正在读取…」），
侧栏的链路陈述同样不动。15 秒后 `app.py` 的存活探针判死，降级到 tkinter。
**Python 侧一句报错都没有**，日志里只有刷屏的：

```
[pywebview] Error while processing window.native.AccessibilityObject.Bounds.Empty.Empty.Empty...
...: maximum recursion depth exceeded
```

**根因**：W3 为了把异步结果推回页面，给 Bridge 挂了个窗口句柄：

```python
self.window = win          # ← 这一个公开属性就够了
```

pywebview 的 `get_functions()`（`site-packages/webview/util.py`）会 `dir()` 一遍
js_api 对象，对每个**公开的、非可调用的**属性递归展开。`Window` 对象展开下去
就是 `native.AccessibilityObject.Bounds.Empty.Empty...` 无限递归，异常在注入
阶段被吞掉，`window.pywebview.api` 根本没挂上——前端 `apiReady()` 等到超时，
所有 bridge 调用永远 pending。

**解法**：句柄只能放私有属性，用私有方法挂：

```python
def __init__(self, on_activity=None, window=None):
    self._window = window          # 下划线开头，get_functions 跳过
def _attach(self, window) -> None: # app.py 建完窗口再调
    self._window = window
```

`tests/test_bridge.py::test_bridge_has_no_public_data_attributes` 守着这条：
js_api 上**只许有方法**，不许有公开的数据属性。

**教训**：`js_api` 的对象图会被框架遍历。任何挂上去的东西都必须是 JSON 安全的
纯数据或方法，否则失败方式是「静默不工作」，不是报错。

## 踩过的坑：`height: 100%` 在 auto 高度的祖先下静默失效（2026-10-28）

通道页是「左列表 + 右表单」两栏，各自内部滚动，整页不滚。高度先写成
`height: 100%`，默认窗口（1180×780）下看着完全正确——**换到 880×600 就塌了**：
`.chan` 变成 1509px 高，两个栏都不滚，整页跟着滚。

原因：`.chan` 的包含块是 `.col`，`.col` 的高度是 `auto`，百分比高度解析不出来，
浏览器**静默**退化成 `auto`（不报错、不警告）。`calc(100vh - 顶栏 - 内边距)`
不依赖任何祖先的高度解析，是本机唯一稳的写法。

同一轮还发现根滚动容器会凭空多出 21px 滚动区（`de.scrollHeight` 492 vs
`clientHeight` 471），右侧多一条全高滚动条，白吃 15px 宽度。`.shell` 的
`height: 100vh` 会被向上取整到 1/64px，而真实视口是小数——改成 `height: 100%`
并给 `html, body` 加 `overflow: hidden`（这个应用**永远不整体滚动**，
滚动只发生在 `.content` 里）。

**教训**：`vh` / 百分比高度 / `overflow` 三者一起用时，必须在**最小窗口**
（880×600，不是默认的 1180×780）下再验一遍。默认尺寸下正确不算正确。

## 本机 CSS 视口是 973×621，不是 1180×780

窗口按 `width=1180, height=780` 创建，但实测 `window.innerWidth = 973`、
`devicePixelRatio = 2.4`：物理客户区 2338px 宽，DPR 2.4 → CSS 视口 973。

推论（调布局时会用到）：

- 内容列 `max-width: 720` 在本机永远达不到上限（973 − 侧栏 240 − 两侧 32×2 = 669）
- 「880×600 窗口」对应的 CSS 视口是 **723×471**（实测 `innerWidth/innerHeight`），
  不是 880×600——通道页的双栏在这一档会塌成 157px，必须换成横向条布局
- 四张统计卡的断点因此定在 `max-width: 900px`（四列需要内容列 ≥ 676）
- 通道页字段的单列/双列断点定在 `max-width: 820px`，两栏→横向条的断点
  定在 `max-width: 760px`

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

## 统计口径：什么算「一条通知」

`stats()` 有**两个数据源**，各自的判据别混。前两轮都在修「过滤」，
第三轮才发现 `today` 的**取数来源**本身就是错的（见下面「踩过的坑」）。

| 字段 | 文件 | 判据 | 为什么 |
|---|---|---|---|
| `today` | `notify-{今天}.log` | 时间戳后第一段是 `event=`，且行内有 **`remote_ok=True`** | 由 `pipeline.notify()` 写，**与通道开关无关**；`remote_ok` 已排除 `log`（`pipeline.py:92`），所以「今日通知」= 真的到手机了（D14） |
| `ok` / `fail` | `notify-{今天}.log` | `_CHANNEL_LINE` 锚定时间戳右侧第一段取出通道名，**`!= "log"` 才计数**；**且只数今天** | 汇总行 `remote_ok=False` 会被 `ok=False` 命中，失败数翻倍（19 变 38）。排除 `log` 是为了和 `today` 同一个单位。**不许写 `"channel=log" in line`**——`msg=` 是通道原文，见下。只数今天见「跨天边界」 |
| `ok` / `fail` | 同上 | 跳过时间戳后第一段是 `test-send ` / `test ` 的行 | 手动测试不算投递。只看第一段，不整行找 `test`——`msg=` 是通道原文，出现 test 很常见 |
| `recent` | `notify-*.log`（今天 + 昨天） | `_EVENT_LINE` 取 `kind` / `title` / `remote_ok`；跳过 `kind == "test"` | **W3.5 换的数据源**。旧实现读 `notifications.log`，而那个文件只有 `log` 通道写——关掉它「最近通知」永久为空，空态还说「还没收到通知」。见下 |

写侧配合改成 `test-send type=bark ok=False msg=…`（不再含 `channel=`），
但**历史行不删**——用户排查「我点了测试为什么没反应」要看它。
`bridge._LOG_CHANNEL` 同时认 `channel=` 和 `type=` 两种字段名，
否则「最后发送时间」会把测试漏掉。

### 两处**故意**不对称，别去「统一」

|  | 测试发送算不算 |
|---|---|
| `insight.stats()` 的 `today` / `ok` / `fail` | **不算**（统计的是投递，不是点按钮） |
| `bridge._last_send_times()` 的「最后发送」 | **算**（用户刚点完测试，列表要显示「刚刚」，否则会以为没生效） |

看起来矛盾，但两边服务的判断不同。谁要「统一」它们，先想清楚是哪一边的用户体验被牺牲了。

### 踩过的坑：`today` 曾经依赖一个用户能关掉的通道（2026-10-28，已解决）

旧实现里 `today` 数 `notifications.log` 的行，而**全项目只有
`channels._send_log` 写这个文件**，也就是「写入本地日志」通道。用户关掉它
（"我又不看日志文件"），`today` 就冻住，`ok` / `fail` 继续涨——第三次
「几个数对不上」。

改成数 `notify-*.log` 的 `event=…` 汇总行之后，这个数**不再依赖
任何可被关闭的东西**：通道全关，`pipeline` 照样写那一行。
（判据后来又收紧成 `remote_ok=True`，见下面 D14 那节。）

**教训**：判断一个数该从哪取，先问「这个数据源会不会被用户关掉」。
前两轮都在修过滤条件，只要数据源选错，过滤修多少次都会冒出第四次。

### 口径定案（D14）：三个数只算远端通道

```
今日通知 = 有 remote_ok=True 的事件数      ← 真的到手机了
通道成功 = 远端通道投递成功次数
通道失败 = 远端通道投递失败次数
```

`log` 通道**完全退出统计**：它写盘成功不代表通知发出去了。它仍然出现在
「启用通道 N/10」里，仍然写 `notifications.log`，仍然能在日志 tail 里看到——
只是不再冒充投递，也不再是「最近通知」的数据源。

### 「最近通知」也不能依赖可关闭的通道（W3.5，已解决）

同一个病，隔了一层：`today` 修好之后，`recent` 还在读 `notifications.log`。
关掉「写入本地日志」通道，这个文件压根不会被创建，「最近通知」永久为空，
而空态文案还写着「**还没收到通知**」——通知其实送到了。

现在 `recent` 和 `today` 同源：都读 `notify-*.log` 的 `event=` 汇总行，
读**今天和昨天**两个文件（跨零点后昨天的记录还在，但带 `day=yesterday` 前缀，
不会被当成今天）。`title=` 里的 emoji 照样剥掉。

跨天标记走 `day` 字段（`"today" | "yesterday"`），**前端只负责拼文本**，
不做日期计算——`hhmm()` 只切时间，`dayLabel()` 只加前缀，判断哪一天在 Python 里做。

为什么三个数都要排除 `log`：现场是 `16 / 16 / 14`，而其中 **14 条从没到过手机**
（`wxmp` 的 `errcode=40037 模板 ID 无效`）。三个数不在同一个宇宙里，
用户就没法从卡片上读出"到底有没有收到"。排除后是 `2 / 2 / 14`。

**只有 `log` 通道时三个数都是 0，这是对的。** `chain_sentence()` 会说
「只写入本地日志，手机上收不到」——两句一致，不矛盾。

**代价**：`log` 写盘失败（磁盘满）不再进统计。可接受——它是调试通道，
失败原因在日志 tail 和它自己的「测试此通道」里都能看到。

### 取通道名和状态必须锚定首段，理由和 `test` 那个坑同源

```python
_CHANNEL_LINE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2})\] channel=(\S+) ok=(True|False)")
```

不能写 `"channel=log" in line`：`msg=` 后面是通道返回的原文，属于不可信文本。
webhook 的上游报错里出现 `channel=log` 完全可能，整行找子串会把一次**真实失败**
当成 log 落盘排掉——数字变小、故障被吞。回归测试见
`tests/test_insight.py::test_stats_does_not_mistake_msg_for_channel_name`。

`ok=` 同理，而且更隐蔽：`msg=upstream said ok=False` 会把一次**成功**数成失败。
W3.3 时这里还留着 `"ok=True" in line` 的子串判据，理由是「真实报错原文都是
`errcode=` / `HTTP xxx`，没命中过」。**这个理由不成立**——「目前没命中过」和
「不会命中」是两件事，而写 `msg=` 的是**远端服务**，它返回什么不由我们决定。

`channels.py` 的写入格式是固定的 `channel={type} ok={ok} msg={msg[:120]}`，
所以状态字段完全可以和通道名一起锚定。W3.4 改完之后 `stats()` 里
**一个子串判断都不剩**。四轮统计 bug 全是「整行找子串」，留一处就还有第五次。
回归测试见 `test_stats_does_not_read_ok_from_msg`。

## 跨天边界：零点一过，界面就失忆

一句话原则（D18）：

> **计数问「今天做了多少」，状态问「上次怎么样」。**

混用就会出现「今日通知 2 / 通道失败 66」这种读不出结论的卡——两个数的时间范围
不一样，用户却以为在同一张卡上就能比。

| 概念 | 字段 | 时间范围 |
|---|---|---|
| 今日通知 | `today` | **只今天**（它叫「今日」） |
| 通道成功 / 失败 | `ok` / `fail` | **只今天**（和 `today` 同一张卡） |
| 最近通知 | `recent` | 今天 + 昨天 |
| 最后发送时间 | `last` | 今天 + 昨天，取最近一条 |
| 投递健康 | `delivery_health` | 跟着 `last` 走 |

### 三个症状，都只在「换一天」时出现

```
① 昨天 3 成功 2 失败 + 今天 1 成功 → ok=4 fail=2     应为 1 / 0
② 真机 logs/ → fail=66，而今天真实失败 27
③ 昨天 23:50 全失败、今天没发过
   chain_sentence → ('…送到你手机', 'ok')      ← 绿点回来了
   channel_state  → ('已启用', 'ok', True)      ← 绿点回来了
```

第 ③ 条是 R16 的**复发**。R16 修的是「有记录且失败」，零点一到记录归零，
绿点就回来了——而「昨晚跑了一夜，早上来看」正是用户最常做的事。

**为什么前几轮审计没抓到**：18 条断言的维度是「这条断言的数据源对不对」，
没有一条问「换一天会怎样」。开发时日志永远是今天刚写过的，怎么点都测不出来。
现在 `docs/prompts/README.md` §2.1 第 5 条要求：改取数的提交，验收必须跑
**四种状态**——今天有数据 / 只有昨天有数据 / 两天都有 / 两天都没有。

### 位置约束：`tag != "today"` 只能放在通道行匹配之后

```python
em = _EVENT_LINE.match(line)
if em:
    ...                    # recent 走这里，两天都要收
    continue
if _is_test_line(line):
    continue
m = _CHANNEL_LINE.match(line)
if not m or m.group(2) == "log":
    continue
if tag != "today":
    continue               # ← 只截通道行，绝不能挪到上面
```

挪到 `_EVENT_LINE` 判断之前，`recent` 会被一起断掉——昨晚那 20 条凭空消失，
「最近通知」只剩今天的。回归测试
`test_stats_recent_still_reads_two_days_after_the_cut` 钉着这个边界。

### 日期判断只在取数层

`channel_state()` / `delivery_health()` 收到的只是「某条通道最近一次记录」，
它们**不该知道这个记录来自几个文件**。`_last_send_times()` 读几个文件、
谁覆盖谁，是它自己的实现细节。

判定层加日期判断，等于把「取数」和「判定」重新搅在一起——那正是这一整段
反复出问题的原因（`today` 换了三次数据源、`ok` 修了四次判据）。

### `last` 的展示必须带「昨天」

`channels_list().last` 从 `"23:50"` 变成 `"昨天 23:50"`。不标的话，
昨天 23:50 和今天 00:10 在界面上分别是 `23:50` / `00:10`，用户以为是同一类东西；
零点后清一色「未发送」更是直接说谎——昨天明明发过。

三种形态：`"昨天 23:50"` / `"22:39"` / `None`（前端显示「未发送」）。
老记录没有 `day` 字段也不许崩，`_last_label()` 退化成纯时间。

## 投递健康信号：配置绿不等于送到了

`chain_sentence()` 原先只读配置，所以「送到你手机」是**承诺**不是**状态**：
微信连续失败 15 次，侧栏照样绿点、照样说这句。这是「界面替失败背书」的第五处
（前四处都是数字）。配置没变，只有投递结果变了，界面必须跟着变。

判据来自今天日志里每条通道的**最后一条**投递记录（`bridge._last_send_times()`）：

| 最近一次远端投递 | 侧栏文案 | level | 通道列表 |
|---|---|---|---|
| 没有记录 | 送到你手机 | `ok` | 已启用 |
| 全部成功 | 送到你手机 | `ok` | 已启用 |
| 部分失败 | 最近一次部分没送到 | `warn` | 成功的那条仍「已启用」 |
| 全部失败 | 但最近一次没送到 | `danger` | 最近一次失败：`{原文前 20 字}` |

三条硬约束：

1. **「没有记录」必须是 `none`，不能算成失败。** 首次启动、日志被清空、今天还
   没发过——都维持 `ok`。把它们判成失败就是拿一个不存在的故障吓用户，比不显示
   更糟。验收里这条和「全失败」是成对的：只测后者，你不知道自己是不是把
   「没发过」也算成了失败。
2. **只统计有记录的远端通道。** 某个通道今天一次都没试过，就没资格参与判定——
   既不算成功也不算失败。
3. **失败原因不进侧栏那句话。** 侧栏 240px，`--fs-aux` 一行约 16 个汉字，
   塞进 `errcode=40037` 会撑成三四行。报错原文只去通道页，且**先 `redact`
   再截断**——反过来的话凭证正好跨在边界上会被切成半截，半截凭证照样是线索。

`channel_state()` 的优先级是**配置问题先于运行时问题**：字段没填完连试都试不了，
说「最近一次失败」只会让人去查网络。`enabled` 可能是上一版配置留下的 `True`，
也可能被手改 `config.json` 改成 `True`——那时字段照样不全。

### 第三处不对称：测试的时间可用，成败不可用（W3.5）

`_last_send_times()` 返回的记录里多一个 `test` 标记，两个消费者分道走：

| 消费者 | 测试记录 |
|---|---|
| `channel_state()` / 通道列表的「最后发送」时间 | **用**（刚点完测试，列表要显示「刚刚」，否则以为没生效） |
| `delivery_health()` → `chain_sentence()` 的降级判定 | **不用**（一次故意的试探失败，不该把侧栏永久打成红） |

这是 W3.5 抓到的真 bug：用户在通道页点「测试此通道」试错了 key，
一次失败就把侧栏判成「最近一次没送到」，而**真实投递一次都没发生过**。
更隐蔽的是测试发的是**表单里未保存的值**——它连配置都不代表。

两处故意不对称，写进 `delivery_health()` 的 docstring 了。要「统一」之前先想清楚
牺牲的是哪一边：统一成「都算」→ 侧栏说谎；统一成「都不算」→ 测试完列表不刷新。

## 跨天边界：零点一到，界面就失忆（2026-10-28，W3.6）

W3.5 把 `recent` 改成读今天+昨天，**其他所有「最近一次」语义还是单日**。
三条实测：

```
① 昨天 3 成功 2 失败 + 今天 1 成功 → stats() = ok 4 / fail 2（应为 1 / 0）
② 真机 logs/（今天 29 条投递行，昨天 39 条）→ fail=66（今天真实 27）
③ 昨天 23:50 全失败、今天没发过
   chain_sentence → ('…送到你手机', 'ok')     ← 绿点
   channel_state  → ('已启用', 'ok', True)     ← 绿点
```

第 ③ 条是 R16 的复发：R16 修的是「有记录且失败」，零点一到记录归零，绿点回来。
而「昨晚跑了一夜，早上来看」是用户最常做的事——他看到的正好是全绿。

**口径（D18）**：

| 概念 | 时间范围 | 为什么 |
|---|---|---|
| `today` / `ok` / `fail` | 只今天 | 同一张「今日」卡上的三个数 |
| `recent` | 今天 + 昨天 | 跨零点后不能空 |
| `last` / `delivery_health` | 今天 + 昨天，取最近一条 | 「最近一次」不受日期限制 |

> **计数问「今天做了多少」，状态问「上次怎么样」。**

日期判断只在取数层（`_last_send_times()` / `stats()` 的双日循环）做。
`channel_state()` 和 `delivery_health()` 收到的就是「某条通道最近一次记录」，
不该知道它来自几个文件——判定层加日期判断，等于把取数和判定重新搅在一起，
那是这一整段反复出问题的原因。

**这类 bug 的共同特征**：只出现在「只有昨天有数据」和「两天都没有」两种状态里。
日常开发时日志永远是今天刚写过的，所以**怎么点都测不出来**。

## 编辑态：草稿只该被用户自己丢掉

通道页的编辑模型是「`base` 是磁盘，`draft` 是屏幕」。三个坑都出在「谁覆盖谁」：

| 症状 | 根因 | 修法 |
|---|---|---|
| 保存 B 通道，A 通道的未保存改动消失 | `adopt(list, keep)` 只保住**当前**通道，其余一律用磁盘值覆盖 draft | `adopt()` 改成保住**所有 dirty 通道**；`isDirty()` 必须在写 `base` 之前算——先写 `base` 就变成「跟刚写进去的值比」，永远不脏 |
| 填一半关窗，全丢 | 保存按钮卡必填（点不动），关窗又没拦 | 保存**不卡**必填（D16：存一半比全丢好），关窗用 `create_confirmation_dialog` 拦一次 |
| 点一次测试失败，侧栏永久变红 | 测试用的是未保存的表单值 | `delivery_health()` 排除 `test` 记录，见上一节 |

关窗拦截**不许自绘**：`webview.Window.create_confirmation_dialog` 是系统对话框，
键盘焦点、屏幕阅读器、DPI 缩放都是对的；自绘的 HTML 弹层要自己处理这三件事。
`set_dirty` 带 200ms 防抖——输入框每敲一个字都发一次 IPC，关窗那一刻的判定会读到
过期的值。

## 超时口径：前端必须比后端长

后端一次发送最坏要**串两次请求**：先换 `access_token`（`channels.TOKEN_TIMEOUT`
= 10s），再发消息（`advanced.http_timeout`，默认 8s），所以最坏 18s。

前端兜底超时由 `channels_meta().timeouts` 下发后派生，**不写死**：

```
前端超时 = max(20s, (token + send) × 1000 + 2000ms)
```

| `http_timeout` | 后端最坏 | 前端兜底 | 余量 |
|---|---|---|---|
| 3 | 13s | 20s（下限） | 7s |
| 8 | 18s | 20s | 2s |
| 20 | 30s | 32s | 2s |

前端比后端短会**反向说谎**：t=10s 前端报「等待超时」并丢掉回调，t=18s 后端
其实成功了，`_push()` 发现 cbMap 里没有这个 id 就静默丢弃——用户看到的失败是假的。
下限 20s 而不是算式的 15s：`http_timeout` 被调到 3 时两段抖动叠起来仍可能超 15s。

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

`tests/` 下有 306 个 pytest 用例，覆盖折行、脱敏、事件归类、过滤链、平台差异，
以及 W0 抽取出来的 `insight` 纯函数、W1 的 bridge 返回值契约、W3 的统计口径与
投递健康信号、W3.5 的草稿保护与最近通知取数、W3.6 的跨天边界。
默认跳过 `network` 标记的用例，`gui` 类用例靠 `sys.platform` 自己跳过，
所以 Windows 上跑的是全量。

```bash
.venv\Scripts\python.exe -m pytest -q
```

### 写验收脚本时：`CONFIG_PATH` 不等于隔离

`config.LOG_DIR` / `STATE_DIR` / `SESSION_DIR` 是**模块级常量**（`ROOT/"logs"` 等），
把 `CONFIG_PATH` 指到临时目录**不会**把日志一起隔离。W3.2 验收时就踩了这个：

```
第一次跑：只改了 CONFIG_PATH → today 起点是 14（读的还是仓库 logs/）
补上三个目录 → 才拿到 0→1 的干净数据
```

要隔离就把四个一起改：

```python
config.CONFIG_PATH = tmp / "config.json"
config.LOG_DIR     = tmp / "logs"
config.STATE_DIR   = tmp / "state"
config.SESSION_DIR = tmp / "sessions"
```

**这类错误的特征是「数字有值但起点不对」**——它不会报错，只会让你把
`today=14` 当成"刚发了一条"。看到非 0 起点先怀疑隔离没做全，别怀疑逻辑。


GitHub Actions 跑 2 个 OS 乘 3 个 Python 版本共 6 个 job，检查顺序是
ruff、`compileall`、pytest，先快后慢。
