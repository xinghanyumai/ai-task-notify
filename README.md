# AI 任务通知

WorkBuddy / CodeBuddy 跑完一轮、失败、或者停下来等你确认的时候，把结果推到手机。

Windows 10 / 11 · 个人自用 · 零服务器 · 零订阅费 · 零实名

在聊天窗口里等 agent 干活的体验不太好：它跑完了你不知道，它问你要权限你也没看见。
这个工具挂在宿主的 hooks 上，任务一停就把结果发到微信、企业微信、Bark 或 ntfy。
通知链路只用 Python 标准库，托盘那两个包缺了也能跑。

<p align="center">
  <img src="assets/ui_channels_light.png" width="49%" alt="推送通道页（浅色）">
  <img src="assets/ui_channels_dark.png" width="49%" alt="推送通道页（深色）">
</p>
<p align="center">
  <img src="assets/ui_template_light.png" width="32%" alt="消息模板页">
  <img src="assets/ui_status_light.png" width="32%" alt="运行状态页">
  <img src="assets/ui_guide_light.png" width="32%" alt="使用教程页">
</p>
<p align="center">
  <sub>推送通道（浅色 / 深色） · 消息模板 · 运行状态 · 使用教程</sub>
</p>

## 快速开始

### 1. 建环境

需要 Windows 10/11 和 Python 3.10 以上（开发机上实测 3.13.5）。在项目目录里执行：

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt` 里只有 `pystray` 和 `Pillow`，托盘图标要用。不装也能跑，只是没有托盘。

### 2. 挂上 hooks

```bash
.venv\Scripts\python.exe install.py all
```

这一步做三件事：往 WorkBuddy 和 CodeBuddy 的 `settings.json` 里各写 9 个事件的 hook、
建桌面快捷方式、建开机自启项。安装器是幂等的，重复执行只替换自己的条目，
改 `settings.json` 之前会自动备份成 `settings.json.bak-时间戳`。
想看看当前状态用 `install.py status`，想撤掉用 `install.py uninstall`。

> 快捷方式指向执行 `install.py` 的那个解释器，所以上面用 `.venv\Scripts\python.exe`
> 而不是裸 `python`。装到别的解释器里，快捷方式就会指向那个解释器。

### 3. 配一个手机通道

双击桌面的「AI 任务通知」，进「推送通道」页。左侧是通道清单，绿点表示已启用，
点任意一项在右侧填参数，填完点右下角「测试此通道」，手机应该立刻响。

微信测试号是最省事的一条路，扫码两分钟，免费免实名，消息直接进微信聊天列表。
完整步骤和三个容易踩的坑在 [docs/channels/wechat-sandbox.md](docs/channels/wechat-sandbox.md)。

### 4. 重启宿主

**hooks 配置在宿主启动时读取并缓存，改完必须完全退出重启。**

WorkBuddy 点窗口关闭按钮只是隐藏到托盘，要右键托盘图标选「退出」，
或者用任务管理器结束 `WorkBuddyAI.exe`。CodeBuddy 完全关掉再打开。

重启后跑一个 20 秒以上的任务，手机会收到通知。

### 5. 验证

```bash
.venv\Scripts\python.exe selftest.py            # 真实发送 4 类通知
.venv\Scripts\python.exe selftest.py --dry      # 只渲染，不发送
.venv\Scripts\python.exe selftest.py --status   # 看 hooks 挂载状态
```

`selftest.py` 不经过 stdin，所以它发得出去不代表真实事件也发得出去。
端到端只有一个验证方法：在 WorkBuddy 里跑一个长任务，然后看手机。
测试消息收得到、真实任务一条都没有的话，看 [docs/troubleshooting.md](docs/troubleshooting.md)。

## 打成单文件 exe（可选）

不想在目标机器上装 Python，就打包一个 exe 拷过去：

```bash
.venv\Scripts\python.exe -m pip install pyinstaller
.venv\Scripts\python.exe tools\build_exe.py
```

产物是 `dist\通知.exe`（约 19MB，一个文件）。`build_exe.py` 会在打包前后做七道
核对：产物形态、五个子命令模块在不在包里、pywebview 的注入脚本在不在、
`webui/` 文件数源码 == exe、体积上限、`--version` 冒烟。**任何一道红都别绕过**——
它们挡的是「exe 能启动但一件正事都干不了」那类问题，本机测不出来。

**交付时只发 exe，不要把 `dist\` 整个打包。** 跑过一次之后 `dist\` 里会有
`config.json` / `logs\` / `state\` —— 那是你本机的运行期产物（配置就该写在 exe
旁边，这样整个文件夹拷走就带走配置），带出去等于把配置一起发出去。

```bash
dist\通知.exe                 # 开设置窗口（双击就是这个）
dist\通知.exe install         # 挂 hooks + 建快捷方式
dist\通知.exe uninstall       # 撤掉
dist\通知.exe tray --silent   # 托盘常驻
dist\通知.exe --version       # 版本号
```

配置和日志写在 **exe 旁边**（`config.json` / `logs/` / `state/`）。放在
`C:\Program Files` 这种写不进去的目录时，自动回落到
`%LOCALAPPDATA%\WBNotify\`——状态页会显示实际用的路径。

排查问题用调试版（带控制台，能看见 traceback）：

```bash
set WBN_CONSOLE=1 && .venv\Scripts\python.exe -m PyInstaller --noconfirm notify.spec
dist\通知-debug.exe
```

## 选哪个通道

内置 10 个通道，可以同时开多个，任一成功即视为送达。默认只开「本地日志」，
它只写文件不打扰你，用来回溯和排查。

| 通道 | 费用 | 实名 | 配置耗时 | 到达方式 | 适合谁 |
|---|---|---|---|---|---|
| 微信测试号 | 免费，不限条数 | 不要 | 扫码约 2 分钟 | 微信聊天列表 | 主力推荐 |
| 企业微信自建应用 | 免费，不限条数 | 不要 | 约 5 分钟 | 企业微信 / 微信插件 | 想要 markdown 排版 |
| Bark | 免费，可自建 | 不要 | 装 App 约 1 分钟 | iOS 通知中心 | iPhone |
| ntfy | 免费，可自建 | 不要 | 装 App 加订阅 | App 推送 | 安卓 / 桌面，公共服国内偏慢 |
| 企业微信群机器人 | 免费 | 不要 | 建群加机器人 | 企业微信 | 已有企业微信 |
| 钉钉 / 飞书机器人 | 免费 | 不要 | 群设置加机器人 | 对应 App | 支持加签 |
| 邮件 SMTP | 免费 | 不要 | 开 SMTP 加授权码 | 手机邮件 App | 最通用，配置稍麻烦 |
| 自定义 Webhook | 免费 | 不要 | 填 URL | 你自己的服务 | 接自建系统 |
| 本地日志 | 免费 | 不要 | 默认开 | 文件 | 回溯与排查 |
| 短信 / Telegram | 收费 / 需科学上网 | 视服务商 | 视服务商 | 短信 / TG | 不推荐 |

每个通道的申请步骤、字段含义和各自的坑都在 [docs/channels/](docs/channels/)。
为什么主推微信测试号而不是第三方推送平台，见
[ADR-003](docs/adr/ADR-003-wechat-sandbox-over-push-platforms.md)。

## 已经有官方通知 / MCP 推送了，还需要这个吗？

先说结论：**如果你只是想让任务结果出现在手机上，你可能不需要装本项目。**
市面上已经有几个能用的方案，其中「微信单通道」这件事本项目确实是重复建设。

### 别人已经有的

| 已有方案 | 覆盖什么 | 本项目的差异 |
|---|---|---|
| WorkBuddy 自带的桌面通知 | 电脑上弹窗 | 我们多一层「手机也能收」和「历史可查」 |
| `wechat-clawbot-push`（PyPI，MIT，MCP 连接器） | 任务结果 → 个人微信，复用已绑定的 ClawBot，不占名额 | 我们多 9 个通道、6 类事件模板、去重/静音、多宿主 |
| 企业微信群机器人 | 微信群/个人微信收消息 | 我们是同一个通道，但会按事件类型分模板 |
| `wexin-claw-hook`（Tauri 单 exe，本地 9720 webhook） | 任意脚本 curl 一行推微信 | 我们不要求宿主能发 HTTP |

**`wechat-clawbot-push` 在本机实测（2026-10-30）**：链路通（`--diag` 第 5 步
HTTP 200、4 个 IPv4 TLS 握手全 OK），但**照它的 README 逐字配置会失败**——
它把路径硬编码成 `~/.workbuddy/...`，而 WorkBuddy 当前实际用的是
`~/.workbuddy-ai/...`（`~/.workbuddy` 是 legacy 目录，本项目 `install.py` 里也是
这么标的）。后果是：MCP 注册进一个不会被读的文件、凭证**自动发现**失败、
`--diag` 直接建议你跑 `--login`。

但配置不用改它的源码，只要把两个值填进**它自己会读的那个** `config.json`：
`bot_token` 和 `user_id`（都在 `~/.workbuddy-ai/` 下面，只读不写）。
注意这两类路径的处理**不一样**：

| 放什么 | 路径 | 要不要加 `-ai` |
|---|---|---|
| MCP 注册 | `~/.workbuddy-ai/mcp.json` | **必须加**，不加就注册进一个不会被读的文件 |
| 凭证 | `~/.workbuddy/wechat-clawbot-push/config.json` | **不加**，包源码里硬编码就是这个路径 |

**绝不能跑 `--login`**——它会占掉微信侧唯一名额、顶掉 WorkBuddy 原生微信连接。

另外实测 `ret=-2` 的真正原因**不是**缺 `context_token`，是**没有「最近活跃会话」**：
你在微信里给 bot 发过一句话之后，**不携带 `context_token` 也能直接推成功**
（实测回执 `HTTP 200 发送成功（未携带 context_token，走最近活跃会话）`）；
没发过就是 `ret=-2`。所以「装好就能推」要补一个前提：**先让那个会话热起来**。

也别指望用 `--refresh` 去捞 `context_token`——WorkBuddy 同时运行时它**必然失败**
（两边用同一个 bot 抢同一条消息流，实测 `getupdates` 超时）。这也说明依赖
`context_token` 的路子本身不可靠，但**不影响推送**。

### 差异到底是什么

那些方案解决的是「**怎么把一句话送到我手机上**」；本项目解决的是
「**哪些事该发、发什么、发过没有、发失败了算谁的**」。
如果你只是想让任务结果出现在微信里，装 `wechat-clawbot-push` 就够，
**不用装本项目**。如果你要的是「六类事件分模板 + 十个通道任选 + 不依赖任何私有
目录」，再往下看。

### 本项目不依赖宿主的私有结构

这是唯一站得住的硬差异：本项目只读宿主**公开的 hooks 协议**——即
`~/.workbuddy-ai/settings.json` 里的 `hooks` 字段，那是宿主对外承诺的扩展点。
第三方 MCP 方案需要读 `~/.workbuddy-ai/claw-state/` 下的内部游标文件，那不是
公开 API，宿主一改内部结构就断。

顺带一句：本项目自己那条**微信测试号通道**也是保留的，不会因为上面这些方案存在
就标成「已废弃」——它不依赖任何私有目录，是宿主改内部结构后唯一还能活的微信出口。

## 常见问题

### 会不会拖慢我的对话

不会。hook 只做搬运，投递给后台进程之后立刻退出，本机实测约 70ms。
推送失败、通道超时、配置写坏都不会影响宿主，hook 永远 `exit 0`。

### 要不要一直开着托盘

不用。通知靠 hooks 按需唤起，托盘只是给你一个看状态和随手测试的入口，
它挂了不影响通知。

### 微信收到消息但正文是空的

模板里每个 `{{变量.DATA}}` 前面必须有「标签：」，否则那几行渲染为空，
而接口还返回 `errcode: 0`，看起来像成功了。跑 `selftest.py --wxmp` 会逐行告诉你
哪一行会空白、详情实际折成几行。

### 手机什么都没收到

先跑 `selftest.py`。测试消息能收到、真实任务没有，基本是宿主没重启，
或者撞上了中文编码那个坑，见 [docs/troubleshooting.md](docs/troubleshooting.md)。

### 改了模板没反应

模板改动要点「保存配置」才落盘。

### 凭证会不会泄露

`config.json` 已进 `.gitignore`，程序每次保存后会用 `icacls` 把它的 ACL 收紧成
仅当前用户可读写，日志和错误信息统一走 `sanitize.redact()` 脱敏。
细节见 [docs/security.md](docs/security.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | hook 协议、事件归类、两段式架构、触发控制、fail-open |
| [docs/archive/design.md](docs/archive/design.md) | 📦 已归档：tkinter 时代的界面语汇、设计令牌、自绘控件 |
| [docs/security.md](docs/security.md) | 凭证处理、请求边界、不可信输入、截图脱敏 |
| [docs/troubleshooting.md](docs/troubleshooting.md) | 症状对照表、日志位置、编码事故完整复盘 |
| [docs/channels/](docs/channels/) | 10 个通道各一页，申请步骤与坑 |
| [docs/adr/](docs/adr/) | 6 条决策记录，写的是为什么这样选 |
| [docs/reviews/](docs/reviews/) | 2026-10-26 的开源就绪审查报告（01–04、07） |
| [SECURITY.md](SECURITY.md) | 怎么报安全问题、凭证泄露后的处置 |
| [CHANGELOG.md](CHANGELOG.md) | 变更历史 |

## 已知限制

微信测试号是「开发测试」定位，微信官方不承诺永久不变，接口调整了就先跑
`selftest.py --status` 确认，再换企业微信自建应用（同样免费免实名）。
详情最多四行、每行约 18 个汉字，因为微信卡片
一个变量只占一行。完整清单见 [docs/troubleshooting.md](docs/troubleshooting.md#已知限制)。

exe 形态另外三条：

- **每次 hook 事件要多等约 2 秒。** onefile 每次启动都要把 19MB 解压到
  `%TEMP%`。宿主的 hook 超时是 10–15 秒，不会超时，但 agent 的每次事件都被
  拖了一下。嫌慢就用源码形态（`python app.py install`），那条路径 ~70ms。
- **WebView2 沙箱默认开着**，只在判定「窗口一片黑」时自动带 `--no-sandbox`
  重启一次。想强制带沙箱排查：`set WBN_WEBVIEW_SANDBOX=1`。
- **没在「没装 Python 的干净机器」上验过**（开发机只有一台）。本机能做到的
  最大程度是核对包内模块/文件清单 + 用 CDP 驱动五个页面全部渲染 + 打通
  hook→派发→通道全链路；目标机器缺 VC++ 运行库或 WebView2 运行时这类问题，
  只能到那台机器上才知道。

## 卸载

```bash
.venv\Scripts\python.exe install.py uninstall
```

移除两个 `settings.json` 里的 hooks，删掉桌面和开机自启快捷方式。
`config.json`、`logs/`、`state/` 留在项目目录，整个文件夹直接删掉也行。

## 许可

[MIT](LICENSE)
