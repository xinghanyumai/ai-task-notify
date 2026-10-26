# 更新日志

本文件记录所有值得用户注意的变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 变更

- **文档体系重构**：README 从 738 行压到 167 行，只留定位、截图、从零快速开始、
  通道选择表、常见问题和索引。正文拆到 `docs/`：`architecture.md`（hook 协议、
  事件归类、两段式架构）、`design.md`（界面语汇、设计令牌、自绘控件、无障碍）、
  `security.md`、`troubleshooting.md`（含编码事故完整复盘）。
- **新增 `docs/channels/`**：10 个通道各一页，写申请步骤、字段含义和各自的坑。
- **新增 `docs/adr/`**：6 条决策记录（hooks 而非轮询、管道而非临时文件、微信测试号
  而非第三方平台、品牌绿拆令牌、多变量而非 `\n`、Python 加 Tkinter），
  每条都有背景、决策、代价、备选方案，带实测数据。
- **新增 `SECURITY.md`**：报问题的渠道、支持范围、凭证泄露后的处置。
- **开源自查报告拆成 `docs/reviews/` 七部分**，单文件从 1039 行降到 300 行以内。

### 修复

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
