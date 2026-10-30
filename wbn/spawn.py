"""拉起子进程时用的环境变量。

**这个模块只解决一个问题：打包后的 exe 再拉起自己时，子进程会挂死。**

PyInstaller onefile 的 bootloader 用一组 `_PYI_*` 环境变量在自己和
「真正跑 Python 的那个子进程」之间传状态：

    _PYI_PARENT_PROCESS_LEVEL   当前是第几层（0 = 还没解压）
    _PYI_APPLICATION_HOME_DIR   解压到哪（`%TEMP%\\_MEIxxxxxx`）
    _PYI_ARCHIVE_FILE           从哪个 exe 解出来的

子进程看到 `_PYI_PARENT_PROCESS_LEVEL > 0`，就认为「父进程已经解压好了，
我直接用那个目录，不要再解压」。这在 bootloader 自己 spawn 自己的场景里是
对的——**但我们的程序也会 spawn 自己**：

    hook.py  → 通知.exe dispatch --stdin
    tray.py  → 通知.exe            （托盘菜单「打开设置」）

这两处拿到的 `os.environ` 是继承来的，里面就带着 `_PYI_PARENT_PROCESS_LEVEL=1`。
于是子进程以为自己是「第二层」，去复用父进程的 `_MEIPASS`；而父进程
（hook）投递完 payload 立刻退出，退出时把 `_MEIPASS` 删了。

症状（2026-10-30 实测）：

    · 子进程既不报错也不干活——不是崩溃，是**挂住**（复现：给
      `通知.exe --version` 加上这三个变量，60 秒都不返回）
    · hook 的 Popen 写 stdin 时抛 BrokenPipe，被 `except Exception: pass` 吞掉
    · 结果：`dist/logs/` 根本没被创建，通知一条都发不出去
    · 开发态（python.exe）完全正常，因为它不认这些变量

**所以凡是从 frozen 进程里 spawn 自己，都必须用 `child_env()`。**
不是「加一层保险」——不加就是核心链路不通。
"""

from __future__ import annotations

import os

# bootloader 用的前缀。用前缀而不是列举全名：不同 PyInstaller 版本的
# 变量集合会变（5.x 有 `_MEIPASS2`，6.x 换成了 `_PYI_*`），
# 列举法每次升级都要改一遍，而漏一个的症状是「又挂住了」。
PYI_ENV_PREFIX = "_PYI_"

# 4.x/5.x 的老名字，一并清掉：用户机器上可能残留（比如从旧版升级上来、
# 或者从别的 PyInstaller 程序里被启动）。
LEGACY_KEYS = ("_MEIPASS2", "_MEIPASS")


def child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """返回一份可以安全传给子进程的环境变量。

    只删 `_PYI_*` 和老名字，其它一律保留——`PATH` / `LOCALAPPDATA` /
    用户自己的 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` 都要带过去。
    """
    env = dict(os.environ if base is None else base)
    for key in [k for k in env if k.startswith(PYI_ENV_PREFIX)]:
        env.pop(key, None)
    for key in LEGACY_KEYS:
        env.pop(key, None)
    return env
