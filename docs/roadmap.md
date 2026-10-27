# 全项目路线图

> 版本：v1 · 2026-10-27
> 角色：**项目经理（PM）只做规划、拆分、协调，不写实现代码。**
> 最终形态：**一个可双击运行的 Windows exe**（不是网页、不是 pip 包、不是"目录 + python"）。
> 提示词清单与执行顺序见 [`prompts/README.md`](prompts/README.md)。

---

## 0. 项目定位（这一条决定后面所有取舍）

| 项 | 定 |
|---|---|
| 形态 | 桌面程序，最终产物 **单个 .exe** |
| 用户 | 装了 WorkBuddy 的个人开发者，想让任务跑完时手机收到消息 |
| 核心链路 | `WorkBuddy 事件 → hook → dispatch → pipeline → 10 个通道 → 手机` |
| UI 的地位 | **附属品**。UI 挂了通知照样要发出去 |
| 分发 | 绿色便携，解压即用；配置和日志在 exe 旁边 |
| 不做 | 网页版、pip 包、自动更新、多用户、云同步 |

**"单 exe"这个约束的连锁反应**（比 UI 重构影响更大，必须先想清楚）：

1. 所有入口（hook / dispatch / tray / UI / install / selftest）合并成一个 exe，用子命令区分。
2. `ROOT = Path(__file__).parent.parent` **失效**——PyInstaller onefile 下 `__file__`
   指向临时解压目录，退出即删。配置和日志会写进 `%TEMP%` 然后消失。
3. WorkBuddy 里已配置的 hook 路径（指向 `python hook.py`）要迁移。
4. 现有用户升级时不能丢 `config.json`。

---

## 1. 角色与协作约定

| 角色 | 谁 | 做什么 | 不做什么 |
|---|---|---|---|
| PM | 我 | 拆任务、写提示词、定验收、维护依赖关系、在阶段之间做质检 | **不写实现代码**、不跑重构 |
| 执行者 | 每个提示词里的 AI | 按提示词实现、自测、汇报 | 不改提示词里的硬约束、不擅自扩范围 |
| 拍板人 | 你 | 定决策点（D1–D12）、验收每个阶段 | — |

**PM 的产出物只有四样**：提示词、路线图、决策记录、阶段验收单。
代码一律由执行者按提示词写。

---

## 2. 模块全量清单与去向（覆盖全部 12965 行）

### 2.1 入口层（根目录）

| 文件 | 行 | 现在干什么 | 去向 |
|---|---|---|---|
| `hook.py` | 215 | WorkBuddy 调它，读 stdin → 管道投给 dispatch | **改造**：支持 `通知.exe hook`；保留 `.py` 直跑兼容 |
| `dispatch.py` | 88 | 常驻派发进程 | **改造**：`通知.exe dispatch --stdin` |
| `tray.py` | 153 | 系统托盘 | **改造**：`open_ui()` 指向 app.py；打包后调自身 exe |
| `ui.py` | 2376 | tkinter 设置中心 | **删除**（逻辑先抽到 `insight.py`） |
| `install.py` | 419 | 装/卸 hook、建快捷方式、写配置 | **改造**：写 hook 配置时要能写 exe 路径 |
| `selftest.py` | 556 | 自检（编码/换行/微信/场景） | **改造**：加"打包环境"检查项 |
| **新增** `app.py` | ~150 | **统一入口**，子命令分发 + pywebview 窗口 | 新 |

### 2.2 业务层 `wbn/`（2860 行，基本不动）

| 文件 | 行 | 去向 |
|---|---|---|
| `channels.py` | 1403 | **保留**（W3 只加 `CHANNEL_GROUPS` 常量） |
| `config.py` | 325 | **必须改一处**：`ROOT` 支持 `sys.frozen`（见 §5 R2） |
| `events.py` | 275 | 保留 |
| `store.py` | 268 | 保留（路径跟随 config.ROOT） |
| `pipeline.py` | 200 | 保留 |
| `sanitize.py` | 147 | 保留 |
| `render.py` | 121 | 保留 |
| `filters.py` | 121 | 保留 |
| **新增** `insight.py` | ~300 | 从 ui.py 抽出的 309 行业务逻辑（W0） |
| **新增** `bridge.py` | ~250 | JS ↔ Python 的 API 层（W1） |

### 2.3 UI 层 `wbn/`（3708 行，全部作废）

| 文件 | 行 | 去向 |
|---|---|---|
| `widgets.py` | 2520 | 删除，换成 `webui/` |
| `theme.py` | 474 | 删除，色值搬进 `tokens.css` |
| `splash.py` | 332 | 删除（PyInstaller splash 或 web 加载态取代） |
| `tokens.py` | 202 | 删除，尺寸搬进 `tokens.css` |
| `anim.py` | 180 | 删除，改 CSS transition |

### 2.4 工具层 `tools/`

| 文件 | 行 | 去向 |
|---|---|---|
| `audit.py` | 335 | **删除**（检查 Tk 控件树，没有对象了） |
| `check_responsive.py` | 326 | **删除** |
| `check_style.py` | 172 | **删除** |
| `bench_ui.py` | 300 | **重写**：量"exe 启动 → 首屏可交互" |
| `make_icon.py` | 148 | **保留**（exe 图标 + 托盘图标都要它） |
| `shot.py` | 119 | **重写**：截 webview 窗口 |
| **新增** `tools/build_exe.py` | ~120 | PyInstaller 打包脚本（W7） |

### 2.5 测试与文档

| 资产 | 现状 | 去向 |
|---|---|---|
| `tests/` 7 个文件 | 170 passed | 保留；`test_platform.py` 里 `W.Switch` 那条删掉；新增 `test_insight.py`、`test_bridge.py`、`test_frozen_paths.py` |
| `docs/` 40 篇 | 完整 | 更新 architecture / README / troubleshooting；`archive/perf.md`、`archive/design-redesign.md` 已归档（C0 完成） |
| CI | 2 OS × 3 Python | 保留；**加一个 Windows 打包 job**（W7） |
| `CHANGELOG.md` | 有 | 每次阶段验收追加 |

---

## 3. 阶段划分与依赖关系

```
S0 已完成（2026-10-25 → 10-27）
  └─ 安全合规 · 质量基线 · 文档体系 · 目录整理 · tkinter UI 重做
        │
        ▼
S1 清场 ────────── C0
        │         （删垃圾 / 归档作废文档 / 废止 ADR-006）
        ▼
S2 逻辑与骨架 ──── W0 ──▶ W1
        │                （W1 依赖 W0 的 insight.py）
        ▼
S3 界面 ────────── W2 ──▶ W3 ──▶ W4
        │           （W3/W4 全部照抄 W2 定稿的 token）
        ▼
S4 下线与回归 ──── W5 ──▶ W6
        │           （W5 删代码前必须过 W6 的清单）
        ▼
S5 打包 ────────── W7
        │           （依赖 W5 删干净 + W6 全绿）
        ▼
S6 分发 ────────── W8
                    （依赖 W7 产出可运行 exe）
                    └─▶ P5 开源发布（可并行，只依赖 S0 的文档与 LICENSE）
```

### 3.1 每一步的目标、约束、依赖

| # | 阶段 | 目标（一句话） | 硬约束 | 依赖 | 验收门 |
|---|---|---|---|---|---|
| **C0** | 清场 | 删垃圾、归档作废文档、废止 ADR-006 | 只删/移，不改内容；用户数据不动 | S0 | 0 断链；`git log --follow` 历史未断；`*.py` 零改动 |
| **W0** | 基线锁定 + 逻辑抽取 | 打 `tkinter-final` 标签，把 309 行业务从 ui.py 抽到 `insight.py` | 业务层 8 个文件一行不改；tkinter 版仍能跑 | S0 | 新旧 `stats()` / `chain_sentence()` 输出**逐字节相同** |
| **W1** | 统一入口 + 桥 | `app.py` 能用、config 能读写、单实例生效 | 原生边框；`as_uri()`；`config_save` 读-改-写；第二个实例退出 | W0 | 中文路径不白屏；并发保存不丢字段；双开被拦 |
| **W2** | 设计系统 + 壳 + 状态页 | token 落地，壳搭好，状态页定稿成样板 | 颜色只从 token 取；间距只用 8 的倍数；不引框架/CDN | W1 | 对比度实测达标；880/1180/1440 三档不崩 |
| **W3** | 通道页 | 10 个通道 + schema 驱动表单 + 异步测试 | 表单从 `CHANNEL_META` 生成；`channel_test` 必须开线程 | W2 | 给 `CHANNEL_META` 加假字段 → 前端自动多出输入框 |
| **W4** | 其余三页 | 触发条件 / 消息模板 / 使用教程 | 预览必须调后端渲染；不许新增色值 | W3 | 预览与 tkinter 版**逐字一致** |
| **W5** | 收尾 + 旧栈下线 | 原生边框确认、删 6084 行、`tray.py` 改指向 | 删之前过 W6 清单；检查脚本删了要**换尺子** | W4 | 托盘能开新窗口；`grep` 无残留引用 |
| **W6** | 复核 | 12 条基线 + a11y + 性能 | 不许调阈值让检查通过 | W5（可先跑） | 12 条勾选表 + 遗留问题清单 |
| **W7** | 单文件打包 | `通知.exe` 一个文件，双击能用 | `ROOT` 支持 frozen；数据在 exe 旁；hook 路径可迁移 | W5 + W6 | 干净机器上双击能跑；配置重启不丢；hook 能触发 |
| **W8** | 发布与分发 | 版本号、更新日志、发布包、升级路径 | 升级不丢 `config.json`；回滚有据 | W7 | 从旧版升级后配置完整；卸载干净 |
| **P5** | 开源发布 | README/文档/许可证对齐 | 与代码栈无关 | S0 | 仓库可公开 |

---

## 4. 已完成进度（存量，不再重做）

| 时间 | 成果 | 证据 |
|---|---|---|
| 10-25 | 凭证从仓库剥离、`.gitignore`、LICENSE、SECURITY.md | 审查报告 01–04 |
| 10-26 | 测试体系 170 用例、CI（2 OS × 3 Python）、ruff 54→0 | `tests/`、`.github/workflows/ci.yml` |
| 10-26 | 文档重构：40 篇 md、7 篇 ADR、5 篇审查快照 | `docs/` |
| 10-27 | 目录整理：3 个脚本移入 `tools/`，根目录 `.py` 9→6 | `docs/repo-layout.md` |
| 10-27 | 功能基线 12 条（9 过 / 2 红 / 1 未验） | `docs/functional-baseline.md` |
| 10-27 | tkinter UI 全站重做：D1/D2 修复、启动层、切页 1–10ms | `docs/design-redesign.md` |
| 10-27 | 启动卡顿根因：`<Configure>` 自激 25243 → 335（75×） | `docs/perf.md` 4.4 |
| 10-27 | 开关重绘为真半圆胶囊、`Step` 圆点修正 | commit `295ad72` |

**这些成果里，S0 的安全/测试/CI/文档**在 exe 阶段全部复用**，不能因为换 UI 就作废。
tkinter UI 那部分（10-27 的三条）**只保留实测数据**，代码随 W5 删除。

---

## 5. 风险登记册

| # | 风险 | 影响 | 概率 | 应对 |
|---|---|---|---|---|
| **R1** | **PyInstaller onefile 启动慢 1–2 秒**（每次解压到 `%TEMP%`） | 与"启动要快"直接冲突 | 高 | 见 D10：onefile + splash，或 onedir + 单 exe 入口。**必须实测后拍板** |
| **R2** | `ROOT` 基于 `__file__`，frozen 后指向临时目录 | 配置/日志写进 `%TEMP%`，重启即丢 | **必现** | `config.py` 加 `sys.frozen` 分支；新增 `test_frozen_paths.py` |
| **R3** | hook 调用方式变更 | 已装用户的 WorkBuddy 配置指向 `python hook.py`，升级后失效 | 高 | `install.py` 提供 `migrate` 子命令；W8 做升级路径 |
| **R4** | 杀软误报 | 用户下载即被拦 | 中 | 无签名时 README 说明；可选自签证书（D12） |
| **R5** | WebView2 缺失 | 设置界面打不开 | 低 | 注册表探测 + 降级提示框（W1） |
| **R6** | 前端资源没打进 exe | 白屏 | 中 | PyInstaller `--add-data`；W7 用**干净机器**验证 |
| **R7** | 无代码签名 | SmartScreen 警告 | 高 | 文档说明"更多信息 → 仍要运行"；不做签名（D12） |
| **R8** | exe 放 `Program Files` 无写权限 | 存不了配置 | 中 | 优先 exe 同目录，不可写回落 `%LOCALAPPDATA%`（D11） |

---

## 6. 待拍板决策

完整版见 [`prompts/DECISIONS.md`](prompts/DECISIONS.md)。**D1–D3、D10–D12 不定，W0 之后不许继续。**

| # | 问题 | 推荐 |
|---|---|---|
| D1 | 保留 tkinter 版做降级？ | 全删，退路靠 git tag |
| D2 | 自绘窗口边框？ | 先不做，W5 实验 |
| D3 | Tailwind 产物入库 vs 手写 CSS？ | 手写 CSS |
| D4 | 深色默认 | 跟随系统 |
| D5 | 字体 | 打包 Inter 拉丁子集 |
| D6 | 何时删旧文件 | W5，回归通过后 |
| D7 | 作废提示词删不删 | 归档不删 |
| **D8** | ~~pip 安装包~~ → **已改判：做单 exe** | 单 exe，绿色便携 |
| D9 | 通知链路回归 | 每阶段 `selftest.py` + 真发一条 |
| **D10** | onefile 还是 onedir？ | **待实测**：onefile 慢 1–2s，onedir 不是一个文件 |
| **D11** | 数据放 exe 旁还是 `%LOCALAPPDATA%`？ | 优先 exe 旁，不可写回落 |
| **D12** | 要不要代码签名？ | 不签，文档说明 |

---

## 7. PM 的阶段质检清单（每个 W 结束我都要过一遍）

1. **范围**：改动文件是否都在白名单内？有超出的，问清楚再放行。
2. **证据**：验收是不是贴了实测数字/截图，还是只说"已完成"？
3. **回归**：`pytest` 是否仍全绿？通知链路是否仍通？
4. **诚实**：有没有把"未验"写成"✓"？有没有为了让检查通过而改检查器？
5. **依赖**：下一阶段依赖的东西是否真的就绪（比如 W3 依赖 W2 的 token 定稿）？

五条里任何一条不过，退回重做，不进下一阶段。
