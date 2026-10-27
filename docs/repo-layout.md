# 目录整理方案

> 状态：**已执行**（2026-10-27）。6.1 的三个文件已移入 `tools/`，7.2 的命令已同步；
> 6.2 的第 6 项（`docs/README.md`、`CONTRIBUTING.md`）还没做。
> 撰写于 2026-10-27，基于当时的工作区（含未提交改动）。

---

## 一、先说结论

根目录现在平铺着 **9 个 `.py`**，它们其实是 **5 类完全不同的东西**：产品入口、用户诊断、开发工具、库、配置。
而 `tools/` 里已经住着 3 个同类开发脚本——同一类东西被拆在两个地方放，这才是"乱"的根源。

整理不需要重构代码。**只做两件事**：

1. 把 3 个开发脚本从根目录挪进 `tools/`，并给它们补上和 `tools/` 现有脚本一致的路径引导。
2. 把 `docs/` 里"审查报告"和"提示词"两种体裁分家。

预计改动：**3 个文件各 2 行**，8 处文档命令引用，0 处用户可见变化。

---

## 二、现状：根目录的 9 个 .py 是什么

| 文件 | 真实角色 | 谁在调用 | 该不该在根 |
|---|---|---|---|
| `ui.py` | 产品入口（GUI） | 用户双击 / 快捷方式 | ✅ 必须在 |
| `hook.py` | 产品入口（宿主 hook 回调） | WorkBuddy / CodeBuddy 调用 | ✅ 必须在 |
| `dispatch.py` | 产品入口（事件分发） | `hook.py` | ✅ 必须在 |
| `tray.py` | 产品入口（托盘常驻） | 用户 | ✅ 必须在 |
| `install.py` | 产品入口（安装器） | 用户首次安装 | ✅ 必须在 |
| `selftest.py` | **面向用户的**诊断 CLI | README / troubleshooting 让用户敲 | ✅ 必须在 |
| `audit.py` | 开发期审查 | 开发者 | ❌ 应在 `tools/` |
| `shot.py` | 开发期截图 | 开发者 / 文档配图 | ❌ 应在 `tools/` |
| `make_icon.py` | 开发期资产生成 | 开发者 | ❌ 应在 `tools/` |

**关键区分**：`selftest.py` 和 `audit.py` 看着像同类（都是"检查脚本"），但读者不同。
`selftest.py` 是**产品的一部分**——文档让用户在出问题时敲它；`audit.py` 是**开发者的尺子**。
所以前者留根，后者进 `tools/`。这条界线如果不划清，下次还会有人把两个一起挪走。

## 三、问题清单（每条都有证据）

| # | 问题 | 证据 |
|---|---|---|
| 1 | 开发脚本混在产品根目录 | **已解决（2026-10-27）**：`audit.py` / `shot.py` / `make_icon.py` 已移入 `tools/`，根目录只剩 6 个 `.py`，全是产品入口 |
| 2 | 同类脚本分两处放 | **已解决（2026-10-27）**：`tools/` 现在是开发脚本的唯一出口，6 个脚本同处一处 |
| 3 | `docs/reviews/` 混了两种体裁 | **已解决（2026-10-27）**：`01–04`、`07` 是审查报告；原来混在里面的三份**提示词**已删除，内容并入 `docs/prompts/` |
| 4 | 提示词编号与执行进度脱节 | **已解决（2026-10-27）**：旧编号 1–9 换成 P0–P5 + A1/A2，`docs/prompts/README.md` 的「旧版去向」保留对照 |
| 5 | 根目录缺入口说明 | 还有 6 个 `.py` 平铺（已从 9 个降下来），新人仍不知道从哪个文件开始读 |
| 6 | 截图与图标混放 | `assets/` 里 `icon.*` 与 `ui_*.png` 同级（**低优先，可不动**） |

---

## 四、整理原则

1. **按"谁在用"分层，不按"文件类型"分层。**
   用户入口 / 开发者工具 / 库代码 / 文档 / 运行数据，五层。按 `.py`/`.md` 分层是假整齐。
2. **用户会敲的路径不能变长。**
   README 和 troubleshooting 里让用户跑 `python selftest.py`。一旦变成 `python tools/selftest.py`，
   所有文档、所有历史 issue 的说法都要改，收益为零。**入口只留短路径。**
3. **同类只放一处。**
   开发脚本的家只能是 `tools/`。有两个家就等于没有家。
4. **移动必须零行为改动。**
   只做「改路径 + 补 `sys.path` 引导」，不顺手改逻辑、不顺手格式化。
   一旦移动和修改混在一次提交里，出问题就无法二分定位。
5. **文档按体裁分家。**
   报告是"某天的快照"，提示词是"可复用的动作"，决策记录是"为什么这么定"。
   三者生命周期不同，混在一起必然一起过期。

---

## 五、目标结构

```
Work buddy 通知/
├─ README.md  LICENSE  CHANGELOG.md  SECURITY.md
├─ CONTRIBUTING.md                    ← 新增（旧验收清单里已要求）
├─ pyproject.toml  requirements.txt  requirements-dev.txt
├─ config.example.json  .gitignore  .gitattributes
│
├─ ui.py                              ← 产品入口（GUI）
├─ hook.py  dispatch.py  tray.py  install.py
├─ selftest.py                        ← 面向用户的诊断 CLI，路径固定
│
├─ wbn/                               ← 库
├─ tests/                             ← pytest
├─ tools/                             ← 所有开发期脚本，唯一出口
│   ├─ audit.py                       ← 移入
│   ├─ shot.py                        ← 移入
│   ├─ make_icon.py                   ← 移入
│   ├─ check_style.py  check_responsive.py  bench_ui.py
│
├─ docs/
│   ├─ README.md                      ← 新增：文档索引
│   ├─ architecture.md  security.md  troubleshooting.md
│   ├─ adr/                           ← 决策记录（ADR-007 已取代 006）
│   ├─ archive/                       ← C0：tkinter 时代的文档，只读不改
│   │   ├─ design.md  design-redesign.md  perf.md
│   │   ├─ ui-rebuild-webview-plan.md
│   │   └─ prompts-tkinter/           ← 旧 P0–P4（不再执行）
│   ├─ channels/                      ← 通道配置说明（不动）
│   ├─ prompts/                       ← 现行提示词（C0 + W0–W8 + P5）
│   │   ├─ README.md  DECISIONS.md
│   │   ├─ C0-清场与旧资产归档.md
│   │   ├─ W0…W8
│   │   └─ P5-开源发布.md
│   └─ reviews/                       ← 只留审查报告
│       ├─ README.md                  ← 补一段"05/06/08 已归档到 docs/prompts/"
│       └─ 01…04、07
│
├─ assets/
│   ├─ icon.ico  icon_256.png
│   └─ screenshots/                   ← 已移入：ui_*.png + 对照图（W6 要用）
├─ state/  logs/                      ← 运行数据，路径由 wbn/config.py 推导，不许动
└─ .github/workflows/ci.yml
```

---

## 六、移动清单

### 6.1 必做（代码移动）

| # | 原位置 | 现在 | 同时改的地方 |
|---|---|---|---|
| 1 | `audit.py` | ✅ `tools/audit.py` | ① 头部 `sys.path.insert(0, ".")` 换成 ROOT 引导<br>② 文档命令 |
| 2 | `shot.py` | ✅ `tools/shot.py` | ① 同上<br>② `docs/security.md` 的示例命令<br>③ 默认输出改成相对 ROOT（`str(ROOT / "assets" / "_shot.png")`），否则从 `tools/` 里跑会写进 `tools/assets/` |
| 3 | `make_icon.py` | ✅ `tools/make_icon.py` | ① `ROOT` 由 `parent` 改成 `parent.parent`<br>② `docs/troubleshooting.md`、`install.py` 注释<br>③ `install.py` 的 `import make_icon` 前补 `sys.path` |

**路径引导的标准写法**（`tools/` 现有 3 个脚本已经在用，照抄即可）：

```python
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
```

现在的 `sys.path.insert(0, ".")` 依赖「在项目根目录下执行」。挪进 `tools/` 后，
`python tools/audit.py` 的 `sys.path[0]` 变成 `tools/`，`import wbn` 会失败。
**这是这三个文件唯一的代码改动，各 3 行。**

### 6.2 文档归置

| # | 现在 | 处理 | 风险 |
|---|---|---|---|
| 4 | 旧提示词三份（`docs/reviews/` 下编号 05、06、08） | **已删除（2026-10-27）**。原计划移入 `_archive/` 留档，实际由 `docs/prompts/` 的 P0–P5 完整取代，留档没有读者 | 低 |
| 5 | `docs/reviews/README.md` | **已改**：表格只列剩下的 01–04、07，头部一句说明「提示词部分已迁出」并指向 `docs/prompts/` | 低 |
| 6 | — | 新增 `docs/README.md`（文档索引）、`CONTRIBUTING.md` | 无 |

### 6.3 可选（低优先，不做也不影响）

| # | 现在 | 移到 | 代价 |
|---|---|---|---|
| 7 | `assets/ui_*.png` 共 5 张 | `assets/screenshots/` | README 5 处图片链接要改 |

---

## 七、影响范围

### 7.1 代码影响

- 涉及文件：`audit.py`、`shot.py`、`make_icon.py`。
  **实际改动比预想的多一处**：`make_icon.py` 不 import `wbn`，所以它不需要
  `sys.path` 引导，但它的 `ROOT = Path(__file__).resolve().parent` 会跟着挪到
  `tools/`，导致图标写进 `tools/assets/`。这一行必须改成 `parent.parent`。
  `audit.py` 与 `shot.py` 各 3 行引导；`make_icon.py` 改 1 行。
- ⚠️ **修正（2026-10-27 复查）**：本节原先写「已确认没有任何模块 import 这三个文件」——**这个结论是错的**。

  `install.py:164` 有真实依赖：

  ```python
  def ensure_icon() -> Path | None:
      if ICON_PATH.exists():
          return ICON_PATH
      try:
          import make_icon          # ← 移走之后这里会 ImportError
      except Exception:
          return None               # ← 被静默吞掉
      try:
          make_icon.main()
      except Exception:
          return None
  ```

  **为什么这条最危险**：`import` 失败被 `except Exception: return None` 吞掉，
  于是搬完之后不报错、测试也不红，只是**新机器上图标不再自动生成**。
  典型的静默降级——查起来要花半天。

  改法（`install.py` 已经在算 `ROOT`，直接注入）：

  ```python
  sys.path.insert(0, str(ROOT / "tools"))
  import make_icon
  ```

- **`pyproject.toml` 有 3 条指向旧路径的死配置**：

  ```toml
  "audit.py" = ["T201"]
  "shot.py" = ["T201"]
  "make_icon.py" = ["T201"]
  ```

  改成 `"tools/audit.py"` 等（已改）。注意：`[tool.ruff.lint] select` 里**没有 `T`**
  （flake8-print），所以这 3 条本来就不生效——不改也不会报错。
  但留着就是 3 条指向不存在文件的配置，属于卫生问题，顺手改掉。
- `tests/` 不引用它们；`ci.yml` 只跑 `ruff` / `compileall` / `pytest`，不涉及。
- `wbn/` 包内无任何引用（只有注释提及）。

### 7.2 文档影响（命令已全部同步）

| 文件 | 行 | 原命令 → 现命令 |
|---|---|---|
| `README.md` | 71–73 | `selftest.py`（**不动**） |
| `docs/architecture.md` | 164–167 | 目录树里三行 → 已收进 `tools/` 一节 |
| `docs/archive/design.md` | 72 | `audit.py` → `tools/audit.py` |
| `docs/security.md` | 63 | `python shot.py` → `python tools/shot.py` |
| `docs/troubleshooting.md` | 94 | `python make_icon.py` → `python tools/make_icon.py` |
| `docs/adr/ADR-006` | 35 | `audit.py` → `tools/audit.py` |
| `docs/reviews/*` | 多处 | **历史快照，不改**（加一行说明即可） |
| `install.py` | 157 | 注释里的 `make_icon.py` → `tools/make_icon.py` |

上面 8 处已全部同步。另外还漏算了 4 处，一并改了：
`docs/adr/ADR-004`（2 处）、`SECURITY.md:40`、`requirements.txt:8`、
`docs/prompts/P0–P3` 里的 7 条 `python audit.py`（那几份提示词还没执行，命令必须是新的）。

### 7.3 用户影响

**零。** 这三个都不是用户入口，README 没有让用户跑过它们。

### 7.4 回滚

`git mv` 反向执行即可。没有数据格式、没有配置字段、没有 hook 协议变更。

---

## 八、明确不动的东西（附理由）

| 不动 | 理由 |
|---|---|
| `SECURITY.md` 留在根 | GitHub 只在这个位置识别安全策略，挪进 `docs/` 会失去入口 |
| `selftest.py` 留在根 | 用户命令，README + troubleshooting 共 12 处引用 |
| `config.example.json` 留在根 | README 指路「照着这个建 config.json」 |
| `state/`、`logs/` 留在根 | `wbn/config.py` 用 `ROOT = Path(__file__).parent.parent` 推导，挪了会让配置和日志写错位置 |
| `docs/adr/`、`docs/channels/` | 结构已经合理，不动 |
| `assets/` 里的图标 | 被 `install.py` / `tray.py` 按相对路径引用，不动 |

---

## 九、执行顺序（每步可独立回滚）

```
第 1 步  ✅ 只做 6.1 的 3 个文件移动 + 路径引导
         验收：pytest -q 170 passed；python tools/audit.py 全部通过；
               python tools/shot.py 能出图；python tools/make_icon.py 能出 ico
         失败就单独回滚这一步，不牵连后面

第 2 步  ✅ 同步 7.2 的文档命令
         验收：grep -rn "python audit\.py" --include=*.md . 只剩 docs/reviews/
               （历史快照）与本节自己引用的旧写法

第 3 步  做 6.2 的文档归置 + 新增 docs/README.md、CONTRIBUTING.md
         验收：docs/README.md 里每条链接都能打开

第 4 步（可选）assets 截图归入 screenshots/
         验收：README 里 5 张图仍能显示
```

**为什么分四步而不是一次做完**：第 1 步是唯一有代码改动的，必须单独成一次提交。
混进文档改动之后，万一 import 出问题，就得先剥掉文档才能定位。
