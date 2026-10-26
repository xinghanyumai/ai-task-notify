# 自定义 Webhook（webhook）

把通知 POST 到你自己的服务上，接自建系统、接 Home Assistant、接 n8n 都行。

## 字段

| 字段 | 默认 | 说明 |
|---|---|---|
| `url` | 空 | 目标地址，只允许 `http` 和 `https` |
| `method` | `POST` | 请求方法 |
| `headers` | `{}` | 额外请求头，JSON 对象 |
| `body_template` | 空 | 请求体模板，留空则发内置 JSON |

## 默认请求体

`body_template` 留空时，程序发这个结构：

```json
{
  "title": "✅ WorkBuddy 任务完成",
  "content": "**项目**：wbn\n**耗时**：1m23s\n...",
  "kind": "task_done",
  "kind_label": "任务完成",
  "project": "wbn",
  "app": "WorkBuddy",
  "time": "2026-01-01 12:00:00",
  "event": "Stop"
}
```

## 自定义请求体

填了 `body_template` 就用模板渲染，可用占位符和消息模板页里的一致：
`{title}`、`{content}`、`{project}`、`{app}`、`{time}`、`{kind}`、`{event}` 等等。

模板走的是正则替换 `{name}`，不用 `str.format`，所以模板里写裸 `{`（比如贴一段 JSON）
不会炸，也不存在格式化字符串注入。未知占位符渲染成空字符串。

## 这个通道的坑

- 只允许 http 和 https。`urllib` 默认还认 `file://`，不拦住的话一个配错的 URL 就能把本地文件读出来，再顺着错误信息进日志。
- 2xx 才算成功。其它状态码一律记为失败并入队重试，最多 5 次。你的服务要返回 200 或 201，返回 204 也行。
- 允许填 127.0.0.1。这是有意的，方便接本机服务。理论上可以拿它探测本机端口，但 URL 是你自己填的，属于自选行为。
- headers 只接受字符串值。JSON 里的数字和布尔会被转成字符串。
