"""打包后的路径：数据写 exe 旁边，资源读 `_MEIPASS` —— 两者不许混。

## 为什么单列两条

PyInstaller onefile 运行时把代码解压到 `%TEMP%\\_MEIxxxxxx`，**退出即删**。
如果 `ROOT` 还是 `Path(__file__).parent.parent`：

- 用户填的 10 个通道凭证 → 重启全没
- 日志、`state/`、限流计数 → 同样
- **而且不会有任何报错**：写是成功的，只是写进了一个马上被删的临时目录

这个 bug 在开发态**永远不会暴露**（开发态 `__file__` 就在仓库里）。它只在用户
机器上出现，表现是「我明明配好了，第二天又要重配一遍」。所以只能靠这两条测试
在 CI 上守住（R2）。

第二条（只读回落）同理：exe 放在 `C:\\Program Files\\` 下，普通用户没有写权限，
`config.save()` 会抛 `PermissionError`。必须回落到 `%LOCALAPPDATA%`。
"""

from __future__ import annotations

import sys
from pathlib import Path

from wbn import config


def _frozen(monkeypatch, exe: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=False)


def test_root_uses_exe_dir_when_frozen(monkeypatch, tmp_path):
    """frozen 时 ROOT 必须是 **exe 目录**，不是 `_MEIPASS`。"""
    exe_dir = tmp_path / "便携版"
    exe_dir.mkdir()
    _frozen(monkeypatch, exe_dir / "通知.exe")

    assert config._resolve_root() == exe_dir.resolve()


def test_root_is_never_meipass_even_when_it_is_set(monkeypatch, tmp_path):
    """这条是 R2 本体：`_MEIPASS` 存在时，数据目录也**不许**落在它里面。

    真实场景里 `_MEIPASS` 是 `%TEMP%\\_MEIxxxxxx`（退出即删）。这里用 tmp 目录
    模拟，断言两件事：ROOT 不是它，而且 ROOT 不在它的子树里。
    """
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    meipass = tmp_path / "_MEI123456"
    meipass.mkdir()
    _frozen(monkeypatch, exe_dir / "通知.exe")
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)

    root = config._resolve_root()
    assert root == exe_dir.resolve()
    assert root != meipass.resolve()
    assert meipass.resolve() not in root.parents, (
        f"数据目录落进了解压目录：{root} —— 退出即删，用户凭证会全丢")

    # 资源那一侧反过来：必须指向 _MEIPASS
    assert config._resolve_bundle() == meipass
    assert config.resource("webui", "index.html") == meipass / "webui" / "index.html"


def test_root_falls_back_when_exe_dir_is_not_writable(monkeypatch, tmp_path):
    """exe 目录写不进去时回落 `%LOCALAPPDATA%\\WBNotify`。

    用「父路径是个文件」来制造 OSError（`NotADirectoryError`）——比改 ACL 稳，
    也不会因为 CI 上以管理员身份跑而失效。判据是**同一段 except 分支**被走到。
    """
    blocker = tmp_path / "这不是目录"
    blocker.write_text("x", encoding="utf-8")
    exe_dir = blocker / "sub"          # 父路径是文件 → 任何 touch 都会 OSError

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "通知.exe"), raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    assert config._resolve_root() == tmp_path / "localappdata" / "WBNotify"


def test_source_tree_paths_are_unchanged():
    """没打包时一切照旧：ROOT = 仓库根，资源也从仓库根读。

    这条防的是「为了打包把开发态改坏」——`webui/` 找不到就是本地白屏。
    （`CONFIG_PATH` / `LOG_DIR` 这些被 `conftest.isolated_state` 重定向到 tmp_path
    了，那是所有测试共用的铁律，这里不碰，只验根目录。）
    """
    assert not getattr(sys, "frozen", False)
    repo = Path(__file__).resolve().parent.parent
    assert config.ROOT == repo
    assert config.BUNDLE == repo
    assert config.resource("webui", "index.html") == repo / "webui" / "index.html"
    assert config.resource("webui", "index.html").exists()
    assert config.CONFIG_PATH.name == "config.json"


# ---------------------------------------------------------------- 安装器
#
# 下面三条守的是「打包后**装进宿主里的那条命令**指向 exe，不是 hook.py」。
# 漏了的症状：exe 里根本没有 `hook.py` 这个文件，宿主每次事件都去起一个
# 不存在的脚本 → 静默失败 → 一条通知都收不到，而且**装的时候看不出来**。


def _frozen_exe(monkeypatch, tmp_path):
    exe = tmp_path / "通知.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=False)
    return exe


def test_hook_command_points_at_exe_when_frozen(monkeypatch, tmp_path):
    """`通知.exe hook --app WorkBuddy`，且不许出现 `hook.py`。

    引号不能省——exe 自己也可能被放在含空格的目录里（本项目就是）。
    """
    import install

    exe = _frozen_exe(monkeypatch, tmp_path)
    cmd = install.hook_command("WorkBuddy")

    assert cmd == f'"{exe}" hook --app WorkBuddy'
    assert "hook.py" not in cmd
    assert cmd.startswith('"')


def test_is_our_hook_recognizes_exe_command(monkeypatch, tmp_path):
    """卸载时必须认得出自己写的条目。

    认不出的后果是 `通知.exe uninstall` 之后宿主里留一堆指向已删 exe 的
    hook，每次事件都白等超时——用户体感是「装了别的东西之后 agent 变慢了」。
    """
    import install

    exe = _frozen_exe(monkeypatch, tmp_path)
    entry = {"hooks": [{"type": "command", "command": f'"{exe}" hook --app WorkBuddy'}]}
    assert install.is_our_hook(entry) is True

    other = {"hooks": [{"type": "command", "command": '"C:/other/tool.exe" run'}]}
    assert install.is_our_hook(other) is False


def test_shortcut_targets_exe_and_uses_tray_subcommand(monkeypatch, tmp_path):
    """桌面图标指 exe 且不带参数；开机自启带 `tray --silent`。

    **`tray --silent` 这半句是打包后新加的**：源码形态自启的是 `tray.py`，
    参数只有 `--silent`；exe 里没有 `tray.py`，必须靠子命令。
    漏了的症状是「开机自启的图标点了没反应」——因为它等于双击开设置窗口。
    """
    import install

    exe = _frozen_exe(monkeypatch, tmp_path)
    calls: list[tuple] = []
    monkeypatch.setattr(install, "make_shortcut", lambda *a, **kw: calls.append(a) or True)
    monkeypatch.setattr(install, "_desktop_dir", lambda: tmp_path / "desktop")
    monkeypatch.setattr(install, "_startup_dir", lambda: tmp_path / "startup")
    # **这一行是给非 Windows runner 用的。** `create_shortcuts` 的自启那半现在
    # 走 `set_autostart()`，而它第一步 `_autostart_probe()` 在 POSIX 上直接返回
    # `(None, "非 Windows 系统…")`——于是 `make_shortcut` 只被调一次，CI 的
    # ubuntu job 上就变成 `assert 1 == 2`（W9 实测）。
    # 这条用例要钉的是「frozen 时目标/参数指向哪」，不是「这台机器有没有启动
    # 文件夹」，所以把平台门打开、其余照旧走真实代码。
    monkeypatch.setattr(
        install, "_autostart_probe",
        lambda: (tmp_path / "startup" / install.AUTOSTART_NAME, ""))

    install.create_shortcuts(with_autostart=True)

    assert len(calls) == 2
    desktop_call, startup_call = calls
    assert desktop_call[1] == install.UI_PATH and desktop_call[2] == ""
    assert startup_call[1] == install.UI_PATH
    assert startup_call[2] == "tray --silent", "开机自启必须走 tray 子命令"
    assert exe.name == "通知.exe"
