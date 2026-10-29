"""旧栈下线后的守卫：不许再有活引用，也不许有人把它加回来。

**这是验收第 4 条那条 grep 的可执行版本。**

任务书里写的是：

    grep -rn "import widgets\\|wbn.theme\\|wbn import theme\\|import tokens\\|..." --include="*.py" .

这条命令**永远不可能为空**，而且不该为空：`wbn/winapi.py` 的 docstring 必须写清
「这个函数原来住在 theme.py，直接删会让 app.py 静默降级」，`tests/fixtures/
guide_expected.py` 必须写清「原文快照自 ui.py」。这些都是**解释历史的散文**，
删掉它们才是损失。

真正该守的是「**可执行的**引用」：import 语句、指向已删文件的路径常量。
所以这里用 `ast` 而不是 grep——注释和 docstring 根本不在 AST 的 `Import` 节点里，
而 `ROOT / "ui.py"` 这种路径常量是一个 `Constant` 字符串，能被精确匹配到。

**为什么值得单独一个文件**：这是整个重构唯一不可逆的一步（6084 行，退路只有
`git tag tkinter-final`）。半年后有人「顺手把 theme.py 加回来放两个色值」，
下面两条断言会立刻叫。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 已删除的模块（import 名）与文件（磁盘路径）。
REMOVED_MODULES = {
    "ui",
    "wbn.theme",
    "wbn.tokens",
    "wbn.anim",
    "wbn.splash",
    "wbn.widgets",
}
REMOVED_FILES = (
    "ui.py",
    "wbn/widgets.py",
    "wbn/theme.py",
    "wbn/tokens.py",
    "wbn/anim.py",
    "wbn/splash.py",
)

# 不扫的目录：虚拟环境、临时脚本、归档文档（历史记录，改了就是伪造历史）。
SKIP_DIRS = {".venv", "__pycache__", ".git", "tmp", "archive", ".workbuddy-ai"}


def _py_files() -> list[Path]:
    out = []
    for p in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
            continue
        out.append(p)
    return sorted(out)


def _imports(tree: ast.AST) -> list[tuple[str, int]]:
    """`[(被 import 的完整名字, 行号)]`。`from wbn import theme` 记作 `wbn.theme`。"""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.append((a.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            for a in node.names:
                found.append((f"{base}.{a.name}" if base else a.name, node.lineno))
    return found


def test_removed_files_are_gone():
    """文件真的删了。退路是 `git checkout tkinter-final -- <文件>`，不是留在工作区。"""
    survivors = [name for name in REMOVED_FILES if (ROOT / name).exists()]
    assert not survivors, (
        f"这些文件应当已被删除，但还在：{survivors}。\n"
        "W5 的决定是全删（DECISIONS D1）——保留 legacy 就等于 6084 行死代码"
        "永远跟着 ruff / 依赖 / Python 版本一起维护。"
        "真需要看，用 `git show tkinter-final:<路径>`。")


def test_nothing_imports_the_removed_modules():
    """没有任何模块 import 已删的模块——包括 `from wbn import theme` 这种写法。"""
    bad: list[str] = []
    for path in _py_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for name, line in _imports(tree):
            # 精确匹配 + 前缀匹配（`wbn.theme.SOMETHING`）
            if name in REMOVED_MODULES or any(
                    name.startswith(m + ".") for m in REMOVED_MODULES):
                bad.append(f"{path.relative_to(ROOT)}:{line}: import {name}")

    assert not bad, (
        "有文件还在 import 已删除的旧栈模块：\n  " + "\n  ".join(bad) +
        "\n\n这类 import 在 `app.py` 里会**伪装成「WebView2 缺失」**（W5 §2.4）："
        "被 try 吞掉 → 静默降级 → 去起一个同样被删的入口 → 双击没反应。"
        "`set_app_user_model_id` 的新家是 `wbn/winapi.py`。")


def test_no_path_constant_points_at_a_removed_file():
    """`ROOT / "ui.py"` 这类路径常量也要清干净。

    验收第 4 条的 grep 抓的是字符串，这里抓的是**当成路径用的**字符串：
    任何值恰好等于已删文件名的常量都算残留。`install.py` 的 `UI_PATH` 就属于
    这一类——它指向已删的 `ui.py` 时，桌面快捷方式双击报「找不到文件」，
    而用户想不到是这次更新弄坏的。
    """
    targets = {name.split("/")[-1] for name in REMOVED_FILES}
    bad: list[str] = []
    for path in _py_files():
        # 本文件自己就是那张「已删文件」的表，跳过——否则守卫会把自己抓起来。
        # 这也是唯一允许出现这些字面量的地方。
        if path.name == Path(__file__).name:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value in targets):
                bad.append(f"{path.relative_to(ROOT)}:{node.lineno}: {node.value!r}")

    assert not bad, (
        "这些地方还拿着已删文件的文件名当路径用：\n  " + "\n  ".join(bad))
