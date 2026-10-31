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
import sys
import threading
from datetime import datetime, timedelta

from wbn import channels, config, events, filters, insight, logs, render, sanitize, store


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


def _install():
    """懒加载仓库根的 `install` 模块。

    为什么不在文件顶部 import：`wbn/` 是包，`install.py` 在仓库根。顶层
    import 会让「import wbn.bridge」依赖「根目录已经在 sys.path 上」，
    而测试是直接 `from wbn import bridge` 的（没有 app.py 帮忙插路径）。
    放函数里，谁用谁付一次代价，`wbn` 仍然能单独 import。
    """
    root = str(config.ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    import install

    return install


def _http_timeout() -> int:
    """`config.advanced.http_timeout`，和 `pipeline.send_test` 读的是同一个键。

    下发到前端只为一件事：让它算出比后端**更长**的兜底超时。这里不做
    上下限裁剪——裁剪会让前端算出的值和后端实际用的值脱钩，那就白下发了。
    """
    try:
        return int(config.load().get("advanced", {}).get("http_timeout") or
                   channels.SEND_TIMEOUT)
    except (TypeError, ValueError):
        return channels.SEND_TIMEOUT


def _log_stamp() -> tuple[int, ...]:
    """昨天 + 今天两个日志文件的 mtime（纳秒）。文件不存在记 0。

    用它而不是「距上次读过了几秒」：**时间是猜的，mtime 是事实**。
    「缓存 5 秒」在「用户点完测试立刻刷新」这个最常见的动作上会读到旧数据——
    测试刚写了一行日志，mtime 变了，缓存必须当场失效。反过来，日志没动的时候
    重复读纯属浪费，而且三次读之间可能夹进一条新记录，三个出口就不一致。

    跨零点时日期变了，stamp 里的两个 mtime 自然全变，缓存失效。

    文件被删掉记 0 而不是报错：日志轮转 / 用户手动清理都不该让页面白屏。

    路径问 `logs.log_path()`，不在这里拼文件名——文件名规则只许有一个
    定义处（W3.8 收敛条件第 1 条：一处读日志）。
    """
    now = datetime.now()
    out = []
    for offset in (1, 0):
        try:
            out.append(logs.log_path(now - timedelta(days=offset)).stat().st_mtime_ns)
        except OSError:
            out.append(0)
    return tuple(out)


def _last_send_times(facts: logs.Facts | None = None) -> dict:
    """每条通道的**两件事实**（今天 + 昨天），`{ctype: {…}}`。

    `facts` 给进来就只做「dataclass → 前端契约的普通 dict」这一步，**不再读文件**
    （bridge 一次页面加载共用一份，W3.8c）；不给就自己 `logs.read()`。

    **它不走缓存，这是有意的。** 它是模块级函数、没有 `self`，要缓存就得挂
    全局变量——那是比「多读一次文件」坏得多的东西。而且 312 个测试直接调它，
    它们要的是「读一次真数据」，不是「读一次别人留下的缓存」。

    解析、分槽、「今天的记录覆盖昨天的」全部在 `logs` 里，这里只摊平——
    `channels_list` / `channel_state` / `delivery_health` 都按 dict 下标读，
    当初 tkinter 版的状态页也走这条路（那个版本已随 W5 删除）。

    | 字段 | 来源 | 消费者 |
    |---|---|---|
    | `time` / `day` / `test` | **最近一条**记录（含测试） | 通道列表的「最后发送时间」、状态页 `last` |
    | `ok` / `msg` / `ok_day` | 最近一条**非测试**记录 | `delivery_health()` / `channel_state()` |

    **`ok` 是三态**：`True` / `False` / **`None`**。`None` 表示「真实投递
    一次都没发生过」，既不是成功也不是失败。消费者必须写 `is False` /
    `is not None`，**不许写 `not last.get("ok")`**——`None` 是 falsy，
    那会把「没发生过」判成「失败」，正是 W3.7 要修的病。

    函数名保留：312 个测试直接调它，改名等于改契约。
    """
    if facts is None:
        facts = logs.read(days=2)
    return {
        ctype: {
            "time": f.time, "day": f.day, "test": f.test,
            "ok": f.ok, "msg": f.msg, "ok_day": f.ok_day,
        }
        for ctype, f in facts.last_by_channel().items()
    }


def _last_label(rec: dict | None) -> str | None:
    """「最后发送时间」的展示文本。

    跨天必须标出来——昨天 23:50 和今天 00:10 只给 `23:50` / `00:10` 的话，
    用户会以为是同一类东西，而它们隔了一整夜。零点后清一色「未发送」
    更是直接说谎：昨天明明发过。
    """
    if not rec:
        return None
    t = rec.get("time") or ""
    return f"昨天 {t}" if rec.get("day") == "yesterday" else t


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


# ---------------------------------------------------------------- 页面结构表
#
# 触发页的六个事件、模板页的七个类型、22 个占位符——这三张表**都已经有定义处**，
# 这里只做转手，不再新写一份内容：
#   · 事件键序 → config.DEFAULT_CONFIG["triggers"]
#   · 事件中文名 → events.KIND_LABELS
#   · 模板类型 → config.DEFAULT_CONFIG["templates"]
#   · 占位符 → render.PLACEHOLDERS
#
# 唯一「新」的是下面这 6 条事件说明：它们只存在于 `ui.py:KIND_DESC`（GUI 层，
# bridge 是纯逻辑层，不能反向依赖它）。这是全项目第三处事件中文名的落点，
# 已知重复，改动必须两处一起改；W4 不插队重构，记 roadmap R34。

_KIND_DESC = {
    "task_done": "一轮任务正常跑完",
    "task_failed": "明确报错或中断",
    "need_input": "助手在等你回答",
    "tool_failed": "工具连续失败（噪音较大）",
    "session_start": "开始新会话",
    "session_end": "会话关闭",
}

# 触发页的字段表。标签、说明、顺序**逐字取自 `ui.py:_page_triggers`**——
# 两套界面对同一个字段用两句话，用户会以为是两个功能。
#
# `kind` 决定控件：`int` → 数字框；`list` → 逗号分隔文本；`text` → 普通文本；
# `bool` → 开关。
#
# `lo` / `hi` 是**前端失焦校正**用的范围，Python 侧不做范围校验
# （`filters.check` 只有 `float()` / `int()`，不裁上下限），所以这不是
# 「第二套规则」，是唯一一套。上限取「再大基本就是手滑」的量级：
# 最短耗时 1 小时、去重 1 天、每分钟 60、每小时 600、工具失败 20 次。
_TRIGGER_FIELDS = (
    # hint 必须写明「总时长」：2026-10-30 用户看到日志里「耗时 220ms」以为该通知，
    # 实际那个 turn 走了 6 秒、被 20 秒门槛挡掉了。模型耗时 ≠ 一轮的总时长，
    # 不写清这一句，用户会以为门槛算错了。
    {"key": "min_duration_seconds", "kind": "int", "lo": 0, "hi": 3600,
     "label": "最短耗时（秒）",
     "hint": "从你发出消息到任务结束的总时长；低于它不通知。"
             "不是模型耗时（那个通常只有几百毫秒）"},
    {"key": "quiet_hours", "kind": "text", "lo": None, "hi": None,
     "label": "静默时段", "hint": "如 23:00-08:00，多段用逗号分隔"},
    {"key": "dedup_seconds", "kind": "int", "lo": 0, "hi": 86400,
     "label": "去重窗口（秒）", "hint": "相同内容在该时间内只推一次"},
    {"key": "max_per_minute", "kind": "int", "lo": 0, "hi": 60,
     "label": "每分钟上限", "hint": "防止突发刷屏"},
    {"key": "max_per_hour", "kind": "int", "lo": 0, "hi": 600,
     "label": "每小时上限", "hint": "长任务密集时可调高"},
    {"key": "tool_fail_threshold", "kind": "int", "lo": 0, "hi": 20,
     "label": "工具连续失败阈值", "hint": "连续失败多少次才通知"},
    {"key": "projects_include", "kind": "list", "lo": None, "hi": None,
     "label": "只通知这些项目", "hint": "按路径子串匹配，逗号分隔；留空表示全部"},
    {"key": "projects_exclude", "kind": "list", "lo": None, "hi": None,
     "label": "排除这些项目", "hint": "逗号分隔"},
    {"key": "keywords_include", "kind": "list", "lo": None, "hi": None,
     "label": "关键词命中", "hint": "摘要里包含任一词才通知；留空不限制"},
    {"key": "question_as_need_input", "kind": "bool", "lo": None, "hi": None,
     "label": "助手最后一句是问句时，归类为「等你确认」而不是「已完成」",
     "hint": ""},
)


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
    `app.py` 的存活探针判死降级到原生提示框（当时是 tkinter，W5 起已下线）。
    **Python 侧完全看不出是这个原因。**
    """

    def __init__(self, on_activity=None, window=None):
        self._on_activity = on_activity
        self._window = window
        # 前端有没有未保存的改动。**只存来源，不做判断**——「什么算改动」
        # 是前端的事（草稿 vs 磁盘），后端不该再实现一遍比较逻辑。
        # 关窗时 `app.py` 读 `_unsaved` 决定要不要拦。
        #
        # 为什么是集合而不是一个布尔（W4 审计 A12）：通道页和触发页都会改配置，
        # 各自调 `set_dirty()`。共用一布尔的话，编辑触发页之后切到通道页，
        # 通道页一看自己没脏就发 `set_dirty(false)`，触发页的改动当场失去保护——
        # 关窗不提示，改了一半的规则直接丢。按来源记，取并集才对。
        self._dirty_sources: set[str] = set()
        # 一次页面加载共用的一份日志（W3.8c）。三个 bridge 方法各读一次文件
        # 的时候，三次读之间只要夹进一条新投递，同一屏上就能出现
        # 「今日通知 2 / 通道失败 3」这种互相矛盾的数——和 W3.7 那个
        # 「侧栏绿、列表红」同类，只是触发条件更隐蔽。
        self._facts: logs.Facts | None = None
        self._facts_stamp: tuple[int, ...] | None = None

    def _get_facts(self) -> logs.Facts:
        """一次页面加载共用一份 `Facts`（W3.8c）。

        **按文件 mtime 失效，不按时间。** 用「缓存 5 秒」会让用户点完
        「测试此通道」立刻刷新时读到旧数据——测试刚写了一行日志，mtime 变了，
        缓存必须当场失效。反过来，日志没动的时候重复读纯属浪费。

        缓存放这里而不是 `logs.py`：那一层是纯函数（`Facts` 是 frozen
        dataclass、`read()` 无副作用），加缓存就让它变成有状态；而且
        「什么时候失效」是应用层的事，不是解析层的事。

        `_facts` / `_facts_stamp` 必须是私有的：pywebview 的 `get_functions()`
        会递归展开公开的非可调用属性（见类注释），一个 `Facts` 上去就是
        无限递归 → API 注入失败 → 页面停在骨架屏。
        """
        stamp = _log_stamp()
        if self._facts is None or self._facts_stamp != stamp:
            self._facts = logs.read(days=2)
            self._facts_stamp = stamp
        return self._facts

    def _attach(self, window) -> None:
        """建完窗口再挂上。名字带下划线，不进 JS 的 API 面（见类注释）。"""
        self._window = window

    @property
    def _unsaved(self) -> bool:
        """有没有未保存的改动。给 `app.py` 的关窗拦截用，**不给 JS 调**。

        故意做成私有：`test_bridge_has_no_public_data_attributes` 钉着
        「js_api 上只许有方法」。公开的 `is_dirty` 属性会被 pywebview 的
        `get_functions()` 当成数据属性扫进去（就是 `self.window` 那个
        「无限递归 → API 注入失败」的形状），而这个值本来也只有 Python 侧
        的关窗回调要读。前端只该写，不该读。

        语义是**所有页面里只要有一个脏**：`_dirty_sources` 存的是「谁脏了」，
        取 `bool()` 就是「关窗要不要拦」。
        """
        return bool(self._dirty_sources)

    def _touch(self) -> None:
        if self._on_activity is None:
            return
        try:
            self._on_activity()
        except Exception:
            pass      # 探针自己出错不许影响正常调用

    @_guard
    def log_js_error(self, message: str, source: str = "") -> dict:
        """前端把未捕获的 JS 异常写进运行日志，返回 `{"logged": True}`。

        **这是 `theme.install_tk_exception_hook` 的等价物。** W5 删掉 `theme.py`
        时那条钩子一起没了——它存在的原因是「Tk 回调异常默认只写 stderr，
        pythonw.exe 下 stderr 是 None，等于消失」（Toast 的 NameError 就是这么
        活过 8 个提交的）。webview 里同一类问题换了个形状：页面里抛一个
        uncaught error，DevTools 能看到，但用户不会开 DevTools，日志里什么都没有，
        现象还是「点了没反应」。

        所以 `webui/assets/app.js` 里挂了 `error` / `unhandledrejection` 两个监听，
        把消息转到这条方法。**这是「换尺子」不是「取消检查」**——旧尺子量 Tk 回调，
        新尺子量页面异常，落在同一个 `logs/notify-*.log` 里。
        """
        text = sanitize.redact(str(message or ""), 500)
        where = sanitize.redact(str(source or ""), 200)
        store.log(f"[js] {text}" + (f" @ {where}" if where else ""))
        return {"logged": True}

    # ------------------------------------------------------------ 窗口状态

    @_guard
    def set_dirty(self, dirty: bool, source: str = "channels") -> dict:
        """前端把「有没有未保存的改动」告诉后端，供关窗时判断。

        **只存来源，不做判断**：`app.py` 的 `closing` 回调读 `_unsaved`，
        决定要不要弹确认框。判断「什么算改动」只有前端一份（草稿 vs 磁盘），
        后端再实现一遍就是两份逻辑，迟早不一致。

        `source` 是「谁在报」（`"channels"` / `"triggers"` / `"templates"`）。
        每个页面只对自己那一格负责，**不许替别人报干净**——`set_dirty(false)`
        只把 `source` 那一格抹掉，别的页面脏着就仍然拦关窗（W4 审计 A12）。

        默认值 `"channels"` 是为了不改既有调用方（`channels.js` 只传一个参数）
        和既有测试（`set_dirty(True)` 的返回值必须仍是 `{"dirty": True}`）。

        前端调用频率受 200ms 防抖约束，见 `channels.js` 的 `syncDirty()`。
        """
        src = str(source or "channels")
        if dirty:
            self._dirty_sources.add(src)
        else:
            self._dirty_sources.discard(src)
        return {"dirty": self._unsaved}

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

    # ------------------------------------------------------------ 触发页 / 模板页

    @_guard
    def ui_meta(self) -> dict:
        """触发页与模板页要的**静态结构**，一次给齐。

        为什么不在 JS 里写死：这几张表都已经有 Python 侧的定义处，
        前端再抄一份就是「两处说同一件事、各自算一遍」（W3.7 的病因）。
        具体到每一张：

        | 表 | 唯一定义处 | 抄一份会怎样 |
        |---|---|---|
        | 6 个事件开关 | `config.DEFAULT_CONFIG["triggers"]` + `events.KIND_LABELS` | 少一个事件就少一个开关，那个事件永远关不掉 |
        | 7 个模板类型 | `config.DEFAULT_CONFIG["templates"]` | 少一个类型，它的模板永远改不了，但它照样会发出去 |
        | 22 个占位符 | `render.PLACEHOLDERS` | 任务书 §2.1 那 6 个里有 `{title}` / `{detail}`，本项目**没有**这两个变量 |
        | 字段标签与说明 | `ui.py:_page_triggers`（逐字） | 两套界面对同一字段用两句话，用户以为是两个功能 |

        `enabled` / `any_channel_on` 只回**布尔**、不回计数：状态页已经有一个
        「启用通道 2/10」，这里再算一遍就是第二个数字，迟早会和那个对不上。
        """
        self._touch()
        cfg = config.load()

        kinds = [
            {
                "key": k,
                "label": events.KIND_LABELS.get(k, k),
                "desc": _KIND_DESC.get(k, ""),
            }
            for k in config.DEFAULT_CONFIG["triggers"]
        ]
        template_kinds = [
            {"key": k, "label": events.KIND_LABELS.get(k, k)}
            for k in config.DEFAULT_CONFIG["templates"]
        ]
        return {
            "kinds": kinds,
            "template_kinds": template_kinds,
            "fields": [dict(f) for f in _TRIGGER_FIELDS],
            "placeholders": [{"token": t, "desc": d} for t, d in render.PLACEHOLDERS],
            "enabled": bool(cfg.get("enabled", True)),
            # 只判「有没有一条」，不给数字（见 docstring 最后一段）
            "any_channel_on": bool(config.enabled_channels(cfg)),
        }

    @_guard
    def triggers_save(self, triggers: dict, filters_raw: dict) -> dict:
        """触发页保存：开关直接覆盖，**过滤器在 Python 侧规整**。

        为什么不让前端把 `"20"` / `"a,b"` 转好再存：转换规则在
        `insight.normalize_filters`，它的 docstring 原文就写着「webview 版要用
        同一套」。前端自己 `Number()` / `split(",")` 一遍就是第二套规则，
        分叉之后症状极隐蔽——`projects_include` 存成字符串时，`_match_any`
        会**逐字符**匹配，过滤规则静默失效，界面一切正常（W4 审计 A8 / A11）。

        返回规整后的值，页面拿它回填输入框：用户敲 `1e3` 这种，落盘是 `1000`，
        界面要跟着变成 `1000`，否则「看到的」和「存下的」又不是一个东西。
        """
        self._touch()
        patch = {
            "triggers": {str(k): bool(v) for k, v in (triggers or {}).items()},
            "filters": insight.normalize_filters(filters_raw or {}),
        }
        res = insight.merge_save(patch)
        if not res.get("ok"):
            raise RuntimeError(res.get("error") or "保存失败")
        cfg = res.get("cfg") or {}
        return {
            "triggers": cfg.get("triggers") or {},
            "filters": cfg.get("filters") or {},
            "turned_on": list(res.get("turned_on") or []),
        }

    @_guard
    def quiet_hours_check(self, spec: str) -> dict:
        """静默时段的格式检查。**复用 `filters` 的解析器，不写第二套规则。**

        为什么必须校验（W4 审计 A6）：`filters.in_quiet_hours` 遇到解析不了的段
        是 `continue`，整段静默**静默失效**——用户填了 `23:00~08:00`（波浪号），
        以为设了静默，实际一条也没拦住，而界面上一切正常。

        判据就是 `filters._parse_hhmm` 本身：能过就是合法。这里只做「逐段拆开、
        逐个问它」，不重新实现 `HH:MM` 的解析——那正是「两套规则必然分叉」的入口。

        `active_now` 是「按这份设置，现在处于静默时段吗」，由
        `filters.in_quiet_hours` 算。页面只在显示 / 失焦时算一次，所以文案要写
        「按已保存的时段算」，不许写成实时的。
        """
        text = str(spec or "").strip()
        parts = [p.strip() for p in text.split(",") if p.strip()]

        bad: list[str] = []
        for part in parts:
            if "-" not in part:
                bad.append(part)
                continue
            a, b = part.split("-", 1)
            if filters._parse_hhmm(a) is None or filters._parse_hhmm(b) is None:
                bad.append(part)

        return {
            "ok": not bad,
            "bad": bad,
            "active_now": filters.in_quiet_hours(text),
        }

    @_guard
    def template_preview(self, kind: str, title: str, content: str) -> dict:
        """模板预览。**渲染只走 `insight.preview`，前端不做字符串替换。**

        前端自己替换 `{project}` 看起来是十行代码的事，但 `render.py` 里有
        换行处理、`channels.plain_text` 里有 markdown 剥离、`render_message`
        里有标题回落——抄一遍就是两套规则，用户会发现「预览是这样，手机上不是」
        （任务书 §2.2）。

        返回的 `vars_missing` 是**模板原文里有、字段表里没有**的占位符。它们在
        渲染阶段已经被替换成空字符串了，所以正文里没有位置可以标色——页面只能
        在预览下面逐条列出来（W4 审计 B6）。
        """
        self._touch()
        data = insight.preview(
            config.load(), str(kind or ""), str(title or ""), str(content or ""))
        return {
            "title": data.get("title") or "",
            "body": data.get("body") or "",
            "vars_used": list(data.get("vars_used") or []),
            "vars_missing": list(data.get("vars_missing") or []),
        }

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
        s = insight.stats(cfg, self._get_facts())
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
        """侧栏底部那句「现在这条链路通不通」，带语义等级。

        `last` 一定要传：只读配置的话，微信连续失败十几次它照样说
        「送到你手机」+ 绿点——界面在替失败背书（W3.4）。
        """
        self._touch()
        text, level = insight.chain_sentence(
            config.load(), _last_send_times(self._get_facts()))
        return {"text": text, "level": level}

    @_guard
    def channels_list(self) -> list:
        """通道列表：短名 + 状态徽章文案 + 语义等级 + 今天最后发送时间。

        状态文案直接复用 `insight.channel_state`，和 tkinter 版一字不差——
        两套 UI 对「已启用 / 已配置未启用 / 待配置」的措辞必须一致，
        否则用户会以为是两个功能。

        `last` 传进去之后，最近一次投递失败的通道会显示「最近一次失败：…」
        并转 `danger`。`last` 字段本身仍然是时间字符串（前端契约不变）。
        """
        self._touch()
        cfg = config.load()
        last = _last_send_times(self._get_facts())
        out = []
        for ch in cfg.get("channels") or []:
            ctype = ch.get("type") or ""
            if not ctype:
                continue
            rec = last.get(ctype)
            sub, level, lit = insight.channel_state(ctype, ch, rec)
            out.append({
                "type": ctype,
                "label": channels.short_label(ctype),
                "state": sub,
                "level": level,
                "on": lit,
                "last": _last_label(rec),
            })
        return out

    # ------------------------------------------------------------ 开机自启

    @_guard
    def autostart_get(self) -> dict:
        """开机自启的真实状态：`{enabled, path, ok, msg}`。

        **只认启动文件夹里那个 `.lnk`**——不读 `config.json`，也不读注册表。
        往 config 里存一个 `autostart: true` 会造出第二真相：配置说开、实际
        没开，而用户看的是界面。

        `ok=False` 表示**读不到启动目录**（非 Windows / 目录被删 / 权限）。
        这时 `enabled` 没有意义，界面必须说「无法读取启动目录」，
        不能说「已关闭」——那是替系统撒谎。
        """
        self._touch()
        return _install().autostart_report()

    @_guard
    def autostart_set(self, on: bool) -> dict:
        """开 / 关自启，**操作后重读真实状态**。

        绝不回显入参：用户点了「开」但建快捷方式失败时，界面必须显示关着。
        「报成功」不算成功，磁盘上变了才算——这是本项目一贯的判据
        （W9 §1.2 第 1 条）。
        """
        self._touch()
        mod = _install()
        done, note = mod.set_autostart(bool(on))
        snap = mod.autostart_report()          # ← 重读，不是回显
        if not snap["ok"]:
            return snap                        # 读不到就照实说读不到
        return {
            "enabled": snap["enabled"],
            "path": snap["path"],
            "ok": done,
            "msg": note,
        }

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
            # 前端兜底超时**必须由这两个值派生**，不许自己写死（W3.1 §2.2）：
            # 后端一次发送最坏要串两次请求（先换 token，再发消息），
            # 前端比后端短就会「后端成功了、界面报超时」，然后真实结果被丢弃。
            "timeouts": {
                "token": channels.TOKEN_TIMEOUT,
                "send": _http_timeout(),
            },
        }

    @_guard
    def channels_state(self) -> list:
        """每个通道的当前状态 + 已填的值。

        `subtitle` / `level` 直接用 `insight.channel_state()` 的结果，**不在前端
        重新判断**——判定逻辑只许有一份，否则两版界面会对同一个配置说两句话。
        """
        self._touch()
        cfg = config.load()
        last = _last_send_times(self._get_facts())
        out = []
        for ctype, m in channels.CHANNEL_META.items():
            ch = config.get_channel(cfg, ctype) or {}
            subtitle, level, lit = insight.channel_state(ctype, ch, last.get(ctype))

            values = {}
            for key, _label, ftype, default, _hint in (m.get("fields") or []):
                values[key] = _field_value(ch.get(key, default), ftype)

            out.append({
                "type": ctype,
                "enabled": bool(ch.get("enabled")),
                "ready": channels.is_ready(ctype, ch),
                "missing": channels.missing_fields(ctype, ch),
                # 只有 clawbot 用得上：凭证从哪来（手动 / 自动探测 / 没有）。
                # 别的通道凭证就在 `values` 里，这里给空串，前端按空处理。
                "credential": (
                    channels.clawbot_source_label(ch) if ctype == "clawbot" else ""
                ),
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
