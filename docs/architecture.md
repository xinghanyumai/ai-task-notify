# 架构

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

## 为什么用 Python 加 Tkinter

核心链路零第三方依赖，只用标准库（`json`、`urllib`、`smtplib`、`hashlib`、`tkinter`）。
hook 进程启动快，也不会因为某个包缺失就整个失效。

Tkinter 是标准库，配置界面不用装 Qt 或 Electron，双击即开。
代价是 Tk 8.6 在 Windows 上不支持彩色 emoji，界面里的 ✅ 会显示成方框勾。
这个代价可以接受，因为推到手机上的是正常彩色 emoji。

只有托盘图标用了 `pystray` 和 `Pillow`，缺了自动降级成直接开设置窗口。

## 目录结构

```
.
├── hook.py            宿主调用的入口，约 70ms 返回
├── dispatch.py        异步派发进程
├── ui.py              设置中心（竖脊导航 + 分节内容区，自绘控件）
├── tray.py            托盘常驻（可选）
├── install.py         安装器：写 hooks、建快捷方式
├── selftest.py        自检：模拟 4 类事件，验证到底能不能收到
├── config.json        你的配置，首次运行自动生成，含凭证，权限已收紧
├── tools/             开发期脚本，不参与运行
│   ├── audit.py       审查：对比度、键盘可达、动效、三档窗口宽度
│   ├── shot.py        开发用截图（浅色深色、任意页面、任意窗口尺寸）
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

## 运行环境

| 项 | 要求 | 说明 |
|---|---|---|
| 系统 | Windows 10 / 11 | 托盘气泡用了 Windows API |
| Python | 3.10+，实测 3.13.5 | 建虚拟环境的命令见 README |
| 第三方包 | `pystray`、`Pillow` | 只有托盘需要，缺了自动降级 |
| 网络 | 能访问所选推送服务 | 微信测试号 / 企业微信 / Bark / ntfy 国内直连即可 |
| 权限 | 普通用户 | 不写注册表、不装服务 |

## 测试与 CI

`tests/` 下有 170 个 pytest 用例，覆盖折行、脱敏、事件归类、过滤链、平台差异。
默认跳过 `network` 标记的用例，`gui` 类用例靠 `sys.platform` 自己跳过，
所以 Windows 上跑的是全量。

```bash
.venv\Scripts\python.exe -m pytest -q
```

GitHub Actions 跑 2 个 OS 乘 3 个 Python 版本共 6 个 job，检查顺序是
ruff、`compileall`、pytest，先快后慢。
