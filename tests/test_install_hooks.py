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

import json
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


# ---------------------------------------------------------------- W15：onedir


def _onedir_exe(tmp_path):
    """造一个 onedir 的 exe 路径：`<tmp>/WBNotify/通知.exe` + `_internal/`。"""
    d = tmp_path / "WBNotify"
    d.mkdir()
    (d / "_internal").mkdir()
    exe = d / "通知.exe"
    exe.write_bytes(b"stub")
    return exe


def test_onedir_replaces_the_onefile_entry(monkeypatch, tmp_path):
    """onefile 装过（hooks 指着仓库根的 通知.exe），换成 onedir 再 install。

    旧条目必须被认出来并清掉——`sys.executable` 现在是
    `WBNotify/通知.exe`，跟旧的 `<仓库>/通知.exe` 不是同一个字符串。
    认不出来就是每个事件挂两条、用户收到两条通知（W14 §2.3 换个形态重演）。
    """
    exe = _onedir_exe(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=True)

    old = f'"{tmp_path}/通知.exe" hook --app WorkBuddy'
    existing = {"Stop": [{"hooks": [{"type": "command", "command": old, "timeout": 15}]}]}

    cmds = _cmds(install.merge_hooks(existing, "WorkBuddy"))

    assert not any(c.lower().count("通知.exe") == 1 and "wbnotify" not in c.lower()
                   for c in cmds), f"根目录那条旧 exe 条目没清掉：{cmds}"
    assert len(cmds) == 1, f"同一个事件只该有一条我们的 hook，实际 {len(cmds)} 条：{cmds}"
    assert "wbnotify" in cmds[0].lower(), f"新命令没指向 WBNotify\\通知.exe：{cmds[0]}"


def test_onefile_still_replaces_the_onedir_entry(monkeypatch, tmp_path):
    """反向迁移也要认得出：现在跑根目录的 exe，宿主里挂着 WBNotify 那条。"""
    exe = tmp_path / "通知.exe"
    exe.write_bytes(b"stub")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=True)

    old = f'"{tmp_path}/WBNotify/通知.exe" hook --app WorkBuddy'
    existing = {"Stop": [{"hooks": [{"type": "command", "command": old, "timeout": 15}]}]}

    cmds = _cmds(install.merge_hooks(existing, "WorkBuddy"))

    assert len(cmds) == 1, f"反向迁移时旧条目没清掉，实际 {len(cmds)} 条：{cmds}"
    assert "wbnotify" not in cmds[0].lower(), f"没换成根目录那条：{cmds[0]}"


def test_a_path_containing_wbn_is_not_our_hook(monkeypatch, tmp_path):
    """P3：别人的命令里出现 `wbn` 三个字母，不许被我们删掉。

    原来判据里有个 `or "wbn" in cmd` 的兜底，`wbn-tools` 这种路径会被误判成
    我们的 hook 然后被删——那是别人的东西。
    """
    exe = _onedir_exe(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=True)

    foreign = {"hooks": [{"type": "command",
                          "command": "python /opt/wbn-tools/run.py hook.py --app X"}]}
    existing = {"Stop": [foreign]}

    cmds = _cmds(install.merge_hooks(existing, "WorkBuddy"))

    assert any("wbn-tools" in c for c in cmds), f"把别人的 wbn-tools hook 删了：{cmds}"
    assert len(cmds) == 2, f"应当「别人的 + 我们的一条」，实际 {len(cmds)} 条：{cmds}"


def test_verify_hooks_catches_a_double_entry(monkeypatch, tmp_path):
    """§2.4：写完必须自己核——一个事件挂两条我们的命令就是「会发两遍」。"""
    exe = _onedir_exe(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=True)

    settings = tmp_path / "settings.json"
    new = install.hook_command("WorkBuddy")
    old = f'"{tmp_path}/通知.exe" hook --app WorkBuddy'
    settings.write_text(
        json.dumps({"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": old}]},
            {"hooks": [{"type": "command", "command": new}]},
        ]}}),
        encoding="utf-8",
    )

    problems = install.verify_hooks([("WorkBuddy", settings)])

    assert problems, "两条并存却没报错——这正是「一条通知发两遍」的状态"
    assert any("Stop" in p and "2 条" in p for p in problems), problems


def test_verify_hooks_passes_after_a_clean_install(monkeypatch, tmp_path):
    """干净的 9 个事件要全过，否则这个断言本身就成了噪音。"""
    exe = _onedir_exe(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=True)

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": install.build_hooks("WorkBuddy")}),
                        encoding="utf-8")

    assert install.verify_hooks([("WorkBuddy", settings)]) == []
