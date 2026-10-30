"""目录树不能撒谎。

`docs/architecture.md` 的「目录结构」一节在 W5 之后烂过一次：里面还写着
`ui.py`（已删）、`wbn/widgets.py`（已删），而 `wbn/insight.py`、`wbn/bridge.py`
这些新东西一个都没有。烂掉的原因很朴素——**没人会去读自己刚写的文档**，
而目录结构是「看一眼就以为懂了」的那类文档，最容易过期。

W7 §1.1 重画之后，把验收条件变成断言：

    树里出现的每个路径，真实存在
    仓库里每个 .py（根 / wbn / tools），树里都有

第二半是关键：只查「树里写的都存在」会漏掉「新加了文件没写进树」，
而后者才是这份文档最常见的坏法。

**`tests/` 只按目录列一行**，不逐个点名——29 个用例文件全写进树里，
树就没法看了，而且每加一条测试都要改文档，人会开始讨厌这条测试。
这个取舍写在断言里（`_EXEMPT_DIRS`），不是忘了。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "architecture.md"

# 只按目录列一行的，不要求逐个点名
_EXEMPT_DIRS = {"tests", "docs", "assets", "state", "logs"}

# 不参与扫描的目录：环境 / 构建产物 / 工具自己的数据
_SKIP_DIRS = {
    ".git", ".github", ".venv", "venv", "build", "dist", "tmp",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".workbuddy-ai", ".codebuddy",
}

# 树里用 `xxx_*.py` 这种通配表示「一族脚本」，不要求精确
_GLOB_CHARS = "*?["


def _tree_block() -> str:
    """取出 `## 目录结构` 后面那个 ``` 代码块。"""
    text = DOC.read_text(encoding="utf-8")
    m = re.search(r"^## 目录结构\s*$", text, re.MULTILINE)
    assert m, "architecture.md 里找不到「## 目录结构」这一节"
    rest = text[m.end():]
    m2 = re.search(r"^```[a-z]*\n(.*?)^```", rest, re.MULTILINE | re.DOTALL)
    assert m2, "「目录结构」下面没有代码块"
    return m2.group(1)


def _entries() -> list[tuple[str, bool, bool]]:
    """解析树，返回 [(相对路径, 是不是目录, 是否通配)]。"""
    out: list[tuple[str, bool, bool]] = []
    stack: list[str] = []
    for raw in _tree_block().splitlines():
        if not raw.strip() or "── " not in raw:
            continue
        pos = raw.rindex("── ") + len("── ")
        # 每个层级固定 4 字符宽（`│   ` 或 `    ` 或 `├── `）
        depth = pos // 4
        # 名字和说明之间通常用 2+ 个空格对齐；名字太长时只留 1 个空格，
        # 所以两种都要认。名字本身不含空格（都是路径），说明里有。
        body = raw[pos:].strip()
        parts = re.split(r"\s{2,}", body, maxsplit=1)
        name = parts[0]
        if len(parts) == 1 and " " in body:
            name = body.split(" ", 1)[0]
        name = name.strip()
        if not name or name == ".":
            continue
        is_dir = name.endswith("/")
        name = name.rstrip("/")
        stack = stack[: depth - 1]
        stack.append(name)
        rel = "/".join(stack)
        out.append((rel, is_dir, any(c in name for c in _GLOB_CHARS)))
    return out


@pytest.fixture(scope="module")
def entries():
    e = _entries()
    assert len(e) > 30, f"目录树只解析出 {len(e)} 条，解析器坏了或树被删了"
    return e


def test_every_path_in_tree_exists(entries):
    """树里写的每个路径都必须真实存在。"""
    missing = [
        rel for rel, _is_dir, is_glob in entries
        if not is_glob and not (ROOT / rel).exists()
    ]
    assert not missing, (
        "architecture.md 的目录树里有这些路径，但仓库里没有：\n  "
        + "\n  ".join(missing)
        + "\n（删了文件忘了改文档，或者写错了名字）"
    )


def test_dir_and_file_markers_match_reality(entries):
    """带 `/` 的必须是目录，不带 `/` 的必须是文件。

    这条挡的是「把文件写成目录」这种最容易被忽略的错——树里一个斜杠之差，
    读的人会以为那是个包。
    """
    wrong = []
    for rel, is_dir, is_glob in entries:
        if is_glob:
            continue
        p = ROOT / rel
        if not p.exists():
            continue        # 上一条已经报过了
        if is_dir and not p.is_dir():
            wrong.append(f"{rel}：树里标成目录，实际是文件")
        if not is_dir and not p.is_file():
            wrong.append(f"{rel}：树里标成文件，实际是目录")
    assert not wrong, "目录树的标记和实际不符：\n  " + "\n  ".join(wrong)


def test_globs_match_at_least_one_file(entries):
    """通配项至少要命中一个文件，否则就是删光了没更新。"""
    empty = [
        rel for rel, _is_dir, is_glob in entries
        if is_glob and not list(ROOT.glob(rel))
    ]
    assert not empty, "这些通配项一个文件都没命中：\n  " + "\n  ".join(empty)


def test_every_python_file_is_listed(entries):
    """仓库里每个 .py 都要在树里出现。

    **这是这条测试真正的价值**：新加一个 `wbn/foo.py` 而没写进树，
    文档就开始过期了，而且没有任何症状。
    """
    listed = {rel for rel, _d, _g in entries}
    globs = [rel for rel, _d, is_glob in entries if is_glob]

    actual: list[str] = [p.name for p in sorted(ROOT.glob("*.py"))]
    for d in sorted(p for p in ROOT.iterdir() if p.is_dir()):
        if d.name in _SKIP_DIRS or d.name in _EXEMPT_DIRS or d.name.startswith("."):
            continue
        for py in sorted(d.glob("*.py")):
            actual.append(f"{d.name}/{py.name}")

    missing = []
    for rel in actual:
        if rel in listed:
            continue
        if any(Path(rel).match(g) for g in globs):
            continue
        missing.append(rel)

    assert not missing, (
        "这些 .py 没写进 architecture.md 的目录树：\n  "
        + "\n  ".join(missing)
        + "\n（新加了文件就补一行，别让文档开始撒谎）"
    )
