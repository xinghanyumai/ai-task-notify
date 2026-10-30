"""输出编码：一行中文不许把进程带走（R64）。

**为什么要有这个文件**：2026-10-30 的 CI 红了 6 轮，`Windows 打包` 的冒烟步骤
每次都是「300s 没退出」，本地却 2.4 秒退出 0。真因直到第 7 轮才从 exe 的
stderr 里挖出来：

    Traceback (most recent call last):
      File "app.py", line 577, in <module>
      File "app.py", line 562, in main
      File "encodings\\cp1252.py", line 19, in encode
    UnicodeEncodeError: 'charmap' codec can't encode characters in position 3-6

`通知.exe` 是 `console=False` 编的，但**只要 stdout 被重定向**（CI 冒烟、
`通知.exe --version > out.txt`），Python 就按系统 ANSI 代码页建一个真的
`sys.stdout`。runner 是英文 Windows → cp1252 → `print("AI 任务通知 1.0.0")`
直接抛。异常冒到引导器手里，PyInstaller 弹「Unhandled exception in script」
对话框等人点确定；runner 上没有桌面，于是永远不退出。

本机是 cp936，中文编码得出来，所以**本地永远复现不了**——这才是它藏了 6 轮的
原因。这里守三件事：

1. `errors` 被放宽成 `replace`，编码保持原样（不硬改 utf-8，那会让 cp936 乱码）
2. 编码不了的流上 `--version` 仍然退出 0（这一条就是 CI 的复现）
3. `_main_guarded` 把未捕获异常变成「退出码 1 + 日志」，不再变成对话框
"""

from __future__ import annotations

import io
import sys

import pytest

import app


def _cp1252_stream() -> tuple[io.TextIOWrapper, io.BytesIO]:
    """一个「英文 Windows 重定向后的 stdout」：cp1252、errors=strict。"""
    raw = io.BytesIO()
    return io.TextIOWrapper(raw, encoding="cp1252", errors="strict"), raw


def _read(raw: io.BytesIO, encoding: str = "cp1252") -> str:
    raw.seek(0)
    return raw.read().decode(encoding, "replace")


def test_chinese_print_really_explodes_on_cp1252():
    """先把病因钉死：不改 errors 的话，这一行就是 CI 上那个异常。

    这条不是在测我们自己的代码，是在测「前提还成立」——哪天 Python 默认
    改成不抛了，下面几条用例就没有意义了，得重新想。
    """
    stream, _ = _cp1252_stream()
    with pytest.raises(UnicodeEncodeError) as ei:
        stream.write("AI 任务通知 1.0.0")
    # position 3-6 正是「任务通知」四个字，和 CI 日志里的原文一致
    assert "position 3-6" in str(ei.value)


def test_harden_stdio_relaxes_errors_but_keeps_encoding(monkeypatch):
    """`replace` 只放宽 errors，不许动 encoding。

    动 encoding 的诱惑很大（"统一 utf-8 多干净"），但真控制台是 cp936 时
    把输出改成 utf-8，中文就变成乱码——那是拿一个 bug 换另一个。
    """
    stream, _ = _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", stream)

    app._harden_stdio()

    assert stream.encoding.lower().replace("-", "") == "cp1252"
    assert stream.errors == "replace"


def test_cp936_stream_still_writes_chinese_verbatim(monkeypatch):
    """能编码的流上，一个字符都不许动——中文必须原样出来。"""
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp936", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)

    app._harden_stdio()
    print("AI 任务通知 1.0.0", flush=True)

    assert "AI 任务通知 1.0.0" in _read(raw, "cp936")


def test_version_exits_zero_on_stream_that_cannot_encode_chinese(monkeypatch):
    """CI 那条红的最小复现：cp1252 的 stdout 上 `--version` 必须退出 0。"""
    stream, raw = _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", stream)

    app._harden_stdio()

    assert app.main(["--version"]) == 0
    stream.flush()
    # 中文降级成 ?，但**行还在、退出码还是 0**——这才是「不许带走进程」
    assert _read(raw).startswith("AI ")


def test_none_streams_are_skipped(monkeypatch):
    """双击启动时 PyInstaller 把 stdout/stderr 设成 None，不许因此抛。"""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    app._harden_stdio()   # 不抛即通过


def test_main_guarded_returns_1_instead_of_dialog(monkeypatch, capsys):
    """未捕获异常 → 退出码 1 + traceback 进日志，而不是 PyInstaller 对话框。"""
    logged: list[str] = []

    def boom() -> int:
        raise RuntimeError("模拟 frozen exe 里的未捕获异常")

    monkeypatch.setattr(app, "main", boom)
    monkeypatch.setattr("wbn.store.log", logged.append)

    assert app._main_guarded() == 1
    assert "模拟 frozen exe 里的未捕获异常" in capsys.readouterr().err
    assert logged and "RuntimeError" in logged[0]


def test_main_guarded_lets_system_exit_through(monkeypatch):
    """`SystemExit` 必须原样放行。

    吞掉它的后果是**每个子命令都变成失败**：`main()` 正常返回 0 走的也是
    `sys.exit(0)`，被这里接住再 return 1 的话，`--version` 在 CI 上照样红。
    """
    def quit_three() -> int:
        raise SystemExit(3)

    monkeypatch.setattr(app, "main", quit_three)

    with pytest.raises(SystemExit) as ei:
        app._main_guarded()
    assert ei.value.code == 3
