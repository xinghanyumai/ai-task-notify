# W14 · 让 exe 真能发给别人用

> 前置：W13 已交付（`d1f88fc`），CI `success`，`655 passed / 1 skipped`。
> 目标：**开源给别人用，不许有功能缺失。**
> 主执行 skill：`systematic-debugging`（§2 的验证）、`code-review-and-quality`（收尾）
> 产出：重打包 + exe 真跑一轮 + 干净目录首次运行证据 + 1 处静默异常修复
> **不动**：`wbn/channels.py` 的协议、`app.py` 的启动链路、11 个通道注册

---

## 0. 开工前（先看 §1，这是本轮最重的一条）

1. 基线：`git log -1 --oneline` = `d1f88fc`；`git status --short` 空。
2. 本轮会**改动你机器上的 `settings.json`**（§2.3）。开工前先跟用户确认一句，
   并确认 `install.py` 的自动备份真的生成了。
3. 全程**不许删** `settings.json.bak-*` 和 `*.w14-backup`。
4. ⚠️ **绝对不许杀 WorkBuddy / CodeBuddy 进程。**
   当前这个对话就跑在 WorkBuddy 里，杀了它对话就断了。
   `install()` 打印的「必须完全退出并重启宿主」那条，**由用户手动做**，
   执行者只负责把配置写对，然后把「需要重启」这件事报给用户。

---

## 1. P0 · 重打包：当前 exe 落后 15 个提交

### 1.1 事实（PM 实测）

| 项 | 值 |
|---|---|
| `通知.exe` mtime | **2026-10-31 02:26** |
| 体积 | 20,175,030 B（19.24 MB） |
| 最后一次改代码 | `765e528` **17:00** |
| 落后提交数 | **15**（`c534215` 之后全部） |

也就是说，按时间推断，现在这个 exe 里**没有**下面这些（`通知.exe` 是压缩包，
`grep` 查不到字符串，所以这是**推断不是实测**——§1.3 第 2 条就是去核实）：

| 缺的东西 | 提交 | 后果 |
|---|---|---|
| 单实例锁修复 | `c534215` | **P0 窗口无限累积**（就是用户截图里那一堆窗口） |
| ClawBot 通道 | `7aaab73` | **整个通道不存在**，别人配了也没有 |
| clawbot 协议对齐 | `1f0a2e3` | 通道坏了也发不出去 |
| 沙箱偏好记忆 | `e8b1466` | 每次启动多等 10 秒 |
| 配置损坏备份 | `9bba44a` | 配置坏了直接静默清空 |
| 摘要去表格 | `765e528` | 推送正文一串竖线 |
| enqueue 留日志 | `bed706f` | 重试队列失败无声 |

**现在把 exe 发给别人，等于发一个 15 小时前的旧版本。** 这条比 §2 的 hooks 更严重。

### 1.2 做法

```bash
python tools/build_exe.py
```

打完核对（**四个数都要贴进汇报**）：

| 项 | 期望 |
|---|---|
| 位置 | 仓库根 `通知.exe`（`EXE = ROOT / "通知.exe"`） |
| 体积 | 18–26 MB（`EXE_SIZE_RANGE_MB`） |
| mtime | **今天 17:30 之后** |
| sha256 | 贴出来，与旧值对比（`git stash` 前先记旧的） |

⚠️ **打包前先记下旧 exe 的 sha256**，否则没法证明"确实换了新的"。

⚠️ **`tools/build_exe.py` 会删 `通知/` onedir 目录**（W7.5 改过，只删 exe 与 onedir，
不碰 `config.json`/`logs`/`state`）。跑之前确认 `config.json` 的 mtime 与大小，
跑完再核一遍——**没变才是对的**。

### 1.3 打完立刻验（不许只看"打包成功"）

| # | 验什么 | 命令 |
|---|---|---|
| 1 | 版本能退出 | `Start-Process -FilePath ".\通知.exe" -ArgumentList "--version" -Wait -PassThru` |
| 2 | 新通道在不在 | `通知.exe --help` 或界面里能看到 `clawbot` 那一行 |
| 3 | 界面能开 | 双击，五个页面都点一遍，**不许出现两个窗口** |
| 4 | 单实例仍生效 | 双击第二次 → 只 focus，不开新窗口 |

第 2 条是重点：**旧 exe 里没有 clawbot**，新 exe 必须能在通道页看到它。

---

## 2. P0 · exe 作为 hook 入口真跑一轮

### 2.1 现状（PM 实测）

`~/.workbuddy-ai/settings.json` 和 `~/.codebuddy/settings.json` 各 9 个事件，
命令**全部指向源码**：

```
"D:/Code/Work buddy 通知/.venv/Scripts/pythonw.exe" "D:/Code/Work buddy 通知/hook.py" --app WorkBuddy
```

**`通知.exe` 从来没有被 hook 调用过一次。** 后果：删掉或移动源码目录，通知立刻停——
而这正是"发给别人"的场景（别人根本没有源码目录）。

### 2.2 先手动喂 stdin（不改任何配置）

这一步零风险，先证明 `通知.exe hook` 这条路径活着：

```bash
echo '{"hook_event_name":"Stop","session_id":"w14-probe","cwd":"D:/Code/Work buddy 通知","reason":"end_turn"}' | ./通知.exe hook --app WorkBuddy
echo "exit=$?"
```

判据：

- 退出码 **0**（hook 永远不该让宿主失败）
- `logs/notify-*.log` 出现新的一行，含 `app=WorkBuddy`
- 通知真发出去（用户手机/通道能看到）

**失败就停在这里报告，不要往下切 hooks。**

### 2.3 再切 hooks 到 exe

两条路，任选，但**必须先备份**：

```bash
# 备份（install.py 自己也会备份，但手动再备一份）
cp ~/.workbuddy-ai/settings.json ~/.workbuddy-ai/settings.json.w14-backup
cp ~/.codebuddy/settings.json  ~/.codebuddy/settings.json.w14-backup
```

然后：

```bash
./通知.exe install
```

（`install.py` 的 `hook_command()` 在 `sys.frozen` 为真时会生成 `"<exe>" hook --app X`。）

切完核对：

```bash
python -c "
import json,pathlib,os
for p in ['~/.workbuddy-ai/settings.json','~/.codebuddy/settings.json']:
    d=json.loads(pathlib.Path(os.path.expanduser(p)).read_text(encoding='utf-8'))
    h=d.get('hooks') or {}
    cmds={v[0]['hooks'][0]['command'] for v in h.values() if v}
    print(p, len(h), '事件')
    for c in cmds: print('   ', c)
"
```

**判据：每个宿主的命令串里必须出现 `通知.exe`，不许再出现 `hook.py`。**

### 2.4 真触发一次宿主任务

⚠️ **hooks 是宿主启动时读取并缓存的**（`install()` 自己会打印这条提示）。
所以切完之后，**必须由用户完全退出 WorkBuddy 再重开**才生效——
**执行者不许自己杀进程**（这个对话就跑在 WorkBuddy 里）。

所以顺序是：

1. 执行者切完配置，核对 §2.3 的命令串，然后**停下来告诉用户**：
   「配置已切到 exe，请完全退出 WorkBuddy（托盘右键 → 退出）再重开，然后随便发一句话让我跑一轮」
2. 用户重启后，执行者再继续验证：
   - 在 WorkBuddy 里发一句会让它干活的提示（比如"看一下当前目录有几个 md 文件"）
   - 等它跑完
   - 查日志：`grep "app=WorkBuddy" logs/notify-*.log | tail -3`
   - 手机端确认收到

**这一步失败 = 立刻回滚**：

```bash
cp ~/.workbuddy-ai/settings.json.w14-backup ~/.workbuddy-ai/settings.json
cp ~/.codebuddy/settings.json.w14-backup  ~/.codebuddy/settings.json
```

回滚后必须再触发一次任务，确认通知恢复——**不许只回滚文件就宣布恢复**。

### 2.5 顺带确认的事

切到 exe 之后，源码目录就不再是必需的。**不要**去删源码目录（用户还在开发），
但要确认：`settings.json` 里已经没有任何一条命令依赖 `D:/Code/Work buddy 通知/` 下的 `.py`。

---

## 3. P1 · 干净目录首次运行（模拟"别人电脑"）

### 3.1 做法

```bash
mkdir -p /tmp/w14-clean && cp 通知.exe /tmp/w14-clean/
cd /tmp/w14-clean
```

（Windows 路径用 `%TEMP%\w14-clean`，别用真机的项目目录。）

### 3.2 四项验证

| # | 场景 | 期望 |
|---|---|---|
| 1 | 空目录双击 → 首次启动 | 能出窗口；目录里生成 `config.json`（从内置默认值，**不含任何凭证**） |
| 2 | 剥掉 Python 的 PATH 跑 `--version` | 退出码 0。命令：`cmd /c "set PATH=C:\Windows\system32&& 通知.exe --version"` |
| 3 | 剥掉 Python 的 PATH 跑 `hook` | 退出码 0，日志落一条 |
| 4 | 只读目录 | 把 exe 放进一个设了只读的目录（或 `C:\Program Files\w14test`，需管理员），启动后配置应回落到 `%LOCALAPPDATA%\WBNotify\`，**状态页显示实际路径**，不许静默失败 |

第 4 条的依据：`wbn/config.py:25-50` 的 `_resolve_root()` 已经实现回落，
用**真写一个探针文件**判断（不是 `os.access`），逻辑是对的——但**从没被真机验证过**。

第 1 条要特别核：生成的 `config.json` 里 11 个通道的凭证字段**必须全空**，
`clawbot.enabled` 必须是 **false**。如果发现任何非空凭证，立刻停手报告——
那意味着 exe 里打进了开发机的配置。

### 3.3 顺带验托盘

```bash
./通知.exe tray --silent
```

期望：托盘图标出现，右键有菜单，退出后进程不残留。**别人会用托盘，这条不能缺。**

---

## 4. P1 · `retry_queue()` 静默失败（W12 漏掉的另一半）

### 4.1 事实

```python
# dispatch.py:70
if int(time.time()) % 10 == 0:
    try:
        pipeline.retry_queue()
    except Exception:
        pass                      # ← 整个重试队列挂掉，无声
```

W12 修了 `store.enqueue`（**写入**失败留日志），但**队列整体重试失败仍然无声**。
写入修好了、重试坏了，消息照样消失。

### 4.2 改法

```python
    except Exception as e:
        store.log(f"重试队列处理失败：{type(e).__name__}: {e}")
```

复用 `store.log()`，不要新写日志。**注意**：`dispatch.py` 顶层已经 import 了 `store`，
确认一下再写，别重复 import。

### 4.3 用例

加进 `tests/test_store_queue.py`：

| # | 用例 | 断言 |
|---|---|---|
| 1 | `test_retry_queue_failure_is_logged` | 打桩 `pipeline.retry_queue` 抛 `RuntimeError` → 调 dispatch 那段逻辑 → 不抛异常，日志出现「重试队列处理失败」 |

---

## 5. 提交（2 个）

⚠️ **`通知.exe` 在 `.gitignore` 里，不许提交。** 只提交代码。

```bash
# ① retry_queue 留日志
git add dispatch.py tests/test_store_queue.py
git status --short
git commit -m "fix(W14): 重试队列整体失败要留日志，补上 W12 漏掉的那一半"

git log -1 --stat
git push origin main
```

硬规则：不许 `git add -A` / `--amend` / `--no-verify`；推完等 CI 真出结果再汇报。

**打包产物和 hooks 切换都不进仓库**，只在汇报里贴证据。

---

## 6. 验收（8 条）

| # | 项 | 判据 |
|---|---|---|
| 1 | exe 是新的 | mtime 晚于 17:30；sha256 与旧值不同（两个值都贴） |
| 2 | exe 体积 | 18–26 MB |
| 3 | 新通道在 | exe 界面通道页能看到 `clawbot`；旧 exe 没有（对比说明） |
| 4 | `通知.exe hook` 能跑 | 退出码 0 + 日志 `app=WorkBuddy` + 用户确认收到 |
| 5 | hooks 已切 | 两个 `settings.json` 的命令串含 `通知.exe`，**不含 `hook.py`** |
| 6 | 真任务走通 | 切完触发一次宿主任务 → 通知到达 + 日志有时间戳 |
| 7 | 干净目录 | 4 项全过；生成的 `config.json` 凭证全空、`clawbot.enabled=false` |
| 8 | 门禁 | `pytest -q` ≥ `656 passed, 1 skipped`（655 + 1）；ruff 干净；`git status --short` 空 |

---

## 7. 汇报（7 项）

```
1. 旧/新 exe 的 mtime + 体积 + sha256（四个值并列）
2. 打包前后 config.json 的 mtime/大小对照（证明没被删）
3. exe 界面截图：通道页能看到 clawbot
4. 手动喂 stdin 那次：命令原文 + 退出码 + 日志行
5. hooks 切换前后命令串对照（两个宿主各一组）+ 真任务那次的日志行
6. 干净目录 4 项各自结果；生成的 config.json 里凭证字段的检查结果
7. pytest 尾行 + ruff 输出 + git log -1 --stat 原文 + CI conclusion 原文
```

第 1、5 项**必须并列贴**，只贴新的等于没证据。
第 6 项如果发现任何非空凭证，**立刻停手报告**，不许继续。
