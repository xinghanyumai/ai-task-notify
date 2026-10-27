# ADR-007：UI 改用 pywebview 加系统 WebView2

- 状态：已采纳
- 日期：2026-10-27
- 取代：[ADR-006](ADR-006-python-tkinter.md)

## 背景

ADR-006 选 Tkinter 自绘，理由是「解压就能跑、不依赖 pip」。这个理由在通知链路上
成立，在界面层不成立。

自绘走到 P1 时，界面代码已经 6084 行（`ui.py` 2376 + `widgets.py` 2520 +
`theme.py` 474 + `tokens.py` 202 + `anim.py` 180 + `splash.py` 332），
换来的是**一个仍然像 2015 年工具的界面**。不是手艺问题，是能力问题：

| 能力 | Tkinter |
|---|---|
| 抗锯齿圆角 | 要 Canvas 逐点画，8px 圆角在 200% 缩放下锯齿可见 |
| 多层阴影 | 只能叠实心圆角矩形模拟，深色下几乎看不出层次 |
| focus 光环 | 没有，只能自己画一圈描边 |
| 字距 / 字重微调 | 只有 `letter-spacing` 这一档都没有 |
| 真正的样式表 | 只有 `option_add`，改一个状态色要遍历控件树 |
| 系统 WebView 级别的文本渲染 | 用的是 GDI 老路径，中文小字号糊 |

这几项**物理上做不到**，不是「再堆两万行就能做到」。继续投入的边际收益趋近于零。

## 决策

界面层从 Tkinter 自绘迁到 **pywebview + 系统 WebView2 + 手写 CSS 设计系统**。

边界划得很清楚：

- **只有界面换**。`hook.py` / `dispatch.py` / `tray.py` / `install.py` /
  `selftest.py` 和 `wbn/` 下的 channels / pipeline / events / filters /
  render / store / config / sanitize **一行不改**。
- 新增 `app.py`（pywebview 壳）与 `wbn/bridge.py`（暴露给前端的 API 类），
  前端是 `webui/` 下的原生 HTML + CSS + JS，**不引 React / Vue / 打包工具链**。
- 旧的 6 个 tkinter 文件（`ui.py`、`wbn/widgets.py`、`wbn/theme.py`、
  `wbn/tokens.py`、`wbn/anim.py`、`wbn/splash.py`）在 W5 删除，不留 fallback。

## 代价

**多一个第三方依赖。** ADR-006 的「核心链路零第三方依赖」被打破，这是有意识的
取舍：核心链路（hook → dispatch → pipeline → channels）**仍然零依赖**，
只有 UI 层引入 `pywebview`。通知链路在 UI 崩掉时照常工作，这一点没有变。

**需要 WebView2 运行时。** Win10 1803 以上、Win11 自带；缺失时要引导用户装
微软的 Evergreen Bootstrapper（2MB）。打包体积增加约 10MB。

**桥接是新的故障面。** 前端调 `window.pywebview.api.*`，异常必须全部在 Python
侧吞掉并返回 `{ok, error}`，否则前端会拿到一个 `undefined` 然后白屏。
阻塞调用（`channel_test`，HTTP 超时 8 秒）必须开线程 + 回调，不能同步等。

**调试方式变了。** 以前断点打在 Python 里就能看控件状态；现在要开
`webview.start(debug=True)` 的 DevTools，问题可能在前端也可能在后端。

**自绘窗口边框要自己实现拖拽与缩放。** 这是拿质感换来的额外工作。

## 备选方案

**Electron。** 质感上限一样，但安装包 150MB 起，启动 1 秒以上。一个通知工具
不配这个体积。

**Tauri。** 体积最小（10MB），质感最好。但要写 Rust，还要把 Python 打成 sidecar
一起管，为一个 5 页的设置界面不值。

**PySide6 + QSS。** 原生桌面质感能做到，但 QSS 不是 CSS：没有 flex、没有
`box-shadow`、没有 `transition`，写的还是另一套方言，等于换一个天花板。

**customtkinter / ttkbootstrap。** 不用重写业务，但天花板还是 Tk 的，
圆角和阴影依旧是贴图，问题只是被推迟。

**继续用 Tkinter 自绘。** 已经在 P0–P1 投入两轮，产出是 6084 行代码和一个
仍然不够好的界面。第三次投入没有理由期望不同结果。

## 与 ADR-006 的关系

ADR-006 的**判断没错，适用范围错了**。它把「不依赖 pip」当成全项目铁律，
而实际上这条只对通知链路关键——用户装了 Python 就能收通知，这是它存在的意义。
设置界面一年打开几十次，多一个 pip 包换真 CSS 能力，这笔交易划算。

ADR-006 原文保留在 [`ADR-006-python-tkinter.md`](ADR-006-python-tkinter.md)，
不修改正文，因为「当时为什么选 Tk」和「后来为什么改」是两条独立的判断，
抹掉前者会让后者失去参照。
