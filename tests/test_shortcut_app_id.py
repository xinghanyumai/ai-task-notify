"""快捷方式要带 AppUserModelID —— 否则任务栏上是 Python 的图标。

## 为什么需要写 AUMID

Windows 任务栏按 AppUserModelID 分组、并据此决定按钮上显示哪个图标。
`.lnk` 里没有 AUMID 时，任务栏用**进程的可执行文件**（`pythonw.exe`）的图标——
用户看到一条快捷方式却顶着 Python 图标，会以为装错了。

`WScript.Shell` 的 `Shortcut` 对象**没有** AppUserModelID 属性，只能走
`IPropertyStore`（`SHGetPropertyStoreFromParsingName` + `PKEY_AppUserModel_ID`）。

## 两条守卫

1. 真造一个 .lnk，读回磁盘字节，断言里面有 AUMID 的 UTF-16LE。
   **读字节而不是读属性**：`SetValue` 不 `Commit` 时返回成功但磁盘上什么都没变，
   只有读字节才抓得住这个（§7.2 那条「必须 Commit」）。
2. AST 扫 `install.py`，AUMID 字面量只许出现在 `wbn/winapi.py`。
   两处同一字符串 = 改一处忘一处，而这个项目已经在这上面栽过。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import install  # noqa: E402
from wbn import winapi  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="IPropertyStore / .lnk 属性存储只在 Windows 上存在",
)


def test_make_shortcut_writes_app_user_model_id(tmp_path):
    """造 .lnk → 读回字节 → 里面必须有 AUMID 的 UTF-16LE。

    没有这条的话，「图标还是 Python 的」只能靠人肉看任务栏，而人肉看不见
    「写进去了但没 Commit」这种半成功——那正是 §7.2 列的第三个坑。
    """
    lnk = tmp_path / "aumid.lnk"
    assert install.make_shortcut(lnk, install.UI_PATH, "", "aumid") is True

    raw = lnk.read_bytes()
    want = winapi.APP_USER_MODEL_ID.encode("utf-16-le")
    assert want in raw, (
        f".lnk 里没有 AUMID（{winapi.APP_USER_MODEL_ID}）。\n"
        f"大小 {len(raw)} 字节——写成功的话会比裸 .lnk 大一百来字节。\n"
        "常见原因：忘了 Commit()（SetValue 返回成功但磁盘没变），"
        "或者把 SHGetPropertyStoreFromParsingName 挂在了 propsys.dll 上。")


def test_app_id_comes_from_winapi_not_a_literal():
    """`install.py` 里不许出现 AUMID 字面量，只能 import 常量。

    字面量出现两次就是「改一处忘一处」的入口，而这个项目在这上面
    已经栽过一次（`arg_line` 没加引号 vs `hook_command` 加了）。
    """
    src = (ROOT / "install.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    literals = [
        f"install.py:{n.lineno}: {n.value!r}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and n.value == winapi.APP_USER_MODEL_ID
    ]
    assert not literals, (
        "AUMID 字面量出现在 install.py 里了，应当 from wbn.winapi import "
        "APP_USER_MODEL_ID：\n" + "\n".join(literals))

    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "APP_USER_MODEL_ID" in names, (
        "install.py 根本没引用 APP_USER_MODEL_ID —— AUMID 那段没接上？")
