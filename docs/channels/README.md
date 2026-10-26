# 推送通道

内置 10 个通道，每个通道一页，写清申请步骤、字段含义和各自的坑。

## 共同的几件事

通道配置存在 `config.json` 的 `channels` 数组里，按 `type` 匹配。设置中心的通道页
只是这个数组的可视化编辑器，手改 `config.json` 也一样生效（改完记得点保存或者重启）。

每个通道有一个 `enabled` 开关。可以同时开多个，发送时并发进行，任一成功即视为送达，
失败的通道单独记进日志。本地日志通道默认开着，它只写文件不打扰你。

所有通道共用一套限流，默认 4 条每分钟、20 条每小时。这是为了照顾微信模板消息接口的
分钟级频次限制，留了足够余量。被限流的事件会记成 `skip reason=rate`，不会补发。

发送失败会入队，下次事件触发时重试，最多 5 次。队列在 `state/queue.jsonl`。

界面上每个通道面板右下角都有「测试此通道」，它走的是真实发送链路，会消耗一次限流额度。

## 挑一个

| 通道 | `type` | 需要准备什么 | 文档 |
|---|---|---|---|
| 微信测试号 | `wxmp` | appID、appsecret、模板 ID，openid 可自动取 | [wechat-sandbox.md](wechat-sandbox.md) |
| 企业微信自建应用 | `wecom_app` | 企业 ID、Secret、AgentId | [wecom-app.md](wecom-app.md) |
| Bark | `bark` | 装 App，取设备 key | [bark.md](bark.md) |
| ntfy | `ntfy` | 装 App，起一个 topic | [ntfy.md](ntfy.md) |
| 企业微信群机器人 | `wecom_bot` | 群机器人 Webhook | [wecom-bot.md](wecom-bot.md) |
| 钉钉群机器人 | `dingtalk_bot` | Webhook，可选加签 secret | [dingtalk-bot.md](dingtalk-bot.md) |
| 飞书群机器人 | `feishu_bot` | Webhook | [feishu-bot.md](feishu-bot.md) |
| 邮件 | `smtp` | SMTP 服务器、账号、授权码 | [smtp.md](smtp.md) |
| 自定义 Webhook | `webhook` | 一个能收 POST 的地址 | [webhook.md](webhook.md) |
| 本地日志 | `log` | 无 | [log.md](log.md) |

只想尽快收到第一条通知的话，走微信测试号。扫码两分钟，免费免实名，
消息直接进微信聊天列表，不用装任何 App。

想要 markdown 加粗和标题，走企业微信自建应用。它的排版最自由，但配置多两步。
