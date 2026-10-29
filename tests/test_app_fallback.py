"""降级提示框：每种失败原因各说各话，而且都不许说假话。

**为什么要有这个文件**（R47）：原来 `app.py` 在开窗前查一次 WebView2 注册表，
探不到就说「这台机器缺少 Microsoft WebView2 运行时」并直接返回。2026-10-29
真实事故——用 regedit 改键名时粘贴带进一个换行，键名变成 `{F3017226-…E4C5}\\n`，
键看着还在、版本号也在，探测却找不到，程序于是对用户说了一句假话。

根因不是探测写得不够全，是**探测失败被当成结论**。所以这里守三件事：

1. 四种真实信号（没装包 / 引擎不对 / 建不出窗口 / 建了画不出）各有各的首句
2. 没有第五句「什么都归到缺少运行时」的默认值——`reason` 必须显式传
3. **每个调用点传的 reason 是对的**（`test_<情形>_reports_<原因>` 那几条）——
   文案写得再准，传错信号等于没做。R47 的病根就是信号传错，前面两条钉不住它。
"""

from __future__ import annotations

import ast
import inspect
import sys
import time
import types

import pytest

import app


def _first_line(reason: str) -> str:
    return app.fallback_text(reason)[0]


def test_four_failure_reasons_say_four_different_things():
    """四种原因的首句必须互不相同——同一句话就又是 R47 那种假话。

    W6.1 时这条是三种（`no_package` / `no_runtime` / `no_render`）；
    R48 加了 `no_engine`（读 `wf.is_chromium`，消掉 10 秒白屏），跟着扩成四种。
    """
    reasons = ("no_package", "no_engine", "no_runtime", "no_render")
    lines = [_first_line(r) for r in reasons]

    assert len(set(lines)) == len(reasons), (
        f"首句有重复：{dict(zip(reasons, lines, strict=True))}")

    # 逐句核对「它说的是不是真的」
    assert "pywebview" in lines[0], "没装包那句得说清缺的是 pywebview"
    assert "WebView2" not in lines[0], "没装包时不许甩锅给 WebView2"

    # no_engine：引擎不可用 ≠ 运行时缺少。键名坏掉时运行时明明装着，
    # 这一句说「缺少」就又是 R47 那句假话。
    assert "定位不到" in lines[1] or "不可用" in lines[1], (
        "引擎不可用那句得说「定位不到 / 不可用」")
    assert "缺少" not in lines[1], "引擎不可用时不许说「缺少」——它可能只是坏了"

    assert "WebView2" in lines[2], "建不出窗口那句得点名 WebView2"
    assert "缺少" not in lines[3], "窗口开了没画出来，不是「缺少」任何东西"


def test_fallback_reason_has_no_default():
    """`reason` 不许有默认值。

    有默认值就一定会有人不传，三种失败又退回同一句话——那正是 R47 的病。
    这条断言的是**形状**，不是某个文案。
    """
    params = inspect.signature(app.fallback_no_webview).parameters
    assert "reason" in params, "签名里必须有个叫 reason 的参数"
    assert params["reason"].default is inspect.Parameter.empty, (
        "reason 不许有默认值：默认值会把「没说清是哪种失败」变成合法调用")


def test_unknown_reason_admits_it_does_not_know():
    """原因串写错时，宁可说「不知道」，也不许猜一个原因糊上去。"""
    headline, hint = app.fallback_text("typo-not-a-real-reason")

    assert headline == app.UNKNOWN_REASON[0]
    assert "原因未知" in headline
    for claim in ("pywebview", "WebView2"):
        assert claim not in headline, f"未知原因时不许声称是 {claim} 的问题"
    assert hint == app.UNKNOWN_REASON[1]


def test_fallback_does_not_import_tkinter():
    """W7 打单文件 exe，带上 tkinter 会多 5MB，还会拖慢启动。

    降级路径是唯一一条「一定会跑到」的兜底，它把 tkinter 拉进来等于白省。
    判据是**没有 import 语句**——注释里提到 tkinter 是在解释为什么不许用。
    """
    src = inspect.getsource(app)
    imports = [ln.strip() for ln in src.splitlines() if ln.strip().startswith(
        ("import ", "from "))]
    assert not [ln for ln in imports if "tkinter" in ln], (
        "降级路径 import 了 tkinter")


def test_app_does_not_query_the_registry():
    """预检已经删了，不许以「更宽松的预检」的形式回来。

    **判据是「app.py 不查注册表」，不是「函数名不许叫某个名字」。**
    原来那个探测就是查 EdgeUpdate 的键，探不到就当运行时不存在——查了就说明
    又在开窗前对运行时下结论，换个名字、多查一个 HKCU 都救不了这个逻辑。

    用 `ast` 而不是字符串搜索：注释里解释这段历史时必须能提到键名，
    否则下一个人看不出为什么不能加回来。
    """
    tree = ast.parse(inspect.getsource(app))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "winreg" not in imported, (
        "app.py 又开始查注册表了。问题不是探测写得不够全，"
        "是探测失败被当成结论")

    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not [s for s in literals if "EdgeUpdate" in s], (
        "EdgeUpdate 的键路径又回来了")


@pytest.mark.parametrize("reason", sorted(app.FALLBACK_REASONS))
def test_message_starts_with_its_own_headline(reason):
    """正文第一行必须就是那句原因——中间插任何一句都会把它挤成第二行。"""
    lines = app.fallback_message(reason).splitlines()
    assert lines[0] == app.fallback_text(reason)[0]
    assert lines[0] != "", "首句不许为空，空行等于没告诉用户为什么"


@pytest.mark.parametrize("reason", sorted(app.FALLBACK_REASONS))
def test_three_buttons_are_still_offered(reason):
    """三个按钮的去向不许因为改文案而丢——那是用户唯一的自救路径。"""
    text = app.fallback_message(reason)
    assert "配置目录" in text
    assert "日志目录" in text
    assert "通知功能不受影响" in text


# ------------------------------------------------------------ 调用点 → reason
#
# 上面几条只钉住「文案对不对」。**传错信号**不会有任何一条变红——把 watchdog
# 的 `no_render` 写成 `no_runtime`，11 条全绿。而 R47 的病根正是信号传错。
#
# 这一组跑**真的** `run_ui()`：只把 `webview` 换成假模块、把
# `fallback_no_webview` 换成记录器，然后看它到底传了哪个 reason。
# `run_ui()` 的结构一点没动——全靠 monkeypatch。


class _FakeEvent:
    """`win.events.closing += _on_closing` 用得上。"""

    def __iadd__(self, other):
        return self

    def __isub__(self, other):
        return self


class _FakeWindow:
    def __init__(self):
        self.events = types.SimpleNamespace(closing=_FakeEvent())

    def destroy(self):
        return None

    def create_confirmation_dialog(self, *a, **k):
        return True


class _FakeWebview(types.ModuleType):
    """只实现 `run_ui()` 真正用到的那几个名字。"""

    def __init__(self, *, create="ok", start="ok", hold=0.0):
        super().__init__("webview")
        self.windows = []
        self.calls = []
        self._create = create
        self._start = start
        self._hold = hold

    def create_window(self, *a, **k):
        self.calls.append("create_window")
        if self._create == "raise":
            raise RuntimeError("假的：WebView2 建不出窗口")
        if self._create == "none":
            return None
        win = _FakeWindow()
        self.windows.append(win)
        return win

    def start(self, **k):
        self.calls.append("start")
        if self._start == "raise":
            raise RuntimeError("假的：WebView2 初始化失败")
        if self._hold:
            time.sleep(self._hold)     # 给 watchdog 线程留出触发时间


def _fake_winforms(*, is_chromium=True, has_flag=True):
    """假的 `webview.platforms.winforms`。`has_flag=False` 模拟版本变化。"""
    m = types.ModuleType("webview.platforms.winforms")
    m.renderer = "edgechromium" if is_chromium else "mshtml"
    if has_flag:
        m.is_chromium = is_chromium
    return m


def _run_ui(monkeypatch, *, webview, winforms=None, timeout=None):
    """跑一次 `run_ui()`，返回它传给 `fallback_no_webview` 的 reason 列表。

    `webview=BLOCKED` 表示「import 就失败」，用来造没装包。
    `timeout` 只在 no_render 那条用——把 `UI_ALIVE_TIMEOUT` 临时调小，
    否则每条要等 10 秒。**源文件里的常量不动。**
    """
    recorded: list[str] = []
    monkeypatch.setattr(app, "fallback_no_webview", recorded.append)
    monkeypatch.setenv("WBN_BENCH", "1")      # 跳过单实例锁，别去抢真窗口

    if webview is BLOCKED:
        monkeypatch.setitem(sys.modules, "webview", None)
    else:
        pkg = types.ModuleType("webview.platforms")
        pkg.__path__ = []
        if winforms is not None:
            pkg.winforms = winforms
            monkeypatch.setitem(sys.modules, "webview.platforms.winforms", winforms)
        webview.platforms = pkg
        monkeypatch.setitem(sys.modules, "webview.platforms", pkg)
        monkeypatch.setitem(sys.modules, "webview", webview)

    if timeout is not None:
        monkeypatch.setattr(app, "UI_ALIVE_TIMEOUT", timeout)

    assert app.run_ui() == 0
    return recorded


BLOCKED = object()


def test_missing_package_reports_no_package(monkeypatch):
    """`import webview` 失败 → 说的是「没装 pywebview」，不是「缺少运行时」。"""
    assert _run_ui(monkeypatch, webview=BLOCKED) == ["no_package"]


def test_engine_unavailable_reports_no_engine(monkeypatch):
    """pywebview 自己判定引擎不是 Chromium → `no_engine`，而且**不建窗口**。

    不建窗口是关键：建了再等 10 秒探针，就是用户抱怨的那个白屏。
    """
    fake = _FakeWebview()
    recorded = _run_ui(monkeypatch, webview=fake,
                       winforms=_fake_winforms(is_chromium=False))
    assert recorded == ["no_engine"]
    assert fake.calls == [], "引擎不对时不该去建窗口——那正是 10 秒白屏的来源"


def test_engine_check_skipped_when_flag_missing(monkeypatch):
    """pywebview 版本变了、没有 `is_chromium` → 跳过检查照常开窗，不许崩。"""
    fake = _FakeWebview()
    recorded = _run_ui(monkeypatch, webview=fake,
                       winforms=_fake_winforms(has_flag=False))
    assert recorded == [], "读不到标志时应当照常开窗"
    assert fake.calls == ["create_window", "start"]


@pytest.mark.parametrize("mode", ["raise", "none"])
def test_window_creation_failure_reports_no_runtime(monkeypatch, mode):
    """`create_window` 抛异常或返回 None → 同一个原因：运行时不可用。"""
    fake = _FakeWebview(create=mode)
    recorded = _run_ui(monkeypatch, webview=fake, winforms=_fake_winforms())
    assert recorded == ["no_runtime"]


def test_start_failure_reports_no_runtime(monkeypatch):
    """`webview.start()` 抛异常 → 与建窗口失败同类，仍是运行时不可用。"""
    fake = _FakeWebview(start="raise")
    recorded = _run_ui(monkeypatch, webview=fake, winforms=_fake_winforms())
    assert recorded == ["no_runtime"]


def test_watchdog_timeout_reports_no_render(monkeypatch):
    """窗口建出来了但前端没调通 bridge → `no_render`，不许报成别的。

    `UI_ALIVE_TIMEOUT` 临时调到 0.05s 只是为了不真等 10 秒；**源文件里的常量
    没动**，`test_platform.py` 之外也没有别处改它。
    """
    fake = _FakeWebview(hold=0.4)
    recorded = _run_ui(monkeypatch, webview=fake, winforms=_fake_winforms(),
                       timeout=0.05)
    assert recorded == ["no_render"]
    assert fake.calls == ["create_window", "start"], "这条必须真走到开窗之后"
