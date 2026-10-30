"""自嵌套 spawn 的环境变量必须干净。

**这条测试挡的是一个「开发态永远看不到、装完就是核心功能全废」的坑。**

PyInstaller onefile 的 bootloader 用 `_PYI_*` 变量告诉子进程「父进程已经解压
好了，直接复用 `_MEIPASS`」。这对自己 spawn 自己是对的，但我们的程序也会
spawn 自己（`hook → dispatch`、`tray → 打开设置`），继承来的变量会让子进程
去复用**一个马上就要被删掉的临时目录**。

2026-10-30 实测的症状（W7 打包第一版）：hook 退出码 0、`Popen` 不抛异常、
日志目录根本没被创建、通知一条都发不出去。而 `python app.py hook` 完全正常。

所以这里断言的不是「函数能跑」，而是**契约**：
    `_PYI_*` 一个都不许剩，其它变量一个都不许少
"""

from __future__ import annotations

from wbn.spawn import child_env


def test_strips_all_pyi_prefixed_vars():
    env = child_env({
        "_PYI_PARENT_PROCESS_LEVEL": "1",
        "_PYI_APPLICATION_HOME_DIR": r"C:\Temp\_MEI123",
        "_PYI_ARCHIVE_FILE": r"C:\dist\通知.exe",
        "PATH": r"C:\Windows",
    })
    assert [k for k in env if k.startswith("_PYI_")] == []


def test_strips_legacy_meipass_keys():
    """PyInstaller 4/5 的老名字，用户机器上可能还残留。"""
    env = child_env({"_MEIPASS2": r"C:\Temp\_MEI123", "_MEIPASS": r"C:\Temp\_MEI123"})
    assert env == {}


def test_keeps_everything_else():
    """PATH / LOCALAPPDATA / 用户自己的 WEBVIEW2 参数都要带过去。

    这条不是凑数：清多了会让子进程找不到 WebView2 运行时（LOCALAPPDATA）
    或者找不到系统 DLL（PATH），症状比原来的挂死更难查。
    """
    src = {
        "PATH": r"C:\Windows\system32",
        "LOCALAPPDATA": r"C:\Users\x\AppData\Local",
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--no-sandbox",
        "_PYI_PARENT_PROCESS_LEVEL": "1",
    }
    env = child_env(src)
    assert env == {k: v for k, v in src.items() if not k.startswith("_PYI_")}


def test_does_not_mutate_input():
    """传进来的 dict 不许被改——调用方可能还要用。"""
    src = {"_PYI_PARENT_PROCESS_LEVEL": "1", "PATH": "x"}
    child_env(src)
    assert src == {"_PYI_PARENT_PROCESS_LEVEL": "1", "PATH": "x"}


def test_defaults_to_os_environ(monkeypatch):
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    monkeypatch.setenv("WBN_SENTINEL_FOR_TEST", "kept")
    env = child_env()
    assert "_PYI_PARENT_PROCESS_LEVEL" not in env
    assert env["WBN_SENTINEL_FOR_TEST"] == "kept"
