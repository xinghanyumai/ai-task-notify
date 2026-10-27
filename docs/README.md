# 文档索引

> **四层入口，各管一段，不要跳着看：**
>
> | 层 | 文件 | 回答 |
> |---|---|---|
> | 1 | [`../README.md`](../README.md) | 这是什么、怎么装、怎么用 |
> | 2 | **本文件** | 文档怎么找 |
> | 3 | [`roadmap.md`](roadmap.md) | 整个项目要走到哪、各阶段依赖什么 |
> | 4 | [`prompts/README.md`](prompts/README.md) | 现在该跑哪一步 |
>
> 四层之外的文档都是**查阅型**：需要时点进去，不需要时不用读。

---

## 按场景找

| 我想…… | 看这里 |
|---|---|
| 知道这工具能做什么、怎么装、怎么配第一条通道 | [`../README.md`](../README.md) |
| 知道项目接下来怎么走、阶段之间卡在哪 | [`roadmap.md`](roadmap.md) |
| 知道现在该跑哪条提示词 | [`prompts/README.md`](prompts/README.md) |
| 知道哪些事已经拍板了、为什么这么定 | [`prompts/DECISIONS.md`](prompts/DECISIONS.md) |
| 知道「功能不能坏」的 12 条是什么 | [`functional-baseline.md`](functional-baseline.md) |
| 配某个通道（微信测试号 / Bark / 企业微信 / SMTP …） | [`channels/`](channels/) |
| 排查「收不到通知」 | [`troubleshooting.md`](troubleshooting.md) |
| 了解凭证存哪、日志怎么脱敏 | [`security.md`](security.md)、[`../SECURITY.md`](../SECURITY.md) |
| 理解事件怎么从宿主进来、怎么发出去 | [`architecture.md`](architecture.md) |
| 知道为什么从 tkinter 换到 pywebview | [`adr/ADR-007-pywebview-over-tkinter.md`](adr/ADR-007-pywebview-over-tkinter.md) |
| 知道某个目录为什么这么摆 | [`repo-layout.md`](repo-layout.md) |
| 查历史文档（tkinter 时代的规范与实测） | [`archive/`](archive/) —— **只读，不代表当前事实** |

---

## 全部文档

### 顶层

| 文件 | 一句话 |
|---|---|
| [`architecture.md`](architecture.md) | 事件怎么从宿主进来、怎么收敛、怎么发出去。**UI 选型一节已作废，见 ADR-007** |
| [`functional-baseline.md`](functional-baseline.md) | 12 条不能坏的行为，W6 的验收基准 |
| [`repo-layout.md`](repo-layout.md) | 目录为什么这么摆，`tools/` 迁移记录 |
| [`roadmap.md`](roadmap.md) | 全项目路线图：逐项去向 + 阶段划分 + 风险登记册 |
| [`security.md`](security.md) | 凭证存哪、日志怎么脱敏、URL 怎么校验 |
| [`troubleshooting.md`](troubleshooting.md) | 收不到通知时的排查顺序 |

### 子目录

| 目录 | 内容 |
|---|---|
| [`adr/`](adr/) | 7 条决策记录（[索引](adr/README.md)）。ADR-006 已被 007 取代，正文都保留 |
| [`channels/`](channels/) | 10 个通道的配置步骤与各自的坑（[索引](channels/README.md)） |
| [`prompts/`](prompts/) | 现行提示词 C0.1 + W0–W8 + P5，以及决策表（[索引](prompts/README.md)） |
| [`reviews/`](reviews/) | 01–04、07 审查快照（[索引](reviews/README.md)）。历史记录，不改 |
| [`archive/`](archive/) | tkinter 时代的文档：`design.md`、`design-redesign.md`、`perf.md`、外来方案原文、旧提示词 P0–P4。**顶部有归档横幅，读到就先看横幅** |

---

## 维护规则

1. **新增顶层文档** → 「按场景找」和「全部文档 · 顶层」各加一行。
2. **归档文档** → 从上面两张表删掉，不再单列；`archive/` 那一行已经涵盖。
   归档时用 `git mv`，并在文件顶部加状态横幅（正文不改）。
3. **决策变更** → 新 ADR 取代旧 ADR：旧 ADR 只改状态行，正文保留；`adr/README.md` 索引同步。
4. **改完跑一次断链检查** —— 脚本在
   [`prompts/C0-清场与旧资产归档.md`](prompts/C0-清场与旧资产归档.md) §5 第 3 条。
   必须 0 条。
5. **不在这里摘抄别的文档的内容。** 索引只负责指路。重复一份就是下一个「说反话」的信源。
