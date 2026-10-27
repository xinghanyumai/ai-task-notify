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

from wbn import config, insight, sanitize


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


class Bridge:
    """Phase 1 只有读写配置两个方法。其余留到 W2/W3/W5，见文件末尾。

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


# ---------------------------------------------------------------- 后续阶段
#
# 这些方法现在**不写空壳**。理由：写了之后 `window.pywebview.api` 上会多出
# 一堆「存在但一调就报错」的方法，前端的特性检测（`if (api.channels_list)`）
# 会误判为已实现，比完全没有更难查。要加的时候一起加。
#
#   channels_list(self)                  W3   通道列表 + 每项状态
#   channel_test(self, ctype, fields)    W3   必须开线程，不能阻塞 UI 线程
#   stats_today(self)                    W2
#   recent_logs(self, limit=20)          W2
#   chain_sentence(self)                 W2
#   open_path(self, target)              W5
#   app_quit(self)                       W5
