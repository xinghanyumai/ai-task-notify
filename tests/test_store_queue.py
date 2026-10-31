"""重试队列：写不进去时必须留痕。

W11 终审发现 `store.enqueue()` 是 `except OSError: pass`——磁盘满、目录权限
被改、杀软锁文件，任何一种都让「发送失败待重试」的消息**无声蒸发**，日志里
一个字都没有。用户只会觉得「这条没发出去，也没重试」，然后开始怀疑程序。

第 2 条是**防修过头**：不许为了记日志把正常写入改坏。
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
