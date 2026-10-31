# W11 · ClawBot 协议对齐 + 真机发送 + 收尾终审

> 前置：W10 已交付（`bf70c9c`），CI `success`，`640 passed / 1 skipped`。
> 主执行 skill：`systematic-debugging`（§1）、`tdd`（§1.8 / §3）、`code-review-and-quality`（§7）
> 产出：协议对齐（3 处）+ ClawBot 真发证据 + README 两处数字 + 3 条自启守卫 + 终审报告 + 4 个提交
> **不动**：`pipeline.py` 的 `_dedup_key`、`test_stats_perf.py` 的 `BUDGET_MS`、现有 10 个通道、`app.py` 启动链路

---

## 0. 开工前

1. 加载 `systematic-debugging`、`tdd`、`code-review-and-quality`。
2. 基线：`git log -1 --oneline` = `bf70c9c`；`git status --short` 有两个未跟踪任务书（W10 / W11）。
3. **不要**重启或关闭用户的 WorkBuddy。§1 全程只读宿主目录，一次都不许写。

---

## 1. 协议对齐（**先修这个，再谈真发**）

### 1.1 你上一轮定位到的偏差，PM 已独立复核：成立，且比你报的多一处

PM 直接从 `D:\APP\WorkBuddyAI\resources\app.asar` 读出宿主自己的 `WeixinApi` 类原文（只读）：

```js
function buildBaseInfo(){ return { channel_version: `workbuddy-desktop-1.0.0` } }
function randomWechatUin(){
  let e = (0,c.randomBytes)(4).readUInt32BE(0);
  return Buffer.from(String(e), `utf-8`).toString(`base64`)     // ← 注意是「十进制字符串」再 base64
}
async post(e, t, n, r){
  let i = JSON.stringify({ ...t, base_info: buildBaseInfo() })   // ← base_info 顶层合并
  ...
}
async getUpdates(e, t, n){ return this.post(`getupdates`, { get_updates_buf: e }, r+1e4, n) }
async sendMessage(e){ return this.post(`sendmessage`, e, 15e3) }
```

### 1.2 逐项对照（**这是本轮的事实基准，写代码前先核一遍**）

| 项 | 宿主 | 我们 W10 | 判定 |
|---|---|---|---|
| `base_info.channel_version` | `workbuddy-desktop-1.0.0` | `"1.0.2"`（`channels.py:1660`） | ❌ **代码 bug** |
| `base_info` 位置 | `post()` 里顶层 `{...body, base_info}` | 顶层 `base_info` | ✅ |
| 游标 `get_updates_buf` | `loadPersistedCursor()` 返回磁盘**原字符串**，原样传 | 任务书 §1.3 让**解码** | ❌ **任务书写错，PM 的锅** |
| `X-WECHAT-UIN` | `b64encode(String(uint32))` = 十进制字符串的 base64 | `b64encode(uint32.to_bytes(4,"big"))` = 4 个原始字节的 base64（`channels.py:1786`） | ❌ **代码 bug** |
| 其余头 | `Content-Type` / `AuthorizationType` / `Content-Length` / `Authorization: Bearer` | 有前两个 + `Authorization`；`Content-Length` 由 urllib 自动加 | ✅ 实质一致 |
| `sendmessage` body | `{msg:{from_user_id:"", to_user_id, client_id, message_type:2, message_state:2, context_token, item_list:[{type:1,text_item:{text}}]}}` | 逐字段一致 | ✅ |
| URL | `apiBaseUrl + "/ilink/bot/" + name` | `https://ilinkai.weixin.qq.com/ilink/bot/...` | ✅ |
| `getupdates` 超时语义 | **长轮询**，默认 35s；请求超时 = poll + 10s；`AbortError` 一律返回 `{ret:0, msgs:[], get_updates_buf:<原游标>}` | `SEND_TIMEOUT = 8` | ⚠️ 探针必须**把超时当「没消息」**，不是当错误 |

`ret:-1` 每次 0.2 秒就回 → 解析阶段就被拒，不是长轮询超时。这与「三个头里有两个错」吻合。

### 1.3 探针调用规范（授权 2 次，用完即止）

| # | 约束 | 值 |
|---|---|---|
| 1 | 游标 | **磁盘原字符串**（104 字符 base64），**不解码**。先做一次 `b64encode(b64decode(s)) == s` 往返校验并贴结果 |
| 2 | `channel_version` | `workbuddy-desktop-1.0.0` |
| 3 | `X-WECHAT-UIN` | `base64.b64encode(str(random.getrandbits(32)).encode()).decode()` |
| 4 | HTTP 超时 | **5 秒**；超时 = 无消息（`ret:0, msgs:[]`），**不算失败** |
| 5 | 调用次数 | 总共 **2 次**，间隔 ≥ 5 秒。第一次用 S0 快照，第二次用当前游标 |
| 6 | 写操作 | 对 `claw-state/` **零写入**。开工前后各记一次 `md5` + mtime |
| 7 | 拿到 `msgs` 后 | 取 `from_user_id` 当 `to_user_id`，取 `context_token`，**汇报里 `to_user_id` 只给前 4 位 + 长度** |

2 次都空 → 停手，`clawbot` 保持「未验证」，**不许**再试第三次、不许猜 id。

### 1.4 拿到凭证后发送

1. `config.json` 的 `clawbot`：`enabled: true`、`to_user_id`、`context_token` 填上，`bot_token` 留空走自动探测。
2. 用真实投递链路发：`python selftest.py` 或状态页「发送测试」。
3. 期望日志：`channel=clawbot ok=True msg=已发送`（或 `已发送（已去掉 context_token）`）。
4. **手机端用户确认之前**，汇报只许写「服务端已接受（ok=True），手机端待确认」。

### 1.5 成功后改文档（只改这三处，别的别动）

| 位置 | 改后 |
|---|---|
| `README.md:91` | `✅ **1 次**（2026-10-31 真机）` |
| `README.md:106-112` 那段 | 改成「真机已验证；但**依赖宿主私有目录**，宿主改结构就断」——保留风险提示，删掉失败解释 |
| `README.md:7` 徽章 | `channels-11%20%C2%B7%204%20verified` |

徽章 URL 不许有裸 `(` `)`；改完 `curl -o /dev/null -w "%{http_code}"` 必须 200。

---

## 1.8 代码修复（**与真发无关，独立必修**）

`channel_version` 和 `X-WECHAT-UIN` 这两处是**已提交代码里的 bug**，即使真发拿不到凭证也要修——
它意味着所有用户的 `clawbot` 通道都是坏的。

| 文件:行 | 改前 | 改后 |
|---|---|---|
| `wbn/channels.py:1660` | `CLAWBOT_CHANNEL_VERSION = "1.0.2"` | `CLAWBOT_CHANNEL_VERSION = "workbuddy-desktop-1.0.0"` |
| `wbn/channels.py:1786` | `base64.b64encode(random.getrandbits(32).to_bytes(4, "big")).decode()` | `base64.b64encode(str(random.getrandbits(32)).encode()).decode()` |

同时把 docstring 里那句「`X-WECHAT-UIN` 是个 base64 的随机 uint32」改成准确说法：
**「随机 uint32 的十进制字符串，再 base64」**。

**测试（`tests/test_channel_clawbot.py` 追加 3 条）**：

| # | 用例 | 断言 |
|---|---|---|
| 1 | `test_channel_version_matches_the_host_client` | `CLAWBOT_CHANNEL_VERSION == "workbuddy-desktop-1.0.0"`；且 `json.dumps(body)` 里出现这个串 |
| 2 | `test_wechat_uin_is_base64_of_the_decimal_string` | 打桩 `random.getrandbits` 返回 `1234567890` → 头里必须是 `b64encode(b"1234567890")`，**且长度是 16 字符**（4 字节 base64 只有 8 字符，能一眼区分） |
| 3 | `test_uin_is_regenerated_per_request` | 连调两次 `_clawbot_headers`，两次 `X-WECHAT-UIN` 不同 |

`docs/channels/clawbot.md` 加一节「协议事实（2026-10-31 从宿主 `app.asar` 读出）」，
把 §1.2 那张表照抄进去——**下次宿主改协议，这就是比对基准**。

---

## 2. README 两处遗留数字（与 §1 结果无关，必修）

| 位置 | 现在 | 应为 | 依据 |
|---|---|---|---|
| `README.md:7` | `channels-10 · 3 verified` | `channels-11 · 3 verified`（§1 成功则 `11 · 4`） | `config.example.json` 的 `channels` 实测 11 项 |
| `README.md:365` | `10 个通道各一页` | `11 个通道各一页` | `ls docs/channels/*.md` = 11 通道页 + 1 索引 |

**不许动** `README.md:133` 那句「10 个推送通道」——那是历史通知正文的样例（`selftest --dry` 的真实输出），
改了是伪造历史。

---

## 3. 自启行为回归守卫（W9 遗留 P1）

现状：`autostart_get` / `autostart_set` 在 `tests/` 里**只出现在 `test_bridge.py:863-867` 的方法名白名单**，
零行为断言。W9 验收时两个变异（回显入参 / 关掉不删文件）**都 570 全绿**。

新建 `tests/test_autostart_switch.py`，`tmp_path` 造假启动目录，打桩 `install._startup_dir`，
**不许碰真实的 `%APPDATA%\...\Startup`**：

| # | 用例 | 断言 |
|---|---|---|
| 1 | `test_off_really_removes_the_shortcut` | 预置假 `.lnk` → `set_autostart(False)` → 文件**真的不在了**，返回 `(True, ...)` |
| 2 | `test_off_when_already_off_is_not_an_error` | 目录空 → `(True, ...)`，不抛 |
| 3 | `test_set_never_echoes_the_argument` | `make_shortcut` 打桩返回 `False` → `bridge.autostart_set(True)` 的 `enabled` 必须是 **`False`** |

变异验证（必做，各贴三个数）：

| 变异 | 做法 | 期望 |
|---|---|---|
| M1 | `install.py:532` 的 `if not on:` 分支首行插 `return True, "ok"` | 用例 1 红 |
| M2 | `bridge.py:639` 的「重读」改成 `return {"enabled": bool(on), ...}` | 用例 3 红 |
| M3 | `CLAWBOT_CHANNEL_VERSION` 改回 `"1.0.2"` | §1.8 用例 1 红 |

---

## 4. 提交（4 个，不许 `git add -A`）

```bash
# ① 收录两份任务书原文（一个字符都不许改）
git add "docs/prompts/W10-全量修复.md" "docs/prompts/W11-ClawBot协议对齐.md"
git status --short
git commit -m "docs(prompts): 收录 W10 / W11 任务书（原文未改）"

# ② 协议对齐（代码 + 测试 + 通道文档）
git add wbn/channels.py tests/test_channel_clawbot.py docs/channels/clawbot.md
git status --short
git commit -m "fix(W11): clawbot 协议对齐宿主——channel_version 与 X-WECHAT-UIN 两处写错"

# ③ 自启回归守卫
git add tests/test_autostart_switch.py
git status --short
git commit -m "test(W11): 补自启开关 3 条行为守卫（关掉真删 / 重复关不报错 / 不回显入参）"

# ④ README（§1 成功才带上 clawbot 状态与 verified 数）
git add README.md
git status --short
git commit -m "docs(W11): 通道数 10→11、docs 表通道页数订正"

git log -4 --stat
git push origin main
```

硬规则：

1. **不许 `git add -A` / `git add .`。**
2. **不许 `--amend` / `--no-verify` / 改 tag。**
3. 提交前密钥自查，**0 命中**（`to_user_id` 和 `@im\.bot:` 也要查）：
   ```bash
   git diff --cached | grep -nEi "wx[0-9a-f]{16}|appsecret|openid|ghp_|sk-[A-Za-z0-9]{20,}|@im\.bot:|BEGIN [A-Z ]*PRIVATE KEY" || echo "clean"
   ```
4. 确认凭证没进暂存区：
   ```bash
   git diff --cached --name-only | grep -E "^config\.json" && echo "STOP" || echo "ok"
   ```
5. 推完**等 CI 真出结果**再汇报，贴 `conclusion` 原文。

---

## 5. 验收（8 条）

| # | 项 | 判据 |
|---|---|---|
| 1 | 协议对齐 | `grep -n "workbuddy-desktop-1.0.0" wbn/channels.py` 命中；`grep -c '"1.0.2"' wbn/channels.py` = 0 |
| 2 | UIN 格式 | §1.8 用例 2 绿；长度 16 字符 |
| 3 | 探针 | ≤2 次；每次贴服务端原始返回；往返校验通过 |
| 4 | 真发 | `channel=clawbot ok=True`，或如实报「无数据源，保持未验证」 |
| 5 | 宿主未被动 | `cursor.json` 开工前后 `md5` + mtime 对照 |
| 6 | 守卫 | 3 条绿；M1/M2/M3 各自能红，还原后逐字节一致 |
| 7 | 门禁 | `pytest -q` ≥ `646 passed, 1 skipped`（640 + 3 + 3）；`ruff check .` 干净；`git status --short` 空 |
| 8 | 提交 | 4 个；`git log -4 --stat` 原文；暂存区无 `config.json*` |

---

## 7. 终审（用户明确要求「防止 bug」）

修完上面全部之后，跑一遍**终审清单**。这是**只读 + 只跑测试**，不许顺手改代码；
发现问题**记下来写进汇报**，别当场改（当场改就没人复核了）。

| # | 检查 | 命令 / 判据 |
|---|---|---|
| 1 | 未提交/未跟踪残留 | `git status --short` 必须空 |
| 2 | 敏感文件没被跟踪 | `git ls-files \| grep -Ei "config\.json$\|\.broken-\|\.log$"` 必须 0 行 |
| 3 | 全仓密钥扫描（不只暂存区） | `git grep -nEi "wx[0-9a-f]{16}\|appsecret\|@im\.bot:\|ghp_\|sk-[A-Za-z0-9]{20,}"` 必须 0 行 |
| 4 | 11 个通道注册五处齐全 | 逐个通道核 `CHANNEL_META` / `CHANNEL_GROUPS` / `REQUIRED` / `send_one` 分支 / `config.example.json`，**列一张 11 行的表**，缺一处标红 |
| 5 | README 数字与代码一致 | 通道数 / 事件数 / 文档页数 / 用例数，各给「README 写的 → 实测值」两列 |
| 6 | README 断链 | 脚本扫相对链接 + 锚点，`broken` 必须 0 |
| 7 | 绝对阈值用例 | `grep -rn "perf_counter\|< [0-9]\+\.\?[0-9]*$" tests/` 里凡是**墙钟绝对阈值**的列出来，标「本机余量倍数」 |
| 8 | 平台隔离 | `install.py` / `app.py` / `wbn/*.py` 顶层导入 AST 扫一遍，Windows 专有符号只许在函数内 |
| 9 | 静默 except | `git grep -n "except.*:\s*$" -- "*.py"` 找 `except Exception: pass` 型，逐条判「吞掉的是不是用户该知道的事」 |
| 10 | 三处「必须重读」是否都成立 | `autostart_set`、`config_health`、`clawbot_credential` 各自「操作后重读」的实现行号 |

汇报格式：一张表，每行 `# | 结论 | 证据 | 发现问题`。**发现问题不许自己修**，只登记。

---

## 8. 汇报（7 项）

```
1. §1.2 那张对照表，逐行标「✅ 已对齐 / ❌ 已修」+ 改动后的代码行号
2. 探针：往返校验输出 + ≤2 次的服务端原始返回 + cursor.json 前后 md5/mtime
3. 真发结果：日志原文那一行 + 手机端是否收到（用户确认 / 待确认）
4. README 两处 diff + 徽章 URL 的 HTTP 状态码
5. 3 条自启守卫 + 3 条协议用例的用例名；M1/M2/M3 的「改坏前→改坏后→还原后」九个数
6. pytest 尾行 + ruff 输出 + `git log -4 --stat` 原文 + CI conclusion 原文
7. §7 终审 10 行表（含发现的问题清单，不许隐瞒）
```

第 2、3、7 项**不许含糊**。第 7 项发现了问题却写「无问题」，比不做终审更糟。
