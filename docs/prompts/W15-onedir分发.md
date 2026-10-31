# W15 · 从 onefile 切到 onedir 分发

> 上游：W14 把 hooks 从源码版切到 exe 之后，PM 实测出一次**回归**：
> `通知.exe hook` 墙钟 1679 / 1562 / 1528 ms，源码版 362 / 379 / 412 ms——**4.1 倍**。
> 根因是 PyInstaller onefile 每次调用都要把整个包自解压到 `%TEMP%\_MEIxxxx`。
>
> 本项目 hooks 挂了 9 个事件，宿主每次事件都要等这么久。这不是「慢一点」，
> 是「AI 每完成一次任务，界面卡 1.5 秒」。
>
> 本任务书把它换成 onedir。**不要按「优化一下」的力度做，这是分发形态变更，
> 有两个 P0 会让人丢数据或收双份通知。**

---

## 0 · 先测后改（这一步不过，后面全部作废）

**别改一行代码，先打一个 onedir 出来量延迟。**

```bash
# 先停托盘和窗口，否则 exe 被锁（W14 踩过：两个托盘进程锁着 exe，打包直接失败）
# 注意：tray 没有 --stop 子命令，只能 taskkill。
taskkill /IM 通知.exe /F 2>nul

.venv\Scripts\python.exe tools\build_exe.py --onedir
```

然后量三次，**同一条命令里量完**：

```bash
printf '%s' '{"hook_event_name":"Stop","session_id":"w15-bench","cwd":"D:/Code/Work buddy 通知","transcript_path":""}' > tmp/w15-hook.json
for i in 1 2 3; do s=$(date +%s%N); ./通知/通知.exe hook --app WorkBuddy < tmp/w15-hook.json; e=$(date +%s%N); echo "onedir run$i wall=$(( (e-s)/1000000 ))ms"; done
```

### 判据

| 项 | 门槛 | 说明 |
|---|---|---|
| onedir hook 三次**中位数** | **< 700 ms** | 对照：onefile 1562 ms，源码 379 ms |
| 三次离散度（max−min） | < 250 ms | 波动大说明还有一次性开销没消掉 |

**中位数 ≥ 700 ms 就停下**，不要继续往下做，直接回报「onedir 没解决，收益不成立」，
并把三次数字贴出来。本任务书后半部分全部建立在「这个门槛过了」的前提上。

> 顺带记录 onefile 的三次数字作为对照基线，写进交付报告。
> 别拿历史数字充数——同一台机器、同一条命令、同一个 payload。

---

## 1 · 产物形态

### 1.1 目录名改 ASCII

`COLLECT(name="通知")` → `COLLECT(name="WBNotify")`。

**为什么**：目录名会出现在两处用户可见的地方——解压后的文件夹、写进宿主
`settings.json` 的 hook 命令。zip 里的中文目录名在非中文 Windows 上解压可能
变成乱码，而乱码路径一旦写进 hooks 就是「每次事件都超时」。
exe 文件名保留 `通知.exe` 不动（单文件改名容易，且产品名是中文）。

产物结构：

```
WBNotify/
├── 通知.exe          ← 本体，约 1MB
└── _internal/        ← 全部依赖，约 60–80MB
    ├── webui/
    ├── assets/
    └── ...
```

### 1.2 `tools/build_exe.py` 的常量与检查

| 常量 / 函数 | 现在 | 改成 |
|---|---|---|
| `ONEDIR_DIR` | `ROOT / "通知"` | `ROOT / "WBNotify"` |
| `EXE_SIZE_RANGE_MB` | `(18.0, 26.0)` | **保留给 onefile**，onedir 另起 `ONEDIR_SIZE_RANGE_MB = (55.0, 90.0)` |
| `SIZE_LIMIT_MB` | 40 | 只对 onefile 生效；onedir 用区间上限 |
| `check_layout(onedir=True)` | 只查 `通知/通知.exe` 在不在 | 见下 |

`check_layout` 的 onedir 分支必须**四条正面判据全过**：

1. `WBNotify/通知.exe` 存在
2. `WBNotify/_internal/` 是目录
3. `WBNotify/_internal/webui/index.html` 存在 ← 挡「白屏」
4. **仓库根目录没有裸的 `通知.exe`** ← 挡「onefile 残留被误当成新产物」

第 4 条不是洁癖：W14 的 onefile 产物就在根目录。切形态后如果不清，
`git status` 看不出来（已 ignore），人也会以为「打包成功了」，实际跑的还是旧的那个。

体积按 `WBNotify/` 整个目录算，不在 `(55, 90)` MB 区间就报错并给出「偏小 = 空壳 /
偏大 = hook 拖回了 numpy」的诊断语（照抄 onefile 那段的话术）。

> `(55, 90)` 是 PM 按 onefile 20MB 反推的估值，**第一次实测后允许调整一次**，
> 调整时把实测值和调整理由写进报告。**不许为了让它变绿而放宽区间**——
> 区间是判据，不是装饰。

### 1.3 保留 onefile 能力

`WBN_ONEDIR` 开关和 `--onedir` 参数**不要删**。理由：将来要对比、要回退、
要给「就想要单文件」的人留条路，都靠它。默认值改成 onedir：

```
build_exe.py            # 默认 onedir（W15 起）
build_exe.py --onefile  # 显式要单文件
```

参数名要跟着改：现在的 `--onedir` 是个「开关」，默认 onefile。反过来之后
`--onedir` 这个词就没意义了，留着会让下一个人看错。做法：

- `tools/build_exe.py` 的 argparse：删 `--onedir`，加 `--onefile`
- 内部所有 `onedir: bool` 形参统一成 `onefile: bool`，判断取反
- `notify.spec` 的 `WBN_ONEDIR` 环境变量**不用改**（它是 spec 的内部约定），
  `build()` 里 `env["WBN_ONEDIR"] = "0" if onefile else "1"`

---

## 2 · P0-1：hooks 迁移（不修就是一条通知发两遍）

### 2.1 问题

现在用户机器上的 hooks 是：

```
"D:\Code\Work buddy 通知\通知.exe" hook --app WorkBuddy
```

切 onedir 之后新 exe 在 `D:\Code\Work buddy 通知\WBNotify\通知.exe`。
`install.py` 的 `is_our_hook()` 判断用的是：

```python
exe = _norm(str(sys.executable)) if getattr(sys, "frozen", False) else ""
...
if exe and exe in cmd and " hook " in f" {cmd} ":
    return True
```

`sys.executable` 是**新**路径，跟旧的 `...\通知.exe` 字符串对不上 → **旧条目认不出来 → 清不掉**。
结果就是 W14 §2.3 那个现象重演：9 个事件各挂两条，宿主重启后每个事件跑两遍。

### 2.2 修法

`_our_hook_py_paths()` 的思路可以照搬，但对象换成 exe。新增一个
`_our_hook_exe_paths()`，返回**所有可能是我们写的 exe 路径**（已 `_norm`）：

| 来源 | 路径 |
|---|---|
| 当前进程 | `sys.executable` |
| onedir → onefile 迁移 | `<exe 父目录>/../通知.exe` |
| 仓库根（源码版 install 写的） | 同上一行，源码态时是 `ROOT / "通知.exe"` |

然后 `is_our_hook` 的 exe 分支改成「`cmd` 里包含任一候选 exe 且含 ` hook `」。

### 2.3 顺手修掉 P3 遗留

```python
if "hook.py" in cmd and (any(t in cmd for t in targets) or "wbn" in cmd):
```

`or "wbn" in cmd` 会误伤：任何别人的 hook 命令里只要出现 `wbn` 三个字母
（比如路径里带 `wbn-tools`）就会被我们删掉。改成只认 `targets` 里的路径。

这条 W14 已登记为 P3，**这次一起修**，因为马上要动这个函数。

### 2.4 迁移后的状态必须自己核

`install` 跑完，**不许只打印「完成」**。必须重新读一遍 `settings.json` 并断言：

```
每个事件恰好 1 条命令
每条命令都含 WBNotify\通知.exe（或源码版的 hook.py）
0 条含根目录的 通知.exe
0 条含 hook.py（onedir 形态下）
```

任一条不满足 → 退出码非 0，把不满足的那条打出来。

### 2.5 旧 exe 怎么办

检测到仓库根目录还有裸的 `通知.exe` 且当前是 onedir 时，**不要自动删**
（那是用户的文件，也可能是他还没迁移完的备份）。改为：

```
⚠ 检测到旧的单文件 通知.exe（<大小>，<mtime>）。
   hooks 已全部指向 WBNotify\通知.exe，旧文件不会再被调用，可以手动删除。
```

一句话，别写五句。

---

## 3 · P0-2：配置迁移（不修就是凭证丢失）

### 3.1 问题

`wbn/config.py` 的 `_resolve_root()` 在 frozen 时返回 `Path(sys.executable).parent`。

- onefile：exe 在仓库根 → `ROOT` = 仓库根 → 读到现有 `config.json`（3688 B，你的真实凭证）
- onedir：exe 在 `WBNotify/` → `ROOT` = `WBNotify/` → **读不到，生成一份全新的默认配置**

后果：微信 appid / appsecret / openid / 全部通道凭证消失，界面上 11 个通道全空，
用户以为「升级把配置弄丢了」。**而文件其实还在上一级目录里。**

### 3.2 修法：一次性迁移，条件从严

在 `config.py` 里加 `_migrate_from_parent()`，在 `ROOT` 解析之后、任何读写之前调用。
**四个条件全满足才迁移**：

1. `frozen` 为真，且 `<exe 父目录>/_internal` 是目录（确认自己是 onedir 形态）
2. 当前 `ROOT / "config.json"` **不存在**（有就绝不碰）
3. `<exe 父目录>/../config.json` 存在，且能 `json.load`，且**同时含 `channels` 和 `version` 两个键**
4. 迁移目标不是 `%LOCALAPPDATA%\WBNotify`（回落目录不参与，避免自己拷自己）

满足就复制 `config.json`、`state/`（整个目录）、`logs/`（整个目录）过来，
然后 `store.log("已从上一级目录迁移配置：<源路径>")`。

**第 3 条的键校验不能省。** 用户可能把 zip 解压到 `D:\Tools\`，而
`D:\Tools\config.json` 是别的程序的配置——没有 `channels` + `version` 就不迁移。

### 3.3 迁移必须可验

新增用例 `tests/test_config_migrate.py`：

| # | 场景 | 期望 |
|---|---|---|
| 1 | onedir + 上级有合法 config | 复制过来，内容逐字节相同 |
| 2 | onedir + 上级 config 是非法 JSON | 不迁移，生成默认配置 |
| 3 | onedir + 上级 config 缺 `channels` | 不迁移 |
| 4 | onedir + 自己已有 config | 不迁移，自己的内容不变（幂等） |
| 5 | 源码态 | 永不迁移 |

### 3.4 README 里那句承诺要改

README 现在写「它和源码模式共用同一份 `config.json`」。onedir 之后**不再共用**：
exe 的配置在 `WBNotify/config.json`，源码的在仓库根。这是好事（互不污染），
但 README 必须如实改，否则用户会照旧去找根目录的 config。

---

## 4 · onedir 运行时验证（三条，缺一不可）

形态换了，`_MEIPASS` 的值也跟着换。以下三条必须**在真机上**验，不能靠读代码推断。

### 4.1 `_MEIPASS` 指向哪

```bash
WBNotify\通知.exe --version
```

在 `app.py` 的 `--version` 分支临时加一行打印（验完删掉，或用环境变量开关）：

```
exe      = ...\WBNotify\通知.exe
_MEIPASS = ...\WBNotify\_internal     ← 期望值
ROOT     = ...\WBNotify               ← 期望值（config 落这里）
BUNDLE   = ...\WBNotify\_internal     ← 期望值（webui 从这里读）
```

**`BUNDLE` 指错 = 界面白屏或没样式，而 Python 侧一行异常都没有。**
如果 PyInstaller 6.22 的 `_MEIPASS` 不是 `_internal`（版本行为可能变），
`config._resolve_bundle()` 要跟着改，别硬编码。

### 4.2 子进程能不能起来

`wbn/spawn.py` 的 `child_env()` 会清掉 `_PYI_*` 环境变量。onefile 下这么做是为了
避免子进程复用父进程的 `_MEIPASS`（父退出即删）。**onedir 下 `_internal` 是持久目录，
清掉之后 bootloader 还能不能自己找到它，必须实测。**

验证：`hook` → `spawn_dispatch` → dispatch 子进程真的发出一条通知。

```bash
echo '{"hook_event_name":"Stop","session_id":"w15-spawn",...}' | WBNotify\通知.exe hook --app WorkBuddy
# 等 3 秒，看 logs\notify-<日期>.log
```

期望 4 行完整落盘（`channel=wxmp` / `channel=clawbot` / `channel=log` / `event=Stop`）。
**只有 `event=Stop` 没有 `channel=` 行 = 子进程没起来**，回去看 `child_env()`。

### 4.3 单实例、托盘、AUMID 不变但要重测

| 项 | 判据 |
|---|---|
| 单实例 | 连开两次 `通知.exe`，第二次直接退出，只有一个窗口 |
| 托盘 | `通知.exe tray --silent` 起得来；再起一次被 `Local\WBN_TRAY_SINGLETON` 挡住 |
| 托盘进程数 | onefile 的父子结构（W14 那两个 `tray --silent`）**应该消失**，只剩 1 个 |
| 任务栏图标 | 不出现两个同名图标（AUMID 生效） |
| 五页 | 状态 / 通道 / 触发 / 模板 / 指南，逐页点开截图 |

托盘进程数那条是个**正面证据**：onefile 必然有 bootloader 父子进程，onedir 应该只有一个。
如果 onedir 下还是两个，说明形态没真正切过去。

---

## 5 · 分发：zip

新增 `tools/make_zip.py`（或并入 `build_exe.py` 的最后一步，二选一，别两处都做）：

- 产物 `WBNotify-v<version>-win64.zip`
- 里面只有一个顶层目录 `WBNotify/`
- **必须排除**：`config.json`、`config.json.*`、`state/`、`logs/`、`*.log`
- 压缩级别默认（`ZIP_DEFLATED`，别用 `ZIP_LZMA`——解压慢，且部分老解压工具不认）
- 打完打印：zip 体积、解压后体积、条目数

**排除清单是安全项，不是优化项。** 如果打包前 `WBNotify/` 里已经跑出过
`config.json`（手工试跑过），直接 zip 出去就等于把微信凭证送人。打包函数里
先断言这四类不存在，再压。

版本号：`wbn/__init__.py` 的 `__version__` 和 `version_info.txt` 一起升到 **1.1.0**。
理由：分发形态变了，用户看到版本号能对上「我装的是文件夹版还是单文件版」。

---

## 6 · CI 改造

`.github/workflows/ci.yml` 的打包段要跟着改：

| 位置 | 现在 | 改成 |
|---|---|---|
| 打包命令 | `python tools/build_exe.py` | 同（默认已改 onedir） |
| 冒烟路径 | `.\通知.exe` | `.\WBNotify\通知.exe` |
| 冒烟断言 | `Test-Path "通知.exe"` | `Test-Path "WBNotify\通知.exe"`，外加 `Test-Path "WBNotify\_internal\webui\index.html"` |
| artifact path | `通知.exe` | `WBNotify/` |
| artifact name | `notify-exe` | `notify-onedir` |

**保留那条 `Start-Process -Wait -PassThru` 的写法，不要退回 `&` + `$LASTEXITCODE`。**
原因在 ci.yml 第 111–120 行的注释里，是踩了 8 轮才修好的：
GUI 子系统程序 PowerShell 不等，`$LASTEXITCODE` 永远是空值。

再加一步：上传 zip。`python tools/make_zip.py` 之后把 `WBNotify-v*.zip` 也传上去。

---

## 7 · README 改写

以下五处**必须改**，改完 grep 一遍确认没有残留的旧说法：

| 位置 | 现在 | 改成 |
|---|---|---|
| 性能段（约 50 行） | 「单文件 exe 形态每次要自解压，1.5–2.2 秒」 | 换成实测数字（§0 测出来的中位数），形如「绿色版约 0.4 秒，一条通知端到端约 2 秒」 |
| 选型段（约 79、82 行） | 「exe 形态端到端 3–4.5 秒」 | 同上，别留旧数字 |
| 打包段标题 | 「打成单文件 exe（可选）」 | 「打成绿色版文件夹（可选）」 |
| 打包段正文 | 「产物是项目根目录的 `通知.exe`（约 19MB，一个文件）」 | 「产物是 `WBNotify/` 目录（约 70MB），`tools\build_exe.py` 会打成一个 zip」 |
| 交付段 | 「**交付只发 `通知.exe` 这一个文件**」×2 处 | 「交付发 `WBNotify-v1.1.0-win64.zip`，对方解压即用」 |

交付段下面那句「`config.json` 里是你的微信凭证，连同目录一起打包就等于把推送权限
一起给了对方」**保留原文**，改成「别把**自己的** `WBNotify/` 目录直接打包发出去
（里面可能有 `config.json`），用 `make_zip.py` 打出来的包不含它」。

新增一小段「安装位置」：

```
解压到任意可写目录（比如 D:\Tools\WBNotify）就能用。
别放进 C:\Program Files——那里不可写，配置会回落到 %LOCALAPPDATA%\WBNotify\，
状态页会显示实际路径。
```

---

## 8 · 验收清单（全过才算完）

### 8.1 形态

- [ ] `WBNotify/通知.exe` 存在，`WBNotify/_internal/` 存在
- [ ] 仓库根**没有**裸的 `通知.exe`
- [ ] 目录体积在 55–90 MB
- [ ] `WBNotify-v1.1.0-win64.zip` 打得出来，里面只有一个顶层 `WBNotify/`
- [ ] zip 里 `config.json` / `state/` / `logs/` **零命中**

### 8.2 延迟

- [ ] onedir hook 三次中位数 < 700 ms，三次原始数字贴进报告
- [ ] 对照 onefile 三次数字也贴出来（同一台机器、同一条命令）

### 8.3 hooks

- [ ] `settings.json` 两个宿主各 9 事件，**每事件恰好 1 条**
- [ ] 全部含 `WBNotify\通知.exe`，0 条含裸 `通知.exe`，0 条含 `hook.py`
- [ ] 用户手机：跑一次真实任务，**只收到 1 条**

### 8.4 配置

- [ ] `WBNotify/config.json` 是迁移过来的（不是默认模板）——对比通道数 11、enabled 3 个
- [ ] `WBNotify/state/` 有内容（token 缓存搬过来了）
- [ ] 仓库根的原 `config.json` **没被删**（迁移是复制不是移动）

### 8.5 运行时

- [ ] `_MEIPASS` / `ROOT` / `BUNDLE` 三个值符合 §4.1
- [ ] hook → dispatch 子进程 → 4 行日志完整
- [ ] 单实例：连开两次只有一个窗口
- [ ] 托盘：起得来、被锁挡住、**进程数 1 个**
- [ ] 五页截图各一张

### 8.6 门禁

- [ ] `pytest -q` 全绿，用例数 ≥ 662 + 新增
- [ ] `ruff check .` 通过
- [ ] `git status --short` 空

---

## 9 · 变异验证（不做这一节 = 没验）

每条都要「改坏了必然红」，验完**用 `git checkout -- <file>` 还原**。
**不要用 `cp` 备份再拷回**——沙箱重试会把已经变异的文件当成备份存下来，
W14 踩过，白验一轮。

| # | 变异 | 必须变红的东西 |
|---|---|---|
| M1 | 删掉 `WBNotify/_internal/webui/index.html` | `check_layout` 报「webui 缺失」；或启动后界面白屏 |
| M2 | `is_our_hook` 的候选 exe 列表只留 `sys.executable` | 旧的 `...\通知.exe` 条目清不掉 → §8.3 第 2 条红 |
| M3 | `_migrate_from_parent` 去掉 `channels` 键校验 | `tests/test_config_migrate.py` 场景 3 红 |
| M4 | `child_env()` 改成直接返回 `os.environ` | §8.5 第 2 条红（子进程复用父 `_MEIPASS`，父退出后崩） |

M2 是最重要的一条：**它守的是「用户不会收到两条通知」**。

---

## 10 · 提交

分三次提交，别糊成一个：

1. `build(W15): 分发形态改 onedir，产物 WBNotify/ + zip`
   —— `notify.spec` / `tools/build_exe.py` / `tools/make_zip.py` / `.gitignore` / `version_info.txt`
2. `fix(W15): hooks 迁移到 onedir 路径；去掉 "wbn" 子串误删` 
   —— `install.py` / `tests/test_install_hooks.py`
3. `feat(W15): onedir 首次启动从上级目录迁移配置`
   —— `wbn/config.py` / `tests/test_config_migrate.py`

README 和 CI 的改动跟第 1 条一起提。

推上去等 CI 绿。**三个 run 都要贴 run id 和 conclusion，不许只写「绿了」。**

---

## 11 · 三条容易翻车的地方（提前说）

1. **打包前必须停托盘。** `通知.exe` 正在跑时 `WBNotify/` 删不掉，
   `build_exe.py` 会 exit 并提示「先关掉再打包」。别用 `--noconfirm` 硬来。

2. **`tmp/` 里有 W14 留下的 `tmp/ci2/`、`tmp/ci_repro/`**，里面是完整的仓库副本
   （含 `hook.py`、`tools/bench_ui.py`）。`ruff check .` 会扫到它们。
   如果 W15 跑 ruff 报出这两个目录里的问题，**不要去修副本**，那是死代码，
   直接确认它们在 `.gitignore` 的 `tmp/` 里、`git status` 干净就行。

3. **`%LOCALAPPDATA%\WBNotify\` 里现在有三样东西**：`config.json`（3392 B，W14 只读
   目录测试造的）、`config.json.w14-backup`（3219 B，10-30 的原版）、`logs/`、`state/`。
   §3 的迁移逻辑**必须排除这个目录**（条件 4），否则 onedir 会去拷它。
   验收完把这个目录清掉——开源用户的机器上不该出现它。
