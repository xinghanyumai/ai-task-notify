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


class Bridge:
    """只读接口给状态页（stats / 链路陈述 / 通道列表），写入只有 config_save。

    `on_activity` 是给 `app.py` 的存活探针用的：前端只要能调通任何一个方法，
    就说明 WebView2 真的活着。传 None 时行为完全不变（pytest 里就是这么用的）。
    """

    def __init__(self, on_activity=None):
        self._on_activity = on_activity

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


# ---------------------------------------------------------------- 后续阶段
#
# 这些方法现在**不写空壳**。理由：写了之后 `window.pywebview.api` 上会多出
# 一堆「存在但一调就报错」的方法，前端的特性检测（`if (api.channels_list)`）
# 会误判为已实现，比完全没有更难查。要加的时候一起加。
#
#   channel_test(self, ctype, fields)    W3   必须开线程，不能阻塞 UI 线程
#   open_path(self, target)              W5
#   app_quit(self)                       W5
