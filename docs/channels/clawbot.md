# 微信 ClawBot（`clawbot`）

复用 **WorkBuddy 自己已经绑定的微信助理**发消息：不申请账号、不扫码、不占名额。
消息以「助理回复」的形式出现在你和 WorkBuddy 的那个微信会话里。

## 它和「微信测试号」的区别

| | `clawbot` | `wxmp` |
|---|---|---|
| 要不要申请 | 不要，WorkBuddy 绑过微信就有 | 要，扫码建测试号 + 建模板 |
| 凭证从哪来 | **自动探测宿主的状态目录** | 你手动填 appID / secret |
| 依赖宿主内部结构 | **是**（`claw-state/`） | 否 |
| 到达形式 | 助理会话里的一条消息 | 公众号消息，锁屏能弹 |

所以它排在 `wxmp` 后面：**宿主改内部结构它就断**，而 `wxmp` 不会。

## 字段

| 字段 | 填什么 |
|---|---|
| `bot_token` | 留空 = 自动探测。填了就优先用你填的，且**完全不再读宿主目录** |
| `to_user_id` | 你的微信用户 ID。留空 = 发到「最近活跃会话」 |
| `context_token` | 一般留空。填了会在失败时被自动去掉重试一次 |
| `auto_discover` | 关掉之后，`bot_token` 留空就直接报「未配置」，不去翻目录 |

界面上「当前凭证」那行会写清楚是**手动填写**、**自动探测（e4457217…）**还是**未配置**。
自动探测会显示 bot_id 前 8 位——目录里可能躺着好几个历史 bot，不显示你就分不清探到的是哪个。

## 自动探测读的是什么

```
~/.workbuddy-ai/claw-state/weixin/<bot_id>_im.bot.cursor.json
```

里面只有一个 `get_updates_buf`，base64 解开后是一段 protobuf，`bot_token` 以明文
ASCII 夹在中间。目录里可能有多个文件（重新绑定后 bot_id 会变），取 **mtime 最新**的那个。

四种失败都会返回人话，不会抛异常：目录不存在 / JSON 坏了 / base64 坏了 / 里面没有 token。

> 注意是 `~/.workbuddy-ai/`（带 `-ai`）。第三方包文档里写的 `~/.workbuddy/` 是 legacy 路径，
> 那里没有 cursor 文件。

## 协议事实（2026-10-31 从宿主 `app.asar` 读出）

宿主自己的 `WeixinApi` 类就在 `D:\APP\WorkBuddyAI\resources\app.asar` 里（明文 JS）。
**宿主一改协议，这张表就是比对基准**——照它核一遍，比在微信里试错快得多。

```js
function buildBaseInfo(){ return { channel_version: `workbuddy-desktop-1.0.0` } }

function randomWechatUin(){
  let e = (0,c.randomBytes)(4).readUInt32BE(0);
  return Buffer.from(String(e), `utf-8`).toString(`base64`)   // 十进制字符串再 base64
}

async post(e, t, n, r){
  let i = JSON.stringify({ ...t, base_info: buildBaseInfo() })  // base_info 顶层合并
  ...
}

async getUpdates(e, t, n){ return this.post(`getupdates`, { get_updates_buf: e }, r + 1e4, n) }
async sendMessage(e){ return this.post(`sendmessage`, e, 15e3) }
```

| 项 | 宿主 | 我们 | 判定 |
|---|---|---|---|
| `base_info.channel_version` | `workbuddy-desktop-1.0.0` | 同 | ✅ 已对齐（`channels.py` 的 `CLAWBOT_CHANNEL_VERSION`） |
| `base_info` 位置 | `post()` 里顶层 `{...body, base_info}` | 顶层 `base_info` | ✅ |
| 游标 `get_updates_buf` | `loadPersistedCursor()` 返回磁盘**原字符串**，原样传 | 同 | ✅ 传 base64 串，**不许解码** |
| `X-WECHAT-UIN` | `b64encode(String(uint32))` = 十进制字符串的 base64（16 字符） | 同 | ✅ 已对齐（`_clawbot_headers`） |
| 其余头 | `Content-Type` / `AuthorizationType` / `Content-Length` / `Authorization: Bearer` | 前两个 + `Authorization`，`Content-Length` 由 urllib 自动加 | ✅ 实质一致 |
| `sendmessage` body | `{msg:{from_user_id:"", to_user_id, client_id, message_type:2, message_state:2, context_token, item_list:[{type:1,text_item:{text}}]}}` | 逐字段一致 | ✅ |
| URL | `apiBaseUrl + "/ilink/bot/" + name` | `https://ilinkai.weixin.qq.com/ilink/bot/...` | ✅ |
| `getupdates` 超时语义 | **长轮询**，默认 35s；请求超时 = poll + 10s；`AbortError` 一律返回 `{ret:0, msgs:[], get_updates_buf:<原游标>}` | `SEND_TIMEOUT = 8` | ⚠️ 探针要**把超时当「没消息」**，不是当失败 |

两个曾经踩过的坑，写在这里防止复发：

1. **`channel_version` 不能写 `1.0.2`。** 服务端拿它做协议协商，写错会在**解析阶段**
   就拒：`{"ret":-1}`、0.2 秒返回——不是 35 秒长轮询超时。这个症状很容易被误判成
   「没消息」或「游标失效」。
2. **`X-WECHAT-UIN` 不是 4 个原始字节的 base64。** 十进制串 base64 是 16 字符，
   原始字节 base64 只有 8 字符，长度一眼就能分开。

## 常见报错

| 返回 | 意思 | 怎么办 |
|---|---|---|
| `ret=-2 invalid arguments` | 缺 `to_user_id`，服务端不知道发给谁 | 在微信里先给助理发一句话，或把用户 ID 填进「接收者 ID」 |
| `ret=-2 prepare failed` | 有 `to_user_id`，但那个会话不活跃 | 同上：先把会话「热」起来 |
| `errcode=-14` | `context_token` 过期 | 程序会自动去掉它重试一次，一般不用管 |
| `401` / `403` | `bot_token` 不对或已失效 | 在 WorkBuddy 里重新绑微信，让游标文件刷新 |

**`ret=-2` 不是「token 错了」**，别去反复重新绑定——先去微信里发一句话。

## 两条禁令

1. **不要跑 `--login` 之类的重新登录命令。** 那会占掉微信侧唯一名额、顶掉 WorkBuddy
   原生微信连接，你的助理就掉线了。要换 bot 请在 WorkBuddy 里操作。
2. **不要把 `bot_token` 贴到 issue / 截图里。** 它等价于这个助理的密码。程序自己的日志
   只会写前 8 位（`e4457217…`），你也照这个来。
