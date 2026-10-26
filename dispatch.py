# -*- coding: utf-8 -*-
"""异步派发进程 —— 由 hook.py 以完全脱离的方式拉起。

职责：读事件（stdin 管道）-> 跑通知流水线 -> 顺带重试失败队列。

独立进程的好处：
- hook 本体 60ms 内退出，agent 不被网络请求拖住
- 推送失败 / 网络超时只影响这个后台进程
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wbn import config, pipeline, store  # noqa: E402
from wbn.sanitize import clean_obj  # noqa: E402


def read_event(argv: list[str]) -> dict | None:
    """支持两种输入：--stdin（管道）与文件路径（便于手工调试）。"""
    try:
        if argv and argv[0] == "--stdin":
            # 读原始字节自己解 UTF-8：不能依赖 sys.stdin 的隐式解码，
            # 宿主环境一旦不是 UTF-8，中文会变成落单代理字符并炸掉后续 encode。
            stream = getattr(sys.stdin, "buffer", None)
            if stream is not None:
                raw_bytes = stream.read()
            else:
                raw_bytes = (sys.stdin.read() if sys.stdin else "").encode("utf-8", "replace")
            raw = raw_bytes.decode("utf-8", "replace").lstrip("\ufeff")
            if not raw.strip():
                return None
            obj = json.loads(raw)
            return clean_obj(obj) if isinstance(obj, dict) else None

        if argv:
            path = Path(argv[0])
            if not path.exists():
                return None
            obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            return clean_obj(obj) if isinstance(obj, dict) else None
    except Exception as e:
        store.log(f"事件读取失败: {e}")
        return None
    return None


def main(argv: list[str]) -> int:
    config.ensure_dirs()

    data = read_event(argv)
    if data:
        payload = data.get("payload") or {}
        app = str(data.get("app") or "WorkBuddy")
        try:
            pipeline.process(payload, app)
        except Exception:
            import traceback

            store.log(f"dispatch 异常: {traceback.format_exc()}")

    # 顺带重试失败队列（节流：约每 10 秒一次机会，避免频繁网络请求）
    if int(time.time()) % 10 == 0:
        try:
            pipeline.retry_queue()
        except Exception:
            pass

    cfg = config.load()
    store.prune_logs(int(cfg.get("advanced", {}).get("log_retention_days") or 14))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        try:
            import traceback

            store.log(f"dispatch 顶层异常: {traceback.format_exc()}")
        except Exception:
            pass
        sys.exit(0)
