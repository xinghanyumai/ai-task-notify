# 开源就绪审查报告 · AI 任务通知

> 审查日期：2026-10-26
> 审查对象：`D:\Code\Work buddy 通知`（21 个 py 文件 / 9319 行）
> 审查立场：项目顾问。只评估、不动代码。

---

## 一、结论速览

| 维度 | 评分 | 判断 |
|---|---|---|
| 功能完成度 | 95 / 100 | 端到端已跑通，12 轮迭代闭环，边界情况处理扎实 |
| 代码质量 | 82 / 100 | 注释与防御性编码优于多数个人项目；缺测试与静态检查 |
| 结构完整性 | 65 / 100 | 引擎层分层清晰，但三个巨型文件拉低可维护性 |
| 工程化 / 开源就绪 | 25 / 100 | **无 git、无 LICENSE、无依赖声明、真实凭证在仓库内** |
| 文档质量 | 88 / 100 | README 738 行，架构/选型/排错齐备，但有 4 处事实性错误 |
| **综合** | **71 / 100** | **功能可交付，工程不可交付** |

**一句话结论**：这是一个「功能已经做完、工程还没开始」的项目。现在上传会同时造成
**凭证泄露**和**许可证缺失**两个法律/安全问题，必须先做 P0 六项才能开仓库。

---

## 一之二、执行进度（2026-10-26 更新）

| P0 项 | 状态 | 证据 |
|---|---|---|
| P0-1 凭证剥离 | 🟡 **部分完成** | 仓库内零命中；但 **appsecret 仍需用户去后台重置** |
| P0-2 git + .gitignore | ✅ 完成 | 37 文件入库，`git status` clean |
| P0-3 LICENSE | ✅ 完成 | MIT，Copyright (c) 2026 XingHan |
| P0-4 依赖声明 | 🟡 部分完成 | `requirements.txt` ✅ / `pyproject.toml` 待补 |
| P0-5 README 从零流程 | ❌ **未做** | README 仍假设 `.venv` 已存在（L309） |
| P0-6 README 事实错误 | ❌ **未做** | theme.py 重复、3 处绝对路径、图片重复引用 |

已验证的克隆可用性（`git clone` 到临时目录后实测）：

```
不存在(正确): config.json / state/token_cache.json / logs/*.log / .venv
compileall 退出码=0
11 个模块导入成功，tkinter 8.6
config.save_default_if_missing() → True，通道数 10
tray.py 在无 pystray 时 pystray=None，降级路径可用
selftest.py --encoding → 编码检查全部通过 / 折行检查全部通过
```

结论：核心链路确实是「标准库即可运行」，克隆后不需要任何 pip 安装就能跑通引擎。
`requirements.txt` 只是托盘与开发工具的补充。

**另已处理**：commit 作者邮箱从真实邮箱改为 `XingHan@users.noreply.github.com`，
reflog 已清空并 `gc --prune=now`，旧对象（含真实邮箱）已物理删除，
`git fsck --unreachable` 无残留。若 GitHub 用户名不是 `XingHan`，推送前改一行：

```bash
git config user.email "<你的用户名>@users.noreply.github.com"
git commit --amend --reset-author --no-edit
```



---

## 二、现状盘点（已核实，非推测）

### 2.1 已验证通过的部分

| 项 | 验证方式 | 结果 |
|---|---|---|
| 全部文件语法 | `python -m compileall` | 21/21 通过 |
| 引擎模块可导入 | 逐个 `__import__` | 11/11 通过 |
| hooks 挂载状态 | `selftest.py --status` | WorkBuddy / CodeBuddy 各 9 个事件已挂载 |
| 编码回归 | `selftest.py --encoding` | 全绿（含 12 项折行检查） |
| 真实发送链路 | `logs/notify-2026-10-25.log` 22:28 | `remote_ok=True`，微信送达 |
| 无障碍审查 | `audit.py` | 对比度 / 键盘 / 动效 / 三档宽度全部通过 |

### 2.2 代码规模分布

```
wbn/widgets.py    2151 行   ← 自绘控件库（26 个类）
ui.py             1811 行   ← 设置中心（App 类 57 个方法 / 1675 行）
wbn/channels.py   1405 行   ← 10 个通道 + 微信模板消息逻辑
selftest.py        547 行   ← 手工自检
install.py         413 行
wbn/theme.py       364 行
wbn/config.py      326 行
wbn/events.py      276 行
audit.py           272 行
wbn/store.py       269 行
其余 12 个文件      1000 行
```

### 2.3 值得保留的工程优点（不要在重构中丢掉）

1. **fail-open 设计**贯穿 hook 全链路：永不写 stdout、永不非零退出、stdin 3 秒超时兜底。
   这是本项目最硬的设计决策，README 1.6 节已固化。
2. **安全边界真实做了**：`sanitize.py` 拦 `file://`、脱敏 URL 中的 key、邮件头压单行、
   模板不用 `str.format` 防注入、凭证文件 `icacls` 收紧。
3. **异常处理无裸 `except:`**：全仓 0 处裸捕获，65 处 `except Exception` 都带兜底语义。
4. **配置向前兼容**：`deep_merge` + `_merge_channels` + `RETIRED_CHANNELS`，
   升级不会让老 config 里的僵尸通道冒出来。这个细节多数项目不做。
5. **注释写「为什么」不写「是什么」**：hook.py 顶部注释直接说明「为什么不用临时文件中转」，
   带实测数据（2.02s → 0.07s）。这类注释是资产。

---

## 三、不足清单

### P0 · 上线阻断（不修不能开仓库）

#### P0-1 真实凭证在仓库目录内 ⛔ 最高危

| 文件 | 泄露内容 | 后果 |
|---|---|---|
| `config.json` | appid `<APPID>`、appsecret（32 位明文）、openid（28 位） | 任何人可向该测试号任意关注者发消息 |
| `state/token_cache.json` | 真实 `access_token`（`<ACCESS_TOKEN>`，7200s 有效） | 可直接调用接口 |
| `state/wxmp_template.json` | 真实模板 ID `<TEMPLATE_ID>` | 可伪造通知内容 |

> 本文档中所有真实凭证均已脱敏为 `<占位符>`，仓库内不留明文。

`assets/` 五张截图已用 `WBN_SCRUB=1` 脱敏（`shot.py` L31 有实现），**这部分是安全的**，
但必须在开仓库前逐张目视复核一次。

**修法**：
1. 先去测试号后台**重置 appsecret**（视同已泄露，不能只删文件）。
2. 从目录移除 `config.json` / `state/` / `logs/`，改由 `.gitignore` 拦截。
3. 新增 `config.example.json`：全空值 + 注释性字段名，作为新用户模板。
4. 新增 `state/.gitkeep` 与 `logs/.gitkeep` 保住目录结构。

#### P0-2 无版本控制

`git status` → `fatal: not a git repository`。9319 行代码、12 轮迭代、零提交历史。

后果：无法 diff review、无法回滚、无法追溯「哪一轮引入了哪个 bug」。
这也是为什么本次审查只能靠读代码 + 读 memory 拼历史。

**修法**：`git init` → **先写 `.gitignore` 再 `git add`**（顺序反了会把凭证写进第一个 commit）。

`.gitignore` 必须覆盖：

```gitignore
# 凭证与运行时状态
config.json
state/
logs/
*.tmp
*.bak-*

# 环境
.venv/
__pycache__/
*.pyc
*.pyo

# 编辑器 / 系统
.vscode/
.idea/
.DS_Store
Thumbs.db

# 本机 AI 工具目录（含内部笔记与真实模板 ID）
.workbuddy-ai/
.codebuddy/
```

#### P0-3 无 LICENSE

法律默认「保留所有权利」。别人 clone 了也不能用、不能改、不能分发。
README 通篇讲「开源后照做必踩坑」，但没有许可证，等于开源不成立。

**修法**：MIT（最匹配本项目定位：个人工具、希望被复用到其他宿主）。

#### P0-4 无依赖声明

`.venv` 里有 `pystray 0.19.5` / `pillow 13.0.0`，但仓库没有任何文件记录这件事。
克隆者按 README 走，第一步 `python ui.py` 就可能因缺 PIL 崩在托盘图标上。

**修法**：`requirements.txt`（含 `pystray` / `pillow` 并标注「仅托盘需要」）
+ `pyproject.toml`（`[project]` 元数据、`requires-python = ">=3.10"`、
`[project.optional-dependencies] tray = [...]`、`[tool.ruff]` 配置）。

#### P0-5 README 假设 `.venv` 已存在

第 309 行：`当前 .venv 已就绪：D:\Code\Work buddy 通知\.venv\Scripts\python.exe`

克隆者的机器上没有这个 `.venv`。文档第一步就断，后续所有 `.venv\Scripts\python.exe`
命令全部不可执行。

**修法**：新增「快速开始（从零）」章节，用 `python -m venv .venv` 起步，
后续命令改用 `.venv/Scripts/python`（相对路径）或直接 `python`。

#### P0-6 README 事实性错误 3 处

| 位置 | 问题 |
|---|---|
| L336 / L339 | `theme.py` 在文件结构里列了**两次**（后一条写成「设计令牌 + 对比度计算」） |
| L309 / L316 / L577 | 硬编码绝对路径 `D:\Code\Work buddy 通知`，克隆者路径不同 |
| L11 / L221 | 同一张 `assets/ui_channels_light.png` 被引用两次（开篇一张、界面章节一张），内容重复 |

> 已核对无误、不要误改的项：「内置 10 个通道」与 `config.py` 的
> `DEFAULT_CONFIG["channels"]` 实际 10 项完全一致（wxmp / wecom_app / bark / ntfy /
> wecom_bot / dingtalk_bot / feishu_bot / smtp / webhook / log）；5 张截图引用路径全部存在。

---

### P1 · 可交付质量（影响维护与信任）

#### P1-1 零自动化测试

`selftest.py` 547 行、0 个 `assert`、0 个 pytest 用例。它是**手工 CLI 工具**，
输出要人读。README 8.1 节把它称作「回归防线」，实际是一道需要人肉执行的防线 ——
CI 里跑不了，重构时也不会自动报警。

最有价值的三个待测面（全部是纯函数，好测）：

| 目标 | 现有实现 | 测试价值 |
|---|---|---|
| `channels._wrap_detail` / `_take_width` / `_char_w` | 12 项手工检查 | 折行是最易回归的排版逻辑 |
| `sanitize.clean_text` / `clean_obj` | 4 项手工检查 | 编码 bug 曾让整条链路静默失效 |
| `events.classify` | 无独立检查 | 语义归类错误会直接推错通知类型 |

**修法**：新建 `tests/`，把 `check_encoding` / `check_wrap` 的用例逐条改写成
`assert`；`classify` 补参数化用例（覆盖 idle_prompt 过滤、问句归类）。

#### P1-2 零 CI

无 `.github/workflows/`。P0/P1 的所有检查（compileall、import、pytest、ruff）
目前全靠人记。

**修法**：单 workflow，矩阵 `windows-latest` / `ubuntu-latest` × Python 3.10 / 3.12 / 3.13。
Windows 跑全量，Linux 只跑 `pytest`（GUI 与 hooks 相关跳过）。

#### P1-3 无静态检查配置

无 ruff / flake8 / black 配置。当前已发现 2 处未使用 import：
`audit.py:19` 的 `channels`、`tray.py:24` 的 `store`。

#### P1-4 三个巨型文件

| 文件 | 行数 | 问题 |
|---|---|---|
| `ui.py` | 1811 | `App` 类 57 个方法 / 1675 行；`_channel_form` 151 行、`_page_guide` 128 行、`_update_preview` 113 行 |
| `wbn/widgets.py` | 2151 | 26 个控件类混在一个文件，`draw_icon` 单函数 159 行 |
| `wbn/channels.py` | 1405 | 10 个通道 + 微信模板消息（约 700 行）耦合 |

**修法（按收益排序）**：
1. `channels.py` 拆出 `wbn/wechat.py`（模板解析 / 折行 / 诊断，约 700 行纯逻辑）
   —— 这是唯一有明确边界、且拆完能独立测试的。
2. `ui.py` 按页拆成 `wbn/pages/`（channels / triggers / template / status / guide），
   `App` 只留生命周期与保存逻辑。
3. `widgets.py` 保持现状（控件库集中反而好找），只把 `draw_icon` 的图标路径表
   外置成数据。

#### P1-5 版本号未暴露

`wbn/__init__.py` 有 `__version__ = "1.0.0"`，但无 `--version` 入口、无 CHANGELOG。
用户报 bug 时无法确认版本。

#### P1-6 `install.py` 把解释器绝对路径写死进 hooks

`hook_command()` 用 `sys.executable` 生成绝对路径写入 `settings.json`。
用户重建 venv 或移动项目目录后，hooks 全部静默失效 —— 且症状是「通知突然不来了」，
极难定位（README 8.1 的排查路径里没有这一条）。

**修法**：`install.py status` 增加「hook 命令里的解释器是否仍存在」检查并显式告警。

#### P1-7 无 SECURITY.md

README 第五章「安全设计」写得很好，但不在 GitHub 识别的标准位置。
安全研究者不会翻 README 找漏洞上报方式。

---

### P2 · 体验与传播（按需）

| 项 | 说明 |
|---|---|
| 开发脚本混在产品根目录 | `audit.py` / `shot.py` / `make_icon.py` 应移入 `tools/` |
| 无 `.github/ISSUE_TEMPLATE` / PR 模板 | 开源后 issue 质量会失控 |
| 无 CONTRIBUTING.md | 缺「改完必须跑 audit + selftest --encoding」这条硬规矩 |
| 无英文 README | 影响国际曝光，可选 |
| 无分发方式 | 无 zip 打包脚本、无 winget、无 PyPI |
| 平台声明不显式 | 代码里有 `os.name != "nt"` 分支，但 README 只说 Windows，未说明非 Windows 支持程度 |
| 日志含隐私 | `logs/notifications.log` 含主机名「雨下」与真实项目名 —— 已在 `.gitignore` 覆盖，但卸载文档应提示删除 |

---

## 四、改进路线图

### 阶段 1 · 止血（半天，必须最先做）

| # | 动作 | 完成判据 |
|---|---|---|
| 1 | 测试号后台**重置 appsecret** | 旧 secret 调接口返回 40125 |
| 2 | 写 `.gitignore`（覆盖 P0-2 清单） | `git status` 里看不到 config.json / state / logs / .venv |
| 3 | `git init` + 首个 commit | 提交内容里 grep 真实 appid 前缀无结果 |
| 4 | 生成 `config.example.json` | 全空值，字段名完整 |
| 5 | 加 LICENSE (MIT) | GitHub 识别为 MIT |

### 阶段 2 · 可安装（1 天）

| # | 动作 | 完成判据 |
|---|---|---|
| 6 | `requirements.txt` + `pyproject.toml` | 全新目录 `pip install -e .` 成功 |
| 7 | README 加「快速开始（从零）」 | 在一台干净机器/新目录按文档走能打开设置中心 |
| 8 | 修 README 3 处错误（重复 theme.py / 绝对路径） | `grep -c "D:\\\\Code" README.md` = 0 |
| 9 | `--version` + CHANGELOG.md | `python selftest.py --version` 输出 1.0.0 |

### 阶段 3 · 可维护（2-3 天）

| # | 动作 | 完成判据 |
|---|---|---|
| 10 | `tests/` + pytest，覆盖折行 / 编码 / 事件归类 | `pytest -q` 全绿，用例 ≥ 25 |
| 11 | ruff 配置 + 修 2 处未使用 import | `ruff check .` 零 error |
| 12 | GitHub Actions（3 OS × 3 Python） | PR 上 CI 全绿 |
| 13 | `channels.py` 拆出 `wechat.py` | `channels.py` < 750 行，`wechat.py` 可独立测 |
| 14 | `install.py status` 检测解释器失效 | 手动改坏 settings.json 里的路径后能报出来 |

### 阶段 4 · 可传播（按需）

| # | 动作 |
|---|---|
| 15 | `tools/` 归置开发脚本，README 同步 |
| 16 | SECURITY.md（提炼 README 第五章） |
| 17 | CONTRIBUTING.md（含「改完必须跑 audit + pytest」） |
| 18 | `.github/ISSUE_TEMPLATE`（bug_report 含「贴 `selftest --status` 输出」） |
| 19 | `ui.py` 按页拆分 |
| 20 | 英文 README（可选） |

---

## 五、本机 Skill 盘点与匹配度

已扫描 `~/.workbuddy-ai/skills/` 与插件缓存，与本项目相关的如下。

### 5.1 高价值（直接用于本轮改进）

| Skill | 路径 | 用在哪 | 效果 |
|---|---|---|---|
| `tdd` | `skills/tdd/SKILL.md` | 阶段 3-10：把 selftest 改造成 pytest | 测试从「人读输出」变成「CI 可判定」 |
| `ci-cd-and-automation` | `skills/ci-cd-and-automation/SKILL.md` | 阶段 3-12：GitHub Actions | 每次 PR 自动跑 lint + test + compileall |
| `code-review-and-quality` | `skills/code-review-and-quality/SKILL.md` | 五轴审查（正确性/可读性/架构/安全/性能） | 输出可执行的改进清单，不是感想 |
| `ponytail-audit` | `skills/ponytail-audit/SKILL.md` | 全仓过度设计审计 | 找出能删的代码，9319 行有压缩空间 |
| `documentation-and-adrs` | `skills/documentation-and-adrs/SKILL.md` | README 拆分 + ADR | 把 738 行 README 拆成 README + docs/ |
| `api-and-interface-design` | `skills/api-and-interface-design/SKILL.md` | `channels.py` 拆分的接口设计 | 拆分后模块边界稳定，不再回滚 |

### 5.2 设计审查（用户指定，用于 UI 复核）

| Skill | 路径 | 定位 |
|---|---|---|
| `frontend-design` | `skills/frontend-design/SKILL.md` | 提供审美方向与反模板化判断 |
| `impeccable` | `skills/impeccable/SKILL.md` | 可访问性 / 交互 / 动画 / 响应式审查 |
| `brand-guidelines` | `plugins/cache/codebuddy-plugins-official/brand-guidelines/unknown/SKILL.md` | Anthropic 品牌规范 |

> ⚠️ **brand-guidelines 有一个已知冲突，必须在提示词里写明**：
> 它是 Anthropic 品牌（奶油底 `#faf9f5` + 陶土橙 `#d97757`），与项目既定的
> WorkBuddy 品牌绿 `#00c29a` 直接冲突。第 4 轮迭代已确认过这个取舍：
> **只借它的「主色/强调色/字体角色」三层克制结构做审查镜片，不换色。**
> 如果提示词里不写这条，下一个 agent 很可能把整个界面刷成橙奶油色。

### 5.3 中价值（辅助）

| Skill | 用途 |
|---|---|
| `humanizer` | README 文案去 AI 味（当前 README 有较多「不是X，而是Y」句式与加粗标签） |
| `self-improvement` | 把 12 轮迭代的踩坑记录沉淀成可复用流程 |
| `workbuddy-docx` | 若需把本报告导出为 Word 交付 |

### 5.4 不适用（明确排除，避免误用）

| Skill | 原因 |
|---|---|
| `windows-gui-installer-automation` | 用于驱动第三方安装向导，本项目是自己写安装器 |
| `geo-map-compliance-guard` | 无地图/位置功能 |
| `yuketang-auto-answer` | 无关 |
| `pdf` / `pdfkit-py` / `workbuddy-pptx` | 无对应产出（除非要出报告） |
| `caveman` / `grilling` / `computer-use` | 风格类或桌面操作类，与本任务无关 |
| `github-skill-install` | 用于装 skill，不是装项目 |

---

## 六、后续改进提示词

以下每条可直接粘贴到新对话。均写明 **skill + 路径 + 具体动作 + 明确验收效果**。

### 提示词 1 · 止血：凭证剥离与仓库初始化

```
你是本项目（D:\Code\Work buddy 通知，一个 WorkBuddy/CodeBuddy 任务通知工具）的
发布工程师。目标：把当前目录变成一个可以安全公开的 git 仓库。

现状（已核实）：
- 没有 git 仓库
- config.json 含真实 appid / appsecret / openid
- state/token_cache.json 含真实 access_token
- state/wxmp_template.json 含真实模板 ID
- .venv 占 30MB，state/sessions/ 有 40 个运行时文件

按以下顺序执行，顺序不能改：

1. 先写 .gitignore。必须覆盖：config.json、state/、logs/、*.tmp、*.bak-*、
   .venv/、__pycache__/、*.pyc、.vscode/、.idea/、.workbuddy-ai/、.codebuddy/
2. 再 git init。先 add 后写 ignore 会把凭证写进第一个 commit，绝对不许。
3. 生成 config.example.json：字段名与 config.py 的 DEFAULT_CONFIG 完全一致，
   所有凭证字段为空字符串，通道 enabled 全为 false，只保留 log 通道为 true。
4. 在 state/ 和 logs/ 下各放一个 .gitkeep。
5. 加 LICENSE（MIT，作者留空待用户填）。
6. 首个 commit。

验收（必须逐条实测并贴输出）：
- git status 里看不到 config.json / state / logs / .venv
- git ls-files | grep -E "config\.json|state/|logs/|\.venv" 为空
- git log --oneline 只有一条，且 git show --stat 里没有凭证文件
- 全文 grep 真实 appid / appsecret / openid 前缀在 git 跟踪文件里零命中

最后明确告诉我：appsecret 必须去测试号后台重置，删文件不等于止血。
```

### 提示词 2 · 测试体系（TDD）

```
使用 @skill:"C:\Users\XingHan\.workbuddy-ai\skills\tdd\SKILL.md"
把 D:\Code\Work buddy 通知 的手工自检改造成 pytest 测试套件。

背景：selftest.py 有 547 行、0 个 assert，是给人读输出用的 CLI 工具。
README 8.1 节把它当「回归防线」，但它无法在 CI 里判定成败。

核心任务（按此顺序，每个模块先写测试再改代码）：
1. tests/test_wrap.py —— 覆盖 channels._char_w / _take_width / _wrap_detail。
   现有 selftest 的 check_wrap() 有 12 项检查，逐条改成 assert。
   必须覆盖的边界：行宽不溢出、行首不出标点、英文单词不被劈开
   （sys.stdin.buffer 不能变成 sys.stdin.bu + ffer）、省略号只在末行、
   空串不出空行、多槽位切分不重复不丢失。
2. tests/test_sanitize.py —— 覆盖 clean_text / clean_obj。
   必须覆盖：落单代理字符能按 surrogateescape 反推还原中文、
   正常中文与 emoji 幂等不变、受损字节可还原、redact 能掩掉 URL 里的 key。
3. tests/test_events.py —— 覆盖 events.classify。
   必须覆盖：Stop → task_done、StopFailure → task_failed、
   permission_prompt → need_input、idle_prompt 必须被过滤不通知、
   auth_success 不通知、Stop 后最后一句是问句时归为 need_input。
4. tests/test_filters.py —— 静默时段跨零点、去重窗口、频率闸门、项目黑白名单。

约束：
- 全部测试不得联网、不得写真实 config.json、不得发通知。
  用 tmp_path + monkeypatch 隔离 config.STATE_DIR / LOG_DIR。
- 不重构业务代码，除非为了可测性必须抽参数（改动要单独 commit）。

验收：pytest -q 全绿，用例数 ≥ 25；故意把 _wrap_detail 的 width 改成 999
后测试必须变红（证明测试真的有效，不是摆设）。
```

### 提示词 3 · CI 与静态检查

```
使用 @skill:"C:\Users\XingHan\.workbuddy-ai\skills\ci-cd-and-automation\SKILL.md"
为 D:\Code\Work buddy 通知 建立 GitHub Actions 流水线。

项目事实：纯 Python，核心链路只用标准库；pystray + pillow 仅托盘需要；
Windows 专有（DWM 标题栏、icacls、SPI_GETCLIENTAREAANIMATION）；
21 个 py 文件 / 9319 行。

要求：
1. .github/workflows/ci.yml，矩阵 windows-latest + ubuntu-latest ×
   Python 3.10 / 3.12 / 3.13。
2. 检查项按「快 → 慢」排，失败即停：
   ruff check → python -m compileall → pytest。
   Windows 跑全量；ubuntu 只跑 pytest，跳过 GUI 与 hooks 相关用例
   （用 pytest.mark.skipif 按 sys.platform 跳过，不要用 -k 排除）。
3. pyproject.toml 里配 [tool.ruff]：line-length 100，
   启用 E/F/W/I/UP/B 规则集，忽略 E501（本项目长注释多）。
4. 顺手修掉两处未使用 import：audit.py:19 的 channels、tray.py:24 的 store。
   改前先跑 ruff 确认，改后确认 audit.py 仍能跑通。
5. 加 [tool.pytest.ini_options]，标记 gui / network 两类，默认跳过 network。

验收：本地 ruff check . 零 error；pytest -q 全绿；
workflow 文件通过 actionlint 或 act 语法校验；
在 PR 上能看到 6 个 job（2 OS × 3 Python）。
```

### 提示词 4 · 全仓代码审查

```
使用 @skill:"C:\Users\XingHan\.workbuddy-ai\skills\code-review-and-quality\SKILL.md"
对 D:\Code\Work buddy 通知 做一次全仓五轴审查（正确性 / 可读性 / 架构 / 安全 / 性能）。

项目背景：个人用的任务通知工具，通过 hooks 挂到 WorkBuddy / CodeBuddy，
任务结束时推送到微信。9319 行，12 轮迭代，功能已跑通。

重点审查以下已知薄弱点，但不要只看这些：
1. hook.py 的 fail-open 契约是否真的没有漏洞：
   有没有路径会在异常时写 stdout、非零退出、或阻塞超过 3 秒？
   （SessionStart / UserPromptSubmit 的 stdout 会被注入模型上下文，多一个字都是污染）
2. channels.py 的 10 个通道是否有统一的错误语义：
   超时、HTTP 4xx/5xx、业务错误码（微信 errcode）、凭证失效，
   四类失败是否都被区分并给出了可操作的原因？
3. store.py 的并发安全：hook 可能被多个会话同时触发，
   runtime.json / queue.jsonl 的读改写有没有竞态？atomic write 是否覆盖全部写入点？
4. ui.py 的 57 个方法里，有哪些是明显重复的模板代码可以抽函数？
5. 安全：sanitize 的 redact 正则有没有漏网（比如 URL query 里的短 key、
   Authorization header）？

输出格式：按严重度分 P0/P1/P2，每条给「文件:行号 + 问题 + 具体修法 + 为什么现在要修」。
不要给泛泛的「建议加注释」。能删的代码比能加的代码更值得提。
明确标注哪些问题属于「不改也能上线」，哪些是「必须改」。
```

### 提示词 5 · 过度设计审计

```
使用 @skill:"C:\Users\XingHan\.workbuddy-ai\skills\ponytail-audit\SKILL.md"
审计 D:\Code\Work buddy 通知 的过度设计。

项目背景：个人工具，单用户，Windows 本机，零服务器。
9319 行 Python，其中 widgets.py 2151 行（26 个自绘控件）、ui.py 1811 行。

我怀疑的点（请验证，不要预设结论）：
1. widgets.py 26 个控件是否都用到了？有没有建了没接进界面的？
   （重点查 Toast / EmptyState / StatTile / Metric / MonoRow / Pill）
2. theme.py 364 行里，设计令牌体系是否过度工程？
   （拆 brand / brand_ink 是有实测依据的，那部分保留）
3. channels.py 里微信模板消息相关约 700 行：
   折行、标签宽度计算、多槽位切分、诊断 —— 有多少是真实需求驱动的，
   有多少是为了应对一个已经被推翻的假设（\n 换行方案）留下的残余？
   注意：detail/detail2/detail3/detail4 多变量方案是实测有效的，不要误删。
4. selftest.py 547 行是否有和 audit.py 重复的检查？
5. config.py 的 deep_merge + _merge_channels 双套合并逻辑是否必要？

输出：一行一条，格式「位置 | 删什么 | 用什么替代 | 预计减少行数」。
按减少行数从大到小排。明确区分「可以删」和「看着冗余但删了会坏」——
后者要写清为什么不能删。最后给一个总账：理论上能砍掉多少行。
```

### 提示词 6 · 模块拆分

```
使用 @skill:"C:\Users\XingHan\.workbuddy-ai\skills\api-and-interface-design\SKILL.md"
把 D:\Code\Work buddy 通知 的 wbn/channels.py（1405 行）拆成可独立测试的模块。

现状：这个文件混了三类东西
- 10 个推送通道的发送实现（bark / ntfy / wecom / dingtalk / feishu / smtp / webhook / log）
- 微信测试号专属逻辑（token 缓存 / 模板解析 / 折行 / 诊断，约 700 行）
- 通道元数据表（CHANNEL_META，驱动界面渲染）

目标结构：
- wbn/channels/__init__.py —— 对外只暴露 send_one / send_all / is_ready /
  missing_fields / has_remote_channel / auto_enable / CHANNEL_META
- wbn/channels/base.py —— 通道注册表 + 统一返回契约 (ok, msg)
- wbn/channels/wechat.py —— 微信测试号全部逻辑（含折行、模板、诊断）
- wbn/channels/simple.py —— 其余 8 个通道

硬约束（违反即失败）：
1. 对外接口一个都不能变。ui.py / pipeline.py / selftest.py / audit.py
   里所有 channels.xxx 调用必须零改动跑通。
2. 折行逻辑（_char_w / _take_width / _wrap_detail）行为必须逐字节一致 ——
   拆分前先录一组输入输出快照，拆分后比对，必须完全相同。
3. 不动配置结构。config.json 里的通道字段名不能变，否则老用户配置全废。
4. 每拆一步跑一次：pytest -q、selftest.py --encoding、audit.py。
   三步全绿才继续下一步。

验收：channels/ 下每个文件 < 500 行；
grep -rn "channels\." 在 ui.py / pipeline.py / selftest.py / audit.py 里零改动；
wechat.py 的折行函数能在完全不启动 GUI、不联网的情况下被测试。
```

### 提示词 7 · 设计复核（用户指定的三 skill 组合）

```
使用 @skill:"C:\Users\XingHan\.workbuddy-ai\skills\frontend-design\SKILL.md"
@skill:"C:\Users\XingHan\.workbuddy-ai\skills\impeccable\SKILL.md"
重新审视 D:\Code\Work buddy 通知 的设置中心界面（ui.py + wbn/widgets.py + wbn/theme.py），
明确用户需求、核心任务、内容特点。所有设计元素都要和内容产生联系，同时保留现有功能。

背景（第 4 轮已确立，不要推翻）：
- 产品真正的内容不是「一堆设置项」，而是一条链路：事件 → 过滤 → 渲染 → 通道 → 手机
- 左侧导航是这根链路的竖脊，5 个页面是脊上节点，切页时有一个点沿脊滑过去
- 顶栏不是页面标题，而是一句陈述：「任务完成或失败时，经微信测试号，送到你手机」
- 刻意去卡片化：主内容铺在页面底，用「节标题 + 向右延伸的细线」分层级
- 设计令牌已定：ink / paper / surface / signal / brand_ink / rule / mute / border_control
- 品牌绿 #00c29a 对比度只有 2.11:1，只能当填充，所有绿色文字必须走 brand_ink #007a63

本次任务不是重做，是复核 + 修正。重点：
1. 用 frontend-design 的判据检查有没有残留的模板化痕迹：
   渐变装饰、无意义的胶囊标签、为了动效而动的动效。
2. 用 impeccable 逐项过可访问性、交互、动画、响应式四类问题。
3. 每个修改都要能回答「它和这条链路有什么关系」，答不上来就不改。

然后使用
@skill:"C:\Users\XingHan\.workbuddy-ai\plugins\cache\codebuddy-plugins-official\brand-guidelines\unknown\SKILL.md"
做交叉审查，检查可访问性、交互、动画、响应式问题。

⚠️ brand-guidelines 是 Anthropic 品牌规范（奶油底 #faf9f5 + 陶土橙 #d97757），
与本项目既定的 WorkBuddy 品牌绿直接冲突。**只借它「主色 / 强调色 / 字体角色」
三层克制的结构纪律来校准调色板，绝对不要换色。** 把它当审查镜片，不当配色来源。
如果你认为应该换色，先停下来问我，不要直接改。

验收（必须全部实测，不接受「看起来没问题」）：
- python audit.py 全绿（对比度逐项实测 / 键盘可达 / 减弱动效 / 三档宽度无溢出）
- 浅色深色各 13 项文字对比度 ≥ 4.5:1，控件边界 ≥ 3:1
- 940×640 最小窗口下无横向溢出、无文字被裁
- Tab 能遍历所有可操作控件，空格/回车可激活，焦点有可见反馈
- 关掉 Windows「显示动画」后所有补间直接跳终点
- 改完用 WBN_SCRUB=1 重拍 assets/ 下五张截图，逐张确认无真实凭证
```

### 提示词 8 · 文档重构与 ADR

```
使用 @skill:"C:\Users\XingHan\.workbuddy-ai\skills\documentation-and-adrs\SKILL.md"
重构 D:\Code\Work buddy 通知 的文档体系。

现状：README 738 行，从架构原理、方案选型、界面设计、安全设计、配置步骤、
排错、卸载、已知限制全塞在一个文件里。信息质量高，但读者找不到入口 ——
想装的人要翻过 200 行设计哲学才看到「步骤 1」。

任务：
1. README 压到 200 行以内，只留：一句话定位、5 张截图、快速开始（从零）、
   通道选择表、常见问题、指向 docs/ 的链接。
2. 拆出 docs/：
   - docs/architecture.md —— 原第一、二章（hooks 原理、架构、为什么用管道）
   - docs/design.md —— 原 2.4 节（设计令牌、链路语汇、无障碍）
   - docs/security.md —— 原第五章（同时作为 SECURITY.md 的内容源）
   - docs/troubleshooting.md —— 原第八章（含 8.1 的编码坑完整复盘）
   - docs/channels/ —— 每个通道一页，含申请步骤与踩坑
3. 写 docs/adr/，至少 5 条 ADR，记录「为什么这样选」而不是「选了什么」：
   - ADR-001 用 hooks 而不是轮询日志
   - ADR-002 用 stdin 管道而不是临时文件（附 2.02s → 0.07s 实测）
   - ADR-003 用微信测试号而不是第三方推送平台
   - ADR-004 品牌绿拆成 brand / brand_ink 两个令牌（附 2.11:1 实测）
   - ADR-005 多变量分段而不是 \n 换行（附新版卡片会吞掉 \n 之后内容的实测）
4. 修掉三处事实错误：文件结构里 theme.py 重复列了两次；
   三处硬编码绝对路径 D:\Code\Work buddy 通知 改成相对路径；
   「.venv 已就绪」改成从零创建的命令。

然后使用 @skill:"C:\Users\XingHan\.workbuddy-ai\skills\humanizer\SKILL.md"
过一遍新写的 README 与 docs。当前文档有明显的 AI 味：
大量「不是X，而是Y」对仗、加粗标签开头、每节都用一句金句收尾。
改成正常人写技术文档的语气，但不要丢掉那些实测数据 —— 数据是这份文档最值钱的部分。

验收：README < 200 行且能在 3 分钟内让新人打开设置中心；
docs/ 下每个文件 < 300 行；ADR 每条都有「背景 / 决策 / 代价 / 备选方案」四段；
grep -c "D:\\\\Code" README.md 结果为 0。
```

---

## 七、验收标准与检查清单

### 7.1 分级验收标准

| 级别 | 定义 | 不达标后果 |
|---|---|---|
| **A · 阻断** | 不满足则不得公开 | 凭证泄露、法律风险、clone 后跑不起来 |
| **B · 交付** | 不满足则质量不达标 | 维护成本高、bug 无法回归、贡献者不敢改 |
| **C · 体验** | 不满足则传播受限 | 曝光低、issue 质量差 |

### 7.2 A 级检查清单（全部勾选才能 push 到公开仓库）

- [ ] appsecret 已在测试号后台**重置**（不是只删文件）
- [ ] `git ls-files` 中不含 `config.json`
- [ ] `git ls-files` 中不含 `state/` 下任何文件
- [ ] `git ls-files` 中不含 `logs/` 下任何文件
- [ ] `git ls-files` 中不含 `.venv/`、`__pycache__/`、`*.pyc`
- [ ] `git ls-files` 中不含 `.workbuddy-ai/`
- [ ] 对 git 跟踪的全部文件 grep 真实 appid / access_token / 模板 ID 前缀零命中
- [ ] `assets/` 五张截图逐张目视确认无真实 appid / secret / openid / 模板 ID
- [ ] `git log -p` 首个 commit 中无凭证（若已误提交，需 filter-repo 重写历史）
- [ ] LICENSE 存在且 GitHub 侧栏识别为 MIT
- [ ] `config.example.json` 存在，所有凭证字段为空
- [ ] `requirements.txt` 存在，列出 pystray / pillow 并标注「仅托盘需要」
- [ ] `pyproject.toml` 存在，`requires-python = ">=3.10"`
- [ ] 在一台**没有 .venv** 的机器或新目录，按 README 从零走能打开设置中心
- [ ] `grep -c "D:\\\\Code" README.md` 结果为 0

### 7.3 B 级检查清单（可交付质量）

- [ ] `pytest -q` 全绿，用例数 ≥ 25
- [ ] 故意破坏 `_wrap_detail` 后测试变红（证明测试有效）
- [ ] `ruff check .` 零 error
- [ ] `python -m compileall` 21 个文件全通过
- [ ] `python -c "import wbn.{config,events,filters,render,channels,sanitize,store,pipeline,theme,widgets,anim}"` 全部成功
- [ ] GitHub Actions 6 个 job（2 OS × 3 Python）全绿
- [ ] `selftest.py --status` 显示 hooks 已挂载
- [ ] `selftest.py --encoding` 全绿
- [ ] `audit.py` 全绿
- [ ] `selftest.py --version` 输出与 `wbn.__version__` 一致
- [ ] CHANGELOG.md 有 1.0.0 条目
- [ ] `install.py status` 能检测出「hook 命令里的解释器已不存在」
- [ ] `channels.py` < 750 行（拆分后），折行函数可离线单测
- [ ] SECURITY.md 存在，含漏洞上报方式
- [ ] CONTRIBUTING.md 含硬规矩：「改完必须跑 audit.py + pytest」

### 7.4 C 级检查清单（体验与传播）

- [ ] README < 200 行，3 分钟内能让新人打开设置中心
- [ ] `docs/` 下至少 4 个文件（architecture / design / security / troubleshooting）
- [ ] `docs/adr/` 至少 5 条 ADR，每条含背景 / 决策 / 代价 / 备选方案
- [ ] `.github/ISSUE_TEMPLATE/bug_report.md` 要求贴 `selftest --status` 输出
- [ ] `.github/pull_request_template.md` 含自检勾选项
- [ ] `tools/` 归置 audit.py / shot.py / make_icon.py，README 同步
- [ ] 平台支持声明显式（Windows 10/11 完整支持；其他平台哪些功能降级）
- [ ] 卸载文档提示手动删除 `logs/`（含主机名与项目名）

### 7.5 最终交付判据

三条同时成立，才算达到可交付质量：

1. **安全**：新克隆的仓库里 grep 不到任何真实凭证，且旧 secret 已失效。
2. **可复现**：干净环境下按 README 走完能收到第一条微信通知。
3. **可回归**：任何人改坏折行或编码逻辑，CI 会在 3 分钟内变红。

---

## 附：本次审查未覆盖的部分

| 项 | 原因 |
|---|---|
| 微信 / 企业微信接口的实际可用性 | 需真实凭证，本次只读代码不调接口 |
| 托盘图标在各 DPI 下的实际观感 | 需人工目视，`make_icon.py` 生成逻辑已读但未逐档看图 |
| 深色模式下全部 5 个页面的视觉 | 只有 channels 页有深色截图 |
| 长时间运行的内存/文件句柄 | 需挂机测试，本次为静态审查 |
| 卸载后 settings.json 的残留 | `install.py strip_hooks` 逻辑已读，未实机验证 |
