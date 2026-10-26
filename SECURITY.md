# 安全策略

这是一个单机自用的个人工具，没有安全团队，也没有漏洞赏金。但凭证是真的，
所以这里写清哪些算问题、怎么报、泄露了怎么办。

## 报问题

开一个 issue，标题带 `security`，正文写清复现步骤和影响。如果涉及具体凭证，
不要贴真实值，用 `abc***ef` 这种形式代替。

真正的漏洞我会修。已知边界不会当成漏洞处理，那些列在
[docs/security.md](docs/security.md) 的「已知边界」一节，主要是这两条：

- 拿得到 `config.json` 的本地攻击者本来就能读你的微信和邮箱，ACL 收紧只防顺手看到
- 自定义 Webhook 允许填 `http://127.0.0.1:xxx`，可以探测本机服务，但 URL 是用户自己填的

## 支持范围

只维护最新提交。这是个人工具，没有 LTS 分支，也没有回填补丁。

## 凭证泄露了怎么办

**删文件、删提交、改写历史都不算解决。** 凭证一旦离开过本机，就当它已经泄露。

测试号页面没有重置 appsecret 的入口，重置路径只对正式服务号成立。真正止血的动作是
换一个微信号扫码申请新的测试号，旧 secret 随之作废，然后重新走一遍关注、建模板、
填配置，五分钟搞定。完整步骤见
[docs/channels/wechat-sandbox.md](docs/channels/wechat-sandbox.md)。

泄露的影响面很窄。拿测试号 appsecret 能取 access_token、向已关注该测试号的用户发模板消息、
读关注者 openid 列表、改自定义菜单，不能群发、不能读手机号或支付信息。关注者基本只有你本人，
所以最坏情况是有人冒充这个公众号给你发钓鱼通知。

其他通道同理：SMTP 授权码去邮箱后台吊销，Bark key 在 App 里重置，机器人 Webhook
在群设置里重新获取，企业微信应用的 Secret 在后台重新生成。

## 本仓库不含凭证

`config.json`、`state/`、`logs/` 都在 `.gitignore` 里。仓库里只有
`config.example.json`，凭证字段全为空。`assets/` 下的界面截图用 `shot.py` 的
`WBN_SCRUB=1` 模式生成，里面的 appID、模板 ID、openid 都是等长假值。

安全设计的完整说明见 [docs/security.md](docs/security.md)。
