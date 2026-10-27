# 决策记录

这里记的是为什么这样选。选了什么读代码和 README 就知道，为什么是这些选择、
当时拿什么数据换来的、代价落在谁头上，只有这里记着。

每条 ADR 有四个固定小节：背景、决策、代价、备选方案。代价那一节是重点，
一个只讲好处的决策记录没有价值。

| 编号 | 决策 | 关键数据 |
|---|---|---|
| [ADR-001](ADR-001-hooks-over-log-polling.md) | 挂在 hooks 上，不轮询日志 | 27 个事件收敛成 5 种语义 |
| [ADR-002](ADR-002-stdin-pipe-over-temp-file.md) | stdin 管道投递，不落盘 | 2.02s 降到 0.07s |
| [ADR-003](ADR-003-wechat-sandbox-over-push-platforms.md) | 直连微信测试号，不用第三方推送 | 0 实名，0 费用，模板自定 |
| [ADR-004](ADR-004-brand-token-split.md) | 品牌绿拆成 `brand` 和 `brand_ink` | 2.11:1 对 5.30:1 |
| [ADR-005](ADR-005-multi-variable-over-newline.md) | 多变量分段，不碰 `\n` | `\n` 之后内容整段消失 |
| [ADR-006](ADR-006-python-tkinter.md) | ~~Python 加 Tkinter，控件全自绘~~ **已被 007 取代** | 核心链路零第三方依赖 |
| [ADR-007](ADR-007-pywebview-over-tkinter.md) | UI 改用 pywebview 加系统 WebView2 | 6084 行自绘换不来真 CSS 能力 |
