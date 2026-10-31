"""hooks 幂等：换成 exe 之后，源码版留下的旧条目必须被认出来并替换掉。

W14 §2.3 实测踩到的坑：先在仓库里跑过源码版 `install`（宿主里写的是
`<仓库>/hook.py`），再跑 `通知.exe install` 换成 exe。结果**每个事件挂两条**——
旧的那条清不掉，新的 exe 那条又追加进去。宿主重启后同一个事件跑两遍，
用户收到两条一模一样的通知。

根因在 `is_our_hook()`：它只拿 `HOOK_PATH` 去比，而打包后 `ROOT` 是 PyInstaller
的 `_MEIPASS` 临时解压目录，`HOOK_PATH` 变成 `%TEMP%\\_MEIxxxx\\hook.py`，
跟用户机器上真正装过的那个 `hook.py` 完全不是一条路径。

这三条守的是同一件事的三面：认得出旧的、认得出新的、别人的不许误删。
"""

from __future__ import annotations

import sys

import install


def _cmds(merged: dict, ev: str = "Stop") -> list[str]:
    return [h["command"] for e in merged.get(ev, []) for h in e.get("hooks", [])]


def test_source_hook_is_replaced_when_running_as_exe(monkeypatch, tmp_path):
    """源码版装过、现在用 exe 跑 install：旧条目要被清掉，只剩一条 exe 的。"""
    exe = tmp_path / "通知.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=True)

    # 源码版写进宿主的那条：指向仓库里的 hook.py，正斜杠 + 解释器双引号。
    old = f'"{tmp_path}/hook.py" --app WorkBuddy'
    existing = {"Stop": [{"hooks": [{"type": "command", "command": old, "timeout": 15}]}]}

    cmds = _cmds(install.merge_hooks(existing, "WorkBuddy"))

    assert not any("hook.py" in c for c in cmds), (
        f"旧的 hook.py 条目没被认出来，宿主里会同时挂两条、通知发两遍：{cmds}"
    )
    assert any("通知.exe" in c for c in cmds), f"新的 exe 条目没写进去：{cmds}"
    assert len(cmds) == 1, f"同一个事件只该有一条我们的 hook，实际 {len(cmds)} 条：{cmds}"


def test_merge_is_idempotent_across_repeated_installs(monkeypatch, tmp_path):
    """连跑两次 install 不许堆条目——这是「幂等」二字的最低要求。"""
    exe = tmp_path / "通知.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=True)

    once = install.merge_hooks({}, "WorkBuddy")
    twice = install.merge_hooks(once, "WorkBuddy")

    assert len(_cmds(twice)) == 1, f"跑两次变成 {len(_cmds(twice))} 条：{_cmds(twice)}"


def test_someone_elses_hook_is_not_touched(monkeypatch, tmp_path):
    """别人的 hook 一条都不许动——`merge_hooks` 的承诺是「只替换自己的」。"""
    exe = tmp_path / "通知.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=True)

    foreign = {"hooks": [{"type": "command", "command": "node /opt/other/tool.js", "timeout": 5}]}
    existing = {"Stop": [foreign]}

    cmds = _cmds(install.merge_hooks(existing, "WorkBuddy"))

    assert "node /opt/other/tool.js" in cmds, f"把别人的 hook 删了：{cmds}"
    assert len(cmds) == 2, f"应当「别人的 + 我们的一条」，实际 {len(cmds)} 条：{cmds}"


def test_uninstall_still_recognizes_the_source_hook(monkeypatch, tmp_path):
    """`uninstall` 走同一个判据，也要能认出源码版那条——否则永远清不干净。"""
    exe = tmp_path / "通知.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=True)

    old = {"hooks": [{"type": "command", "command": f'"{tmp_path}/hook.py" --app WorkBuddy'}]}
    stripped = install.strip_hooks({"Stop": [old]})

    assert "Stop" not in stripped or not stripped["Stop"], (
        f"uninstall 没认出源码版那条，残留在：{stripped}"
    )
