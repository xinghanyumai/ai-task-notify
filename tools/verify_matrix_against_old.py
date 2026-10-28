"""矩阵完整性检验：七个历史 bug 在旧代码上必须**全红**。

这是「30 格矩阵够不够」的唯一客观检验。矩阵自己全绿说明不了什么——
**刚写出来的矩阵对当前代码当然是绿的**。真正的证据是：把时钟拨回去，
同一批数据、同一张期望值表，旧代码必须撞在某一格上。

    某格在旧代码上不红 = 那一格没覆盖到这个 bug，矩阵要补。

反方向也要查：某格在旧代码上红了、新代码也红，说明期望值写错了（不是代码错）。

用法：

    python tools/verify_matrix_against_old.py

退出码 0 = 7 行全「旧红 / 新绿」。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import inspect  # noqa: E402

import test_logs_matrix as M  # noqa: E402

from wbn import bridge, config, insight  # noqa: E402

# bug 名 / 旧版本（bug 仍在的那次提交）/ 格 / 该格哪条断言必须失败
CASES = [
    ("W3.1 测试发送进统计",     "42b279c",    "T1", "Y0", True,
     "ok + fail == 0"),
    ("W3.2 today 依赖 log 通道", "13bc612",    "T2", "Y0", False,
     "today == 1"),
    ("W3.3 log 冒充投递",       "6b9a83e~1",  "T2", "Y0", True,
     "ok == 1"),
    ("W3.4 界面替失败背书",     "dd7f5f4~1",  "T3", "Y0", False,
     "chain == danger"),
    ("W3.5 recent 依赖 log 通道", "bd6ee91~1", "T2", "Y0", False,
     "len(recent) == 1"),
    ("W3.6 跨天累加 / 零点失忆", "36ec674~1",  "T0", "Y1", False,
     "fail == 0 / chain == danger"),
    ("W3.7 测试遮蔽真实投递",   "f2cd0bf~1",  "T1", "Y1", False,
     "chain == danger"),
]


def _load_old(commit: str, tmp: Path):
    """把某个提交的 `insight.py` / `bridge.py` 加载成独立模块。

    模块名必须带 `wbn.` 前缀：这两个文件里写的是 `from . import config`，
    挂在别的名字下会报「attempted relative import with no known parent package」。
    """
    tag = commit.replace("~", "_").replace("-", "_")
    for name in ("insight", "bridge"):
        src = subprocess.check_output(
            ["git", "show", f"{commit}:wbn/{name}.py"], cwd=ROOT)
        (tmp / f"{name}.py").write_bytes(src)

    out = []
    for name in ("insight", "bridge"):
        mod_name = f"wbn._old_{tag}_{name}"
        spec = importlib.util.spec_from_file_location(mod_name, tmp / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        out.append(mod)
    return out


def _chain(ins, cfg, last):
    """旧版 `chain_sentence` 可能不接受 `last`（W3.4 之前）。"""
    if "last" in inspect.signature(ins.chain_sentence).parameters:
        return ins.chain_sentence(cfg, last)
    return ins.chain_sentence(cfg)


def _state(ins, ctype, ch, rec):
    """`rec` 是**单条通道**的记录（`last.get(ctype)`），不是整张 `{ctype: …}` 表。

    传整张表进去，`last.get("ok")` 永远是 `None`，`channel_state` 会一律
    返回「已启用」——这个工具自己就先瞎了一半。
    """
    if "last" in inspect.signature(ins.channel_state).parameters:
        return ins.channel_state(ctype, ch, rec)
    return ins.channel_state(ctype, ch)


def _probe(ins, bri, cfg, focus):
    """四个出口 → 和期望值表同样的六列。"""
    s = ins.stats(cfg)
    last = bri._last_send_times()
    chain = _chain(ins, cfg, last)
    ch = next(c for c in cfg["channels"] if c["type"] == focus)
    state = _state(ins, focus, ch, last.get(focus))
    return (s.get("today"), s.get("ok"), s.get("fail"),
            len(s.get("recent") or ()), chain[1], state[1])


COLS = ("today", "ok", "fail", "recent", "chain", "state")


def _want(t: str, y: str) -> tuple:
    e = M.EXPECTED[(t, y)]
    return (e["today"], e["ok"], e["fail"], e["recent"], e["chain"], e["state"])


def main() -> int:
    rows = []
    for name, commit, t, y, log_on, guard in CASES:
        tmp = Path(tempfile.mkdtemp(prefix="w38b_"))
        logdir = tmp / "logs"
        logdir.mkdir()
        M._write(tmp, 1, M._yday(y, log_on))
        M._write(tmp, 0, M._today(t, log_on))
        if log_on:
            M._write_notifications(tmp, 1, y)
            M._write_notifications(tmp, 0, t)

        old_dir = tmp / "old"
        old_dir.mkdir()
        old_ins, old_bri = _load_old(commit, old_dir)

        # 旧模块和 `config.load()` 都读同一组全局常量——两个都要指进 tmp。
        # 只改 LOG_DIR 不改 CONFIG_PATH，`_cfg()` 会读到真机 config.json，
        # 那里 wxmp / webhook / log 都开着，和矩阵「只启用一条远端通道」的
        # 假设冲突，新代码也会跟着红。
        saved = (config.LOG_DIR, config.CONFIG_PATH)
        config.LOG_DIR = logdir
        config.CONFIG_PATH = tmp / "config.json"      # 不存在 → 用默认配置
        try:
            cfg = M._cfg(M.FOCUS[(t, y)], log_on)
            want = _want(t, y)
            got_old = _probe(old_ins, old_bri, cfg, M.FOCUS[(t, y)])
            got_new = _probe(insight, bridge, cfg, M.FOCUS[(t, y)])
        finally:
            config.LOG_DIR, config.CONFIG_PATH = saved

        diff = [f"{c}:{o}≠{w}"
                for c, o, w in zip(COLS, got_old, want, strict=True) if o != w]

        rows.append({
            "bug": name, "commit": commit, "cell": f"{t}×{y}×{'开' if log_on else '关'}",
            "guard": guard, "old": got_old, "new": got_new, "want": want,
            "old_red": bool(diff), "new_green": got_new == want, "diff": diff,
        })

    w = max(len(r["bug"]) for r in rows)
    print(f"{'历史 bug'.ljust(w)} | 旧版本 | 格 | 旧红？ | 新绿？ | 旧代码差在哪")
    print("-" * (w + 60))
    ok = True
    for r in rows:
        print(f"{r['bug'].ljust(w)} | {r['commit']:>10} | {r['cell']} | "
              f"{'红' if r['old_red'] else '**绿**'} | "
              f"{'绿' if r['new_green'] else '**红**'} | "
              f"{', '.join(r['diff']) or '—'}")
        ok = ok and r["old_red"] and r["new_green"]

    print()
    print(f"旧红 {sum(r['old_red'] for r in rows)}/7   "
          f"新绿 {sum(r['new_green'] for r in rows)}/7")
    if not ok:
        print("✗ 有格子没起到作用：旧代码不红 = 矩阵没覆盖到；新代码不绿 = 期望值写错。")
        return 1
    print("✓ 七个历史 bug 全部能被矩阵复现，当前代码全绿。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
