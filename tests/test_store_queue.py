"""重试队列：写不进去时必须留痕。

W11 终审发现 `store.enqueue()` 是 `except OSError: pass`——磁盘满、目录权限
被改、杀软锁文件，任何一种都让「发送失败待重试」的消息**无声蒸发**，日志里
一个字都没有。用户只会觉得「这条没发出去，也没重试」，然后开始怀疑程序。

第 2 条是**防修过头**：不许为了记日志把正常写入改坏。

W14 补上另一半：W12 只修了「写入」失败，`dispatch.py` 里**队列整体重试**
失败仍是 `except Exception: pass`。写进去了、重试时炸了，消息照样消失。
"""

from __future__ import annotations

from wbn import store


class _BoomPath:
    """一个 `open()` 必炸的假 QUEUE_PATH。"""

    def open(self, *a, **k):
        raise OSError("模拟磁盘满")


def test_enqueue_failure_is_logged_not_silent(isolated_state, monkeypatch):
    monkeypatch.setattr(store, "QUEUE_PATH", _BoomPath())

    store.enqueue({"title": "写不进去的一条"})          # 不许抛

    lp = store.log_path()
    assert lp.exists(), "队列写失败后连日志文件都没生成——说明一个字都没记"
    text = lp.read_text(encoding="utf-8")
    assert "重试队列写入失败" in text, f"队列写失败没留痕，日志里只有：{text!r}"
    assert "OSError" in text, "得带上异常类型，不然分不清是磁盘满还是权限"


def test_enqueue_success_writes_one_line(isolated_state):
    """正常路径不许被改坏：还是写一行，还能读回来。"""
    store.enqueue({"title": "能写进去的一条"})

    assert store.QUEUE_PATH.exists()
    lines = [ln for ln in store.QUEUE_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1, f"应当只写一行，实际 {len(lines)} 行"

    queue = store.load_queue()
    assert len(queue) == 1
    assert queue[0]["title"] == "能写进去的一条"


class _FixedTime:
    """让 `int(time.time()) % 10 == 0` 恒成立，否则进不去重试分支。

    打桩 `dispatch` 模块自己的 `time` 引用，不碰全局 `time.time()`——
    后者会影响同进程里所有拿时间戳的东西，测试之间互相污染。
    """

    @staticmethod
    def time() -> float:
        return 10.0


def test_retry_queue_failure_is_logged(isolated_state, monkeypatch):
    """队列整体重试抛异常时必须留痕，而且不许把异常漏给调用方。

    背景：`dispatch.py` 是 hook 拉起的后台进程，异常漏出去等于这条消息
    连「失败」都没记上。W12 修了写入侧，这里修的是重试侧。
    """
    import dispatch
    from wbn import pipeline

    monkeypatch.setattr(dispatch, "time", _FixedTime)

    def _boom() -> None:
        raise RuntimeError("模拟重试炸了")

    monkeypatch.setattr(pipeline, "retry_queue", _boom)

    dispatch.main([])                       # 不许抛

    lp = store.log_path()
    assert lp.exists(), "重试失败后连日志文件都没生成——说明一个字都没记"
    text = lp.read_text(encoding="utf-8")
    assert "重试队列处理失败" in text, f"重试失败没留痕，日志里只有：{text!r}"
    assert "RuntimeError" in text, "得带上异常类型，不然分不清是网络还是代码"
    assert "模拟重试炸了" in text, "得带上异常原文，不然只知道炸了不知道为什么"
