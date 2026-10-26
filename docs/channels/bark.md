# Bark（bark）

iPhone 上最省事的方案。装个 App，复制一串 key，完事。免费，零注册。

## 申请步骤

1. App Store 搜 **Bark** 安装。
2. 打开 App，首页那串 `https://api.day.app/XXXXXXXX` 里的 `XXXXXXXX` 就是设备 key。
3. 设置中心选 `Bark`，打开开关，填 key，点「测试此通道」。

## 字段

| 字段 | 默认 | 说明 |
|---|---|---|
| `server` | `https://api.day.app` | 官方服务器。自建的话换成自己的域名 |
| `key` | 空 | 设备 key。粘贴整串 URL 也行，程序会把前缀和斜杠剥掉 |
| `group` | `AI 任务` | 通知分组，iOS 上会归到一组里 |
| `sound` | `bell` | 提示音 |
| `level` | `active` | `active` 正常提醒，`timeSensitive` 时效性通知，`passive` 静默投递 |
| `icon` | 空 | 通知图标 URL，可留空 |

## 这个通道的坑

- key 里带斜杠也能用。从 App 里复制出来常带着 `/` 或者整个 URL，程序会剥掉，所以不用手动清理。
- 官方服务器在国内偶尔慢。慢到影响体验就自建，Bark 服务端是开源的，随便找台服务器跑起来，把 `server` 改成自己的地址。
- Bark 是 iOS 独占。安卓上没有官方客户端，走 [ntfy](ntfy.md)。
- `level` 填 `passive` 会静默投递。通知进通知中心但不亮屏不响，容易以为没收到。
