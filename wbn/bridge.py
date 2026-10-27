"""暴露给 JS 的 Python 接口（pywebview 的 `js_api`）。

三个必踩的坑，都在这里堵住，不在前端兜：

1. **返回值必须可 JSON 序列化。** 返回 `Path` / `datetime` / 自定义对象时，
   pywebview 不会报错，前端静默收到 `undefined`。这里用 `json.dumps` 当判官，
   序列化不过就抛——开发期立刻看见，而不是上线后「某个字段偶尔不显示」。
2. **异常必须吞掉并转成 `{ok: false, error}`。** Python 异常传到 JS 只剩
   `[object Object]`，看不到 message。`_guard` 统一包一层，错误信息过
   `sanitize.redact` 再出门（里面可能有 webhook key）。
3. **中文路径。** 前端要用的路径一律 `str(p)` 传字符串，URL 由调用方
   （`app.py`）用 `Path.as_uri()` 生成，不在这里手拼 `file://`。

约定：所有方法返回 `{"ok": true, "data": ...}` 或 `{"ok": false, "error": "..."}`。
"""

from __future__ import annotations

import functools
import json
import re
import threading
from datetime import datetime

from wbn import channels, config, insight, sanitize

# 日志行形如：
#   [00:06:38] channel=wxmp ok=False msg=errcode=40037 模板 ID 无效
#   [00:06:38] test channel=log ok=True msg=已记录
# `channel=` 不一定紧跟时间戳（测试发送会多一个 `test`），所以中间用 .*?
_LOG_CHANNEL = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\].*?\bchannel=(\S+)")


def _ensure_json(value):
    """返回值不能 JSON 序列化就当场抛，不要让它悄悄变成 undefined。"""
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise TypeError(f"bridge 返回值不能 JSON 序列化：{e}") from e
    return value


def _guard(fn):
    """所有 bridge 方法都过这层：异常不外抛，返回值统一 {ok, data?, error?}。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return {"ok": True, "data": _ensure_json(fn(*args, **kwargs))}
        except Exception as e:
            return {
                "ok": False,
                "error": sanitize.redact(f"{type(e).__name__}: {e}", 300),
            }

    return wrapper


def _last_send_times() -> dict:
    """今天每条通道最后一次发送的时间，`{ctype: "HH:MM"}`。

    为什么读日志这件事在 bridge 里而不在 insight 里：W2 的任务书禁止改动
    `insight.py` 的行为，而 insight 目前没有「每通道最后发送时间」这个查询。
    挪进 insight 是 W3 的事（通道页要显示更细的发送历史，那时一起做，
    顺便把 `notify-*.log` 的解析抽成一个函数，避免两处各写一份正则）。
    """
    path = config.LOG_DIR / f"notify-{datetime.now():%Y-%m-%d}.log"
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out

    for line in text.splitlines():
        m = _LOG_CHANNEL.match(line)
        if m:
            out[m.group(2)] = m.group(1)[:5]      # 只留 HH:MM，秒没用
    return out


def _field_value(raw, ftype: str):
    """配置里的值 → 前端控件能直接吃的形态。

    **bool 必须是真布尔。** `_send_smtp` 里写的是 `if c.get("ssl", True)`，
    字符串 "false" 在那里是真值——传字符串会静默变成「永远开 SSL」。
    解析规则复用 `channels._on`，全项目只此一份。

    **int 反而要传字符串。** 现有 config.json 里 `port` 就是 `"465"`，
    通道实现自己 `int()`；这里跟着现状走，免得存回去类型变了，
    让 `git diff config.json` 冒出一堆无意义的引号增删。
    """
    if ftype == "bool":
        return channels._on(raw)
    if raw is None:
        return ""
    return str(raw)


class Bridge:
    """配置读写 + 通道页要的三件事（结构、状态、异步测试）。

    `on_activity` 是给 `app.py` 的存活探针用的：前端只要能调通任何一个方法，
    就说明 WebView2 真的活着。传 None 时行为完全不变（pytest 里就是这么用的）。

    `window` 是 pywebview 的窗口对象，**只给异步回调往回推结果用**。
    建窗口时还没有它，所以 `app.py` 建完再调 `_attach()` 补上。
    不在这里引入 pywebview 去拿 `webview.windows[0]`：bridge 要保持纯 Python，
    否则 pytest 直接调它就得多装一个 GUI 依赖。

    **它必须叫 `_window`，不能叫 `window`。** pywebview 的 `get_functions()`
    会 `dir()` 一遍 js_api 对象，对**每个公开的非可调用属性递归展开**
    （见 site-packages/webview/util.py）。`window` 是个 Window 对象，展开就是
    `native.AccessibilityObject.Bounds.Empty.Empty.Empty...` 无限递归，结果：
    报一屏 `Error while processing window.native...` 之后 API 注入失败，
    前端永远拿不到 `window.pywebview.api`，页面停在骨架屏，最后被
    `app.py` 的存活探针判死降级到 tkinter。**Python 侧完全看不出是这个原因。**
    """

    def __init__(self, on_activity=None, window=None):
        self._on_activity = on_activity
        self._window = window

    def _attach(self, window) -> None:
        """建完窗口再挂上。名字带下划线，不进 JS 的 API 面（见类注释）。"""
        self._window = window

    def _touch(self) -> None:
        if self._on_activity is None:
            return
        try:
            self._on_activity()
        except Exception:
            pass      # 探针自己出错不许影响正常调用

    # ------------------------------------------------------------ 配置

    @_guard
    def config_get(self) -> dict:
        """读磁盘上的配置。

        凭证是明文传过去的——本地进程间，不走网络，前端用 `type=password`
        显示。真要防的是「截图 / 录屏时泄露」，那属于前端的事。
        """
        self._touch()
        cfg = config.load()
        return {
            "cfg": cfg,
            "path": str(config.CONFIG_PATH),
            "theme": (cfg.get("ui") or {}).get("theme", "system"),
        }

    @_guard
    def config_save(self, patch: dict) -> dict:
        """**读-改-写**，不许整体覆盖。

        UI 打开期间 hook 进程可能改过配置（比如自动禁用某通道、写入
        token 缓存），拿内存里那份整存会把别人的改动冲掉。所以交给
        `insight.merge_save`：它重新 load，只覆盖 patch 里出现的字段。
        """
        self._touch()
        if patch is not None and not isinstance(patch, dict):
            raise TypeError(f"patch 必须是对象，收到 {type(patch).__name__}")
        res = insight.merge_save(patch or {})
        if not res.get("ok"):
            raise RuntimeError(res.get("error") or "保存失败")
        return {k: v for k, v in res.items() if k != "ok"}

    # ------------------------------------------------------------ 状态页

    @_guard
    def stats_today(self) -> dict:
        """今日统计。数字本身全部来自 `insight.stats()`，这里只做两件事：

        1. 把 `channels`（**已启用**通道数）改名成 `enabled_channels`。
           原名和「通道总数」只差两个字，前端写 `d.channels` 迟早会拿来当分母。
        2. 补 `total_channels`，给「启用通道 2/10」做分母。
        """
        self._touch()
        cfg = config.load()
        s = insight.stats(cfg)
        return {
            "today": s["today"],
            "ok": s["ok"],
            "fail": s["fail"],
            "enabled_channels": s["channels"],
            "total_channels": len(cfg.get("channels") or []),
            "recent": s["recent"],
        }

    @_guard
    def chain_sentence(self) -> dict:
        """侧栏底部那句「现在这条链路通不通」，带语义等级。"""
        self._touch()
        text, level = insight.chain_sentence(config.load())
        return {"text": text, "level": level}

    @_guard
    def channels_list(self) -> list:
        """通道列表：短名 + 状态徽章文案 + 语义等级 + 今天最后发送时间。

        状态文案直接复用 `insight.channel_state`，和 tkinter 版一字不差——
        两套 UI 对「已启用 / 已配置未启用 / 待配置」的措辞必须一致，
        否则用户会以为是两个功能。
        """
        self._touch()
        cfg = config.load()
        last = _last_send_times()
        out = []
        for ch in cfg.get("channels") or []:
            ctype = ch.get("type") or ""
            if not ctype:
                continue
            sub, level, lit = insight.channel_state(ctype, ch)
            out.append({
                "type": ctype,
                "label": channels.short_label(ctype),
                "state": sub,
                "level": level,
                "on": lit,
                "last": last.get(ctype),
            })
        return out

    # ------------------------------------------------------------ 通道页

    @_guard
    def channels_meta(self) -> dict:
        """通道页要的全部静态结构，一次给齐。

        前端**不硬编码 10 套表单**：`fields` 就是 schema，遍历它渲染控件。
        加一个通道只改 `CHANNEL_META` 一处，前端自动多出对应的输入框。

        元组 `(key, label, type, default, hint)` 在这里转成对象——元组是给
        Python 用的紧凑写法，JSON 里没有元组，前端也不好按下标取。
        """
        self._touch()
        groups = [
            {"label": label,
             "types": [t for t in types if t in channels.CHANNEL_META]}
            for label, types in channels.CHANNEL_GROUPS
        ]

        meta = {}
        for ctype, m in channels.CHANNEL_META.items():
            meta[ctype] = {
                "label": m.get("label") or ctype,
                "short": channels.short_label(ctype),
                "summary": m.get("summary") or "",
                "help": m.get("help") or "",
                "icon": m.get("icon") or "dot",
                "fields": [
                    {"key": key, "label": label, "type": ftype,
                     "default": default, "hint": hint}
                    for key, label, ftype, default, hint in (m.get("fields") or [])
                ],
            }

        return {
            "groups": groups,
            "meta": meta,
            "required": {k: list(v) for k, v in channels.REQUIRED.items()},
        }

    @_guard
    def channels_state(self) -> list:
        """每个通道的当前状态 + 已填的值。

        `subtitle` / `level` 直接用 `insight.channel_state()` 的结果，**不在前端
        重新判断**——判定逻辑只许有一份，否则两版界面会对同一个配置说两句话。
        """
        self._touch()
        cfg = config.load()
        out = []
        for ctype, m in channels.CHANNEL_META.items():
            ch = config.get_channel(cfg, ctype) or {}
            subtitle, level, lit = insight.channel_state(ctype, ch)

            values = {}
            for key, _label, ftype, default, _hint in (m.get("fields") or []):
                values[key] = _field_value(ch.get(key, default), ftype)

            out.append({
                "type": ctype,
                "enabled": bool(ch.get("enabled")),
                "ready": channels.is_ready(ctype, ch),
                "missing": channels.missing_fields(ctype, ch),
                "subtitle": subtitle,
                "level": level,
                "on": lit,
                "values": values,
            })
        return out

    @_guard
    def channel_test(self, ctype: str, fields: dict, cb_id: int) -> dict:
        """发一条测试消息。**立刻返回，结果用 evaluate_js 回推。**

        必须开线程：`insight.test_channel` 是同步 HTTP，最坏情况「取
        access_token 10s + 发消息 8s」，放主线程就是整个窗口冻住十几秒，
        Windows 直接把标题栏改成「未响应」。

        测的是**表单里当前的值**，不是磁盘上那份：用户改完还没保存就点测试，
        要试的是他眼前这一份（tkinter 版 `_collect()` 后再测，同语义）。
        """
        self._touch()
        ctype = str(ctype or "")
        if ctype not in channels.CHANNEL_META:
            raise ValueError(f"未知通道 {ctype!r}")

        cfg = config.load()
        ch = config.get_channel(cfg, ctype)
        if not ch:
            raise RuntimeError(f"配置里没有通道 {ctype}")

        known = channels.FIELD_LABELS.get(ctype, {})
        for key, value in (fields or {}).items():
            if key in known:            # 只认 schema 里有的字段，别的一概不写
                ch[key] = value

        def work() -> None:
            try:
                ok, msg = insight.test_channel(cfg, ctype)
            except Exception as e:      # 线程里的异常没人接，必须自己兜住
                ok, msg = False, f"{type(e).__name__}: {e}"
            # 错误原文是远端返回的 body，属于不可信文本：先脱敏（抹 key），
            # 前端那边还会用 textContent 显示（转义）。两道都要有。
            self._push(cb_id, ok, {"msg": sanitize.redact(str(msg), 600)})

        threading.Thread(target=work, daemon=True, name="wbn-chan-test").start()
        return {"started": True, "cb": cb_id}

    @_guard
    def channel_fetch_openid(self, fields: dict, cb_id: int) -> dict:
        """微信测试号：从关注者列表里取 openid，同样异步回推。

        只取 appid / secret 两个字段——`fetch_openid` 只需要它们，
        整个表单（含 openid 自己）传过去没有意义。
        """
        self._touch()
        picked = {k: v for k, v in (fields or {}).items()
                  if k in ("appid", "secret")}

        def work() -> None:
            try:
                ids, err = insight.fetch_openid(picked)
            except Exception as e:
                ids, err = [], f"{type(e).__name__}: {e}"
            self._push(cb_id, not err, {
                "ids": list(ids or []),
                "error": sanitize.redact(str(err or ""), 300),
            })

        threading.Thread(target=work, daemon=True, name="wbn-chan-openid").start()
        return {"started": True, "cb": cb_id}

    # ------------------------------------------------------------ 内部

    def _push(self, cb_id, ok: bool, data: dict) -> None:
        """把异步结果推回页面：`window.__wbnCb({...})`。

        **payload 必须 json.dumps。** `msg` 里是用户和远端服务写的文本，
        直接拼进 JS 字符串，一个引号就能把脚本拼断——而
        `window.pywebview.api` 就在同一个上下文里，拼断之后能做的事很多。

        插入位置只能是**函数调用的参数位**（JSON 是 JS 对象字面量的子集，
        作为实参天然合法），绝不能拼进字符串字面量。

        U+2028 / U+2029 在 JSON 里允许裸写，但老 JS 引擎把它们当行分隔符，
        会把一行脚本劈成两行。Chromium 现在没事，转义一下是零成本的保险。
        """
        window = self._window
        if window is None:
            return

        payload = json.dumps(
            {"id": cb_id, "ok": bool(ok), "data": data}, ensure_ascii=False)
        payload = payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

        try:
            window.evaluate_js(f"window.__wbnCb({payload})")
        except Exception:
            pass        # 窗口已经关了，结果没人收，正常


# ---------------------------------------------------------------- 后续阶段
#
# 这些方法现在**不写空壳**。理由：写了之后 `window.pywebview.api` 上会多出
# 一堆「存在但一调就报错」的方法，前端的特性检测（`if (api.channels_list)`）
# 会误判为已实现，比完全没有更难查。要加的时候一起加。
#
#   open_path(self, target)              W5
#   app_quit(self)                       W5
