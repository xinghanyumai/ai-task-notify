# 更新日志

本文件记录所有值得用户注意的变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 新增

- **单文件 exe**：项目根目录的 `通知.exe`，约 19MB，双击即用，目标机器不需要装 Python。
  `install` / `uninstall` / `tray` / `hook` / `selftest` 全部走子命令
  （`通知.exe install`、`通知.exe hook --app WorkBuddy` …），源码那套用法不变。
  配置、日志、状态写在 exe 旁边，整个文件夹拷走就带走配置；exe 放在不可写目录
  （如 `C:\Program Files`）时自动落到 `%LOCALAPPDATA%\WBNotify`。

### 变更

- **产物从 `dist\` 挪到项目根**：打出来直接就是根目录的 `通知.exe`，`dist\` 目录不再产生。
  连带一个行为变化——`config.json` / `logs\` / `state\` 现在**源码模式和 exe 模式
  共用同一份**（以前 exe 在 `dist\` 里，是各写一份）。好处是配置只有一份，界面里
  改完两边一致；代价是 exe 会往仓库根写日志，这是有意的。
  `tools/build_exe.py` 原来会顺手清 `dist\` 里的 `config.json` / `logs` / `state`，
  产物挪位置之后那三样就是真实数据了，**这段清理逻辑已删除**，只删 exe 本身。

- **WebView2 沙箱默认打开**：之前为了兼容性默认关闭，现在默认开启，只在看门狗
  判定「窗口没画出来」时降级一次重试。`WBN_WEBVIEW_SANDBOX=1` 强制打开并禁止降级，
  `WBN_WEBVIEW_NOSANDBOX=1` 强制关闭。
- **文档体系重构**：README 从 738 行压到只留定位、截图、从零快速开始、
  通道选择表、常见问题和索引。正文拆到 `docs/`：`architecture.md`（hook 协议、
  事件归类、两段式架构）、`design.md`（界面语汇、设计令牌、自绘控件、无障碍）、
  `security.md`、`troubleshooting.md`（含编码事故完整复盘）。
- **新增 `docs/channels/`**：10 个通道各一页，写申请步骤、字段含义和各自的坑。
- **新增 `docs/adr/`**：6 条决策记录（hooks 而非轮询、管道而非临时文件、微信测试号
  而非第三方平台、品牌绿拆令牌、多变量而非 `\n`、Python 加 Tkinter），
  每条都有背景、决策、代价、备选方案，带实测数据。
- **新增 `SECURITY.md`**：报问题的渠道、支持范围、凭证泄露后的处置。
- **开源自查报告拆成 `docs/reviews/` 五部分**，单文件从 1039 行降到 300 行以内；
  里面的提示词部分另立 `docs/prompts/`，按 P0–P5 重排。
- **开发脚本收进 `tools/`**：`audit.py`、`shot.py`、`make_icon.py` 从根目录移入，
  和已有的 `bench_ui.py` 等放在一起。根目录只剩 6 个产品入口（含面向用户的
  `selftest.py`）。这三个脚本的命令路径随之变成 `python tools/xxx.py`，
  文档与 `install.py` 里的导入已同步。用户不受影响——没人需要手敲它们。

### 修复

- **一个任务重复弹两三条一模一样的通知**：宿主对同一个 turn 的「完成」会连发几次，
  而原来的去重键把渲染结果（含耗时）也算进去了——三次渲染的耗时是 12 秒 / 15 秒 /
  18 秒，于是三个不同的键，三条全发。现在终态事件（任务完成 / 任务失败）按
  「哪个会话的第几轮」去重，同一个 turn 只发一条；「等你确认」「工具失败」这类
  仍然按内容去重，因为一个 turn 里真的可能问两次。
- **exe 里的 `hook` 子命令能用了**：之前打出来的 exe 调 `hook` 会立刻退出、一条通知
  都发不出去，而且没有任何报错。现在宿主触发事件后能正常走完并推送。
- **exe 的界面不再永远停在「正在读取…」**：之前窗口能开，但前端脚本没进包，
  页面一直转圈。现在五个页面都能正常显示。
- **从 exe 里拉起自己不再挂死**：之前「hook 收到事件 → 拉起 dispatch」「托盘打开设置」
  这类自我调用会卡住半分钟然后什么也不做。现在正常返回。
- **README 事实错误**：文件结构里 `theme.py` 被列了两次；三处硬编码绝对路径
  `D:\Code\Work buddy 通知` 改为相对路径；「`.venv` 已就绪」改为从零创建虚拟环境的命令。
- `Toast` 控件引用了从未导入的 `RADIUS_LG`，这个 `NameError` 从首个提交起就存在，
  被 `pythonw.exe` 吞掉，现已修复并加了回归用例。
- `sanitize.redact` 把 CRLF 折成两个空格，改为用一条正则折成一个，脱敏后的日志行不再有多余空格。

## [1.0.0] - 2026-10-25

首个可用版本。核心链路已在 WorkBuddy / CodeBuddy 上完成端到端验证。

### 新增

- **hooks 接入**：一套代码同时服务 WorkBuddy（`~/.workbuddy-ai/settings.json`）
  与 CodeBuddy（`~/.codebuddy/settings.json`），挂载 9 个生命周期事件。
- **5 种语义归类**：把宿主的 27 个事件收敛成 `task_done` / `task_failed` /
  `need_input` / `tool_failed` / `session_start|end`，用户只需关心这五件事。
- **10 个推送通道**：微信测试号（wxmp）、企业微信自建应用、企业微信机器人、
  Bark、ntfy、钉钉、飞书、SMTP、自定义 Webhook、本地日志。
  可同时启用多个，任一成功即视为送达。
- **微信模板消息自动适配**：自动拉取 `get_all_private_template` 读模板真实变量，
  按变量名映射语义；支持 `detail` / `detail2` / `detail3` / `detail4` 多行分段。
- **设置中心**：Tkinter 自绘控件（按钮/开关/竖脊导航/分段控件/滚动条/手机预览），
  浅色 / 深色 / 跟随系统三态，全部尺寸走 DPI 换算。
- **触发控制**：总开关 → 事件开关 → 静默时段 → 项目黑白名单 → 关键词 →
  最短耗时门槛 → 频率闸门 → 内容去重，八层过滤。
- **安装器**：`install.py` 幂等写入 hooks（自动备份 `settings.json.bak-时间戳`），
  创建桌面与开机自启快捷方式，`uninstall` 可完整回滚。
- **自检工具**：`selftest.py` 支持 `--dry` / `--status` / `--encoding` / `--wrap` /
  `--wxmp` / `--adopt` / `--version`。
- **无障碍审查**：`audit.py` 覆盖对比度（逐项实测）、键盘可达、主按钮唯一性、
  减弱动效短路、三档窗口宽度溢出。

### 修复

- **中文变落单代理字符导致真实事件全部丢失**：宿主投递的 stdin 是 UTF-8 字节，
  但 Windows 上 `sys.stdin` 按 cp936 + surrogateescape 解码，产生 `\udca2` 这类
  孤立代理码位，`encode("utf-8")` 直接抛异常，通知在投递给派发进程前就消失。
  改为读 `sys.stdin.buffer` 原始字节显式按 UTF-8 解码，并加 `clean_obj()` 递归清洗。
  `selftest.py --encoding` 作为回归防线。
- **微信消息只显示模板标题（如 `test`）**：模板内容里纯 `{{变量.DATA}}` 单独占一行
  是无效的，微信要求「标签：变量」形式，否则该行渲染为空。`wxmp_content_warnings()`
  会逐行指出哪一行会空白。
- **`\n` 换行方案被实测推翻**：新版模板消息卡片里 `\n` 不换行，还会把之后的内容
  整段丢掉。改为「一个变量一行」，按声明数量切段，行首避标点、英文单词不劈开。
- **空闲提醒误报为「等你确认」**：`Notification` 的 `idle_prompt` 与 `Stop` 报的
  是同一件事，已在 `events.py` 过滤。
- **输入框点击后消失**：伪造的 `<Configure>` 事件宽高为 0，圆角矩形算出负区域。
- **通道列表被裁切**：左右两栏原本共用一个滚动区，右栏变短会挤压左栏。
- **切换通道整页闪烁**：改为只重建右栏并带滑入过渡。
- **任务栏显示 Python 图标**：`assets/app.ico` 文件名不匹配 + 未设 AppUserModelID。

### 安全

- `config.json` / `state/token_cache.json` 保存后自动 `icacls` 断开 ACL 继承，
  只授当前用户。
- 所有对外错误信息过 `sanitize.redact()`，掩掉 `secret=` / `access_token=` /
  24 位以上十六进制串 / URL 路径里的长随机串。
- `_http()` 只放行 http / https，拦掉 `file://` 读取本地文件。
- 邮件头压成单行，防 `Bcc` 注入。
- 模板渲染走正则替换而非 `str.format`，模板里写裸 `{` 不会炸，也无格式化字符串注入。
- 会话 ID 写文件前只保留字母数字与 `-_`，截断到 64 字符，`../` 构不成路径穿越。

### 已知限制

- 仅支持 Windows 10 / 11（托盘气泡、`icacls`、DWM 标题栏、减弱动效检测均为 Windows API）。
- Tkinter 8.6 在 Windows 上不支持彩色 emoji，界面里 ✅ 显示为方框勾；推到手机的是彩色。
- 微信模板消息的标题创建后不可修改，它决定手机卡片第一行。
- 微信卡片一个变量只占一行，详情最多四行，每行约 18 个汉字。
- 微信测试号的昵称与头像固定不可改（由 appid 派生）。
