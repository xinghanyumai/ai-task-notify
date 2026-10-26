"""hook 入口 —— 由 WorkBuddy / CodeBuddy 在生命周期事件上调用。

铁律：
1. 绝不阻塞 agent。这里只做「读 stdin → 通过管道交给异步进程」，然后立刻退出。
   实测耗时 < 60ms，用户完全无感。
2. 绝不写 stdout。SessionStart / UserPromptSubmit 的 stdout 会被注入模型上下文，
   任何多余输出都会污染对话。
3. 绝不非零退出。fail-open：本程序出任何问题，都不该影响宿主正常工作。

为什么不用临时文件中转：
本机沙箱环境下删除一个文件约 700ms（删除会被审计拦截），
而临时文件方案必然要「创建 + 删除」，等于每条通知白等 1.4 秒。
改用 stdin 管道投递，全程不落盘。

用法（由 install.py 写进 settings.json，无需手工调用）：
    pythonw.exe hook.py --app workbuddy
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wbn import config, store  # noqa: E402
from wbn.sanitize import clean_obj  # noqa: E402

DISPATCH = Path(__file__).resolve().parent / "dispatch.py"

# 这些事件交给异步进程判定（判定需要读 transcript、算耗时，不适合在 hook 里做）
DEFERRED_EVENTS = {
    "Stop",
    "StopFailure",
    "SubagentStopFailure",
    "SessionEnd",
    "SessionStart",
    "PermissionRequest",
    "Notification",
    "TaskCompleted",
    "Elicitation",
}

# 这些事件只更新本地状态，从不通知
STATE_ONLY_EVENTS = {"UserPromptSubmit", "PostToolUseFailure"}

# 单条 payload 上限，超出就截断长文本字段（管道缓冲区 64KB 的安全余量）
MAX_PAYLOAD_BYTES = 48 * 1024


def read_stdin(timeout: float = 3.0) -> dict:
    """读 hook 传入的 JSON。拿不到就返回空 dict，绝不抛异常、绝不阻塞。

    放在守护线程里读，是为了兜底「宿主没投递 stdin」的极端情况：
    直接 sys.stdin.read() 会一直等 EOF，把 agent 卡死。

    两个关键点（踩过坑，别改回去）：
    1. 必须读 sys.stdin.buffer 的**原始字节**再自己按 UTF-8 解。
       走 sys.stdin.read() 等于把解码权交给宿主的 locale / 环境变量，
       宿主那边一旦不是 UTF-8，中文就会变成乱码或落单代理字符。
    2. 解完必须 clean_obj 洗一遍。宿主可能直接写出 "\\udca2" 这种
       JSON 转义，解出来是孤立的代理码位，后续 encode("utf-8") 必崩。
    """
    import threading

    box: dict[str, bytes] = {}

    def _worker() -> None:
        try:
            stream = getattr(sys.stdin, "buffer", None)
            if stream is not None and not stream.closed:
                box["raw"] = stream.read()
            elif sys.stdin and not sys.stdin.closed:
                box["raw"] = sys.stdin.read().encode("utf-8", "replace")
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)

    raw = box.get("raw", b"")
    if not raw or not raw.strip():
        return {}
    try:
        obj = json.loads(raw.decode("utf-8", "replace").lstrip("\ufeff"))
        return clean_obj(obj) if isinstance(obj, dict) else {}
    except Exception:
        return {}


def pythonw() -> str:
    """优先用 pythonw.exe，避免每次 hook 闪一个黑框。"""
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand) if cand.exists() else str(exe)


def _encode(payload: dict, app: str) -> bytes:
    """把事件打成 UTF-8 字节。

    这里的 errors="replace" 是最后一道保险：上游 read_stdin 已经洗过代理字符，
    但 payload 里可能还有别处塞进来的脏字符串，宁可把一个字符换成 "?"
    也不能让整条通知因为编码异常而凭空消失。
    """
    text = json.dumps({"app": app, "ts": time.time(), "payload": payload},
                      ensure_ascii=False)
    return text.encode("utf-8", "replace")


def spawn_dispatch(payload: dict, app: str) -> None:
    """拉起完全脱离的派发进程，事件通过 stdin 管道投递（不落盘）。"""
    data = _encode(payload, app)
    if len(data) > MAX_PAYLOAD_BYTES:
        # 超长就砍掉最占空间的字段，通知本身不受影响
        for key in ("last_assistant_message", "tool_response", "transcript_content"):
            if key in payload and isinstance(payload[key], str):
                payload[key] = payload[key][:2000]
        data = _encode(payload, app)

    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    proc = subprocess.Popen(
        [pythonw(), str(DISPATCH), "--stdin"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
        cwd=str(Path(__file__).resolve().parent),
    )
    try:
        proc.stdin.write(data)
        proc.stdin.close()
    except Exception:
        # 子进程没起来也不影响宿主
        pass


def handle_state_only(ev: str, payload: dict, cfg: dict) -> None:
    """不通知、只维护状态的事件。"""
    sid = str(payload.get("session_id") or "")

    if ev == "UserPromptSubmit":
        prompt = str(payload.get("prompt") or "")
        if sid:
            store.mark_turn_start(sid, prompt)
        return

    if ev == "PostToolUseFailure":
        if not sid:
            return
        streak = store.bump_tool_failure(sid)
        threshold = int(cfg.get("filters", {}).get("tool_fail_threshold") or 3)
        # 连续失败到阈值才通知，避免单次重试就打扰
        if threshold > 0 and streak >= threshold:
            spawn_dispatch(payload, str(payload.get("_app") or "WorkBuddy"))
            store.reset_tool_failure(sid)
        return


def main() -> int:
    started = time.time()
    app = "WorkBuddy"
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--app" and i + 1 < len(argv):
            app = argv[i + 1]

    payload = read_stdin()
    if not payload:
        return 0

    ev = str(payload.get("hook_event_name") or payload.get("event") or "")
    if not ev:
        return 0

    cfg = config.load()
    if not cfg.get("enabled", True):
        return 0

    if ev in STATE_ONLY_EVENTS:
        payload["_app"] = app
        handle_state_only(ev, payload, cfg)
        return 0

    if ev not in DEFERRED_EVENTS:
        return 0

    spawn_dispatch(payload, app)

    # 预算守卫：正常情况远低于阈值，这里只做兜底记录
    budget = float(cfg.get("advanced", {}).get("hook_budget_ms") or 1500) / 1000.0
    if time.time() - started > budget:
        store.log(f"hook 超预算 {time.time()-started:.2f}s event={ev}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        # 任何异常都静默吞掉，保证宿主不受影响
        try:
            store.log(f"hook 未捕获异常: {__import__('traceback').format_exc()}")
        except Exception:
            pass
        sys.exit(0)
