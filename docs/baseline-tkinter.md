# tkinter 版性能基线（最终态）

> **这是不可再生数据。** 采集于 W0（2026-10-27），代码状态 = tag `tkinter-final`。
> W5 删除 tkinter 代码后无法重新测量。W6 的新旧对照、W8 的发布说明都引用本文件。
>
> **与 [`archive/perf.md`](archive/perf.md) 的区别**：那一组是 P1/P2 时代的中途数据
> （还带着 5 页预热、`redesign-base` 的卡片结构、7 档字号）。本文件是**删掉预热之后、
> 抽出 `insight.py` 之后的最终态**。两组测的不是同一份代码，**W6 用本文件**。
>
> 本文件只记数字与事实。结论、优化建议属于 W6 的复核报告。

## 1. 环境

| 项 | 值 |
|---|---|
| 代码状态 | tag **`tkinter-final`**（解引用 = `f98ac11`）。采集时 `git describe --tags` = `tkinter-final-2-g2267c39`——**这个距离会随文档提交继续增长，别拿它当校验**。要校验代码是否与测量时一致，用 `git diff tkinter-final -- '*.py'`，应只含 W0 那一次抽取（`ui.py` / `wbn/insight.py` / `tests/test_insight.py`） |
| 采集日期 | 2026-10-27 |
| Python | 3.13.5（`.venv`，CPython AMD64） |
| OS / 版本 | Windows 11，build 26300 |
| 屏幕 DPI / 缩放 | **192 DPI = 200%**（`theme.S.scale = 1.9988`，`GetDpiForWindow` 返回 192） |
| 屏幕分辨率 | 3120 × 2080（设备像素） |
| 根窗口 | **2239 × 1559 设备像素** = 1120 × 780 逻辑像素（`DIM.WIN_W/H`） |
| 内容区（channels 页实测） | 1320 × 780 设备像素 |
| 机器 | Intel Core Ultra 7 155H（22 逻辑核）/ 31.5 GB 内存 |

**DPI 这一行不能省。** 200% 缩放下每个控件都要走 `theme.S()` 换算，字体度量、
`measure()` 调用次数、Canvas 图元数量全都翻倍。164.1ms 这个数换到 100% 缩放的机器上
会小得多——W6 如果在别的机器上对照，必须先把 DPI 对齐，否则比出来的差值里混着
「缩放不同」这一项。

### 1.1 和 `archive/perf.md` 的数字不能直接比（三处口径不同）

W6 会想拿本文件的数字去和 `archive/perf.md` 比。**不行**，三处口径都不一样：

| 口径 | 本文件 | `archive/perf.md` |
|---|---|---|
| **DPI** | 192 = **200%** | 时间轴那张表标注 **250%** |
| **起点** | `App()` 构造 | 进程起（含 import、`tk.Tk()`） |
| **代码** | 删预热 + 抽 `insight.py` 之后 | 还带 5 页预热、`redesign-base` 卡片结构 |

具体到容易搞混的一对数字：

```
本文件：App() 构造 → 首次布局完成   164.1 ms   （200% DPI，不含解释器启动）
perf.md：App(root) 建侧栏/顶栏/首屏   82 ms     （250% DPI，进程已在跑）
```

**164.1 比 82 大，不是变慢了**——起点终点都不同，DPI 还低了一档。
把它俩相减得出的任何结论都是错的。

**要跨文档对照，只有一条路**：先对齐 DPI（在同一台机器、同一缩放比例下各测一次），
再对齐起点终点。做不到就别比，只在本文件内部纵向比（W6 的新数字 vs 本文件）。

> 顺带一个待查项：`perf.md` 写 250%，本文件实测 200%。可能是换了显示器或改了缩放，
> 也可能是当时估的。**这不影响本文件的数字**（本文件是实测 `GetDpiForWindow`），
> 但说明 `perf.md` 的环境描述不够硬，别把它当环境基准。

## 2. 五组数字

全部由 `tools/bench_ui.py` 采集，窗口 1120×780 逻辑像素（默认尺寸，不传 `--geometry`）。

| 指标 | 数值 | 怎么测的 |
|---|---|---|
| 启动建界面（冷） | **164.1 ms** | `bench_ui.py --runs 3` → `_startup_ms()`：**跨进程**，`App()` 构造到首次布局完成，先丢一次预热再取 median-of-3 |
| 首次进入 · 触发条件 | **23.9 ms** | 同上 → `_cold_ms("triggers")`：**跨进程**，新进程建 `App()` 后立刻进该页，median-of-3 |
| 首次进入 · 消息模板 | **267.6 ms** | `_cold_ms("template")`，口径同上 |
| 首次进入 · 运行状态 | **388.9 ms** | `_cold_ms("status")`，口径同上 |
| 首次进入 · 使用教程 | **30.5 ms** | `_cold_ms("guide")`，口径同上 |
| **首次进入四页合计** | **711 ms** | 脚本自己算的 `cold_total`，等于上面四项相加 |
| 切页（已在内存） | **0.8 – 9.6 ms** | `_switch_ms()`：**同进程**，`pack_forget` + `pack`，每页 median-of-3 |
| 整页重建（切主题走这条路） | **222.7 ms** | `_rebuild()`：**同进程**，销毁全部子控件后重建当前页，median-of-3 |
| pytest | **212 passed** | `.venv/Scripts/python.exe -m pytest -q` |

**切页明细**（同一次运行，median-of-3）：

| 目标页 | ms |
|---|---|
| channels | 1.7 |
| triggers | 0.8 |
| template | 9.6 |
| status | 4.7 |
| guide | 1.0 |

### 2.1 页名与数字的对应（别对错）

`bench_ui.py` 用的是内部 key，不是中文页名。对照表：

| 内部 key | 中文页名 | 首次进入 |
|---|---|---|
| `triggers` | 触发条件 | 23.9 ms |
| `template` | 消息模板 | 267.6 ms |
| `status` | 运行状态 | 388.9 ms |
| `guide` | 使用教程 | 30.5 ms |
| `channels` | 推送通道 | 不在「首次进入」里——它是首屏，算进「启动建界面」的 164.1 ms |

最慢的是**运行状态**（388.9 ms），因为要读 `notifications.log` 与
`notify-YYYY-MM-DD.log` 两个文件、正则匹配每一行、再画时间轴。

### 2.2 复现命令

```bash
cd "D:/Code/Work buddy 通知"

# 五组数字里的前八项（启动 + 四页 + 切页 + 重建）
.venv/Scripts/python.exe tools/bench_ui.py --runs 3

# 带预算判定，超预算返回非零退出码
.venv/Scripts/python.exe tools/bench_ui.py --runs 3 --strict

# pytest
.venv/Scripts/python.exe -m pytest -q
```

`--runs` 是**每个测量点的重复次数**，取中位数。冷启动类测量（启动、首次进入）
每次都在**新进程**里跑，因为同进程第二次会命中 import 缓存与 Tk 全局状态；
第一次调用额外跑一次并丢弃，用来预热文件系统缓存。

## 3. 已知的既有缺陷（不是 W0 引入的）

### 3.1 ScrollFrame 销毁后仍收到 `<Configure>`

- **位置**：`wbn/widgets.py:1498`，`ScrollFrame._on_canvas` → `_refresh_bar` → `bar.find_all()`
- **现象**：ScrollFrame 已销毁，延迟触发的 `<Configure>` 回调仍进来，抛
  `TclError: invalid command name "...!scrollframeN.!canvas"`。切通道重建右栏表单时能稳定复现
- **影响**：Tk 把回调异常吞了，界面不崩、功能正常。属于日志噪音级
- **为什么不在 W0 修**：`widgets.py` 是 W5 的删除对象，改它等于给要拆的房子刷墙
- **W6 注意**：做新旧并排截图时如果看到这条错误，它是**旧代码就有的**，
  不是 web 版引入的。别往新代码上查

### 3.2 界面里 ✅ 显示成方框

Tk 8.6 在 Windows 上不支持彩色 emoji，界面内的 ✅ 渲染成单色方框勾。
推到手机的是正常彩色 emoji。这条在 `README.md` 与 `docs/troubleshooting.md` 的
「已知限制」里都记着，迁到 WebView2 后自动消失。

## 4. 不做什么

本文件**只记数字和事实**。

- 不写结论（「所以 web 版应该更快」这类判断属于 W6）
- 不写优化建议（哪些还能压、压多少，属于 W6 的复核报告）
- 不写目标值（web 版要达到多少，属于 W2 的预算表）
- **不重新测量**。这组数字的价值在于它对应 `tkinter-final` 这一份确定的代码；
  重测会引入新的机器状态、新的日志数据、新的后台进程，反而不可比

## 5. 引用关系

| 谁 | 用它做什么 |
|---|---|
| W6 | 新旧并排对照的性能一侧。web 版用同一套 `bench` 口径重测，两列并排 |
| W8 | 发布说明里「比上一版快了多少」的分子 |
| `docs/functional-baseline.md` | 第 12 条「启动不慢」的基准 |
| `docs/archive/perf.md` | **不用**。那是 P1/P2 中途数据，测的不是同一份代码 |
