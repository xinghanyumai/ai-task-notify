"""CI 工作流自检。

为什么值得写测试：workflow 是纯配置，改错了本地一点感觉都没有——要等到
推上去才发现 6 个 job 变成了 3 个、或者 lint 被悄悄挪到 pytest 后面。
这里把「快 → 慢」「Windows 全量 / ubuntu 只测」「矩阵 = 2×3」这些约定
变成断言，改配置时立刻知道破了哪条。

注意 PyYAML 的坑：YAML 1.1 会把裸 `on:` 解析成布尔 True，所以下面的
`_triggers()` 两种键名都要认。
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="需要 pyyaml（见 requirements-dev.txt）")

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW.is_file(), f"workflow 不存在：{WORKFLOW}"
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), "workflow 顶层必须是映射"
    return doc


def _triggers(doc: dict) -> dict:
    # YAML 1.1：裸 on: 会被解析成 True
    return doc.get("on") or doc.get(True) or {}


def _steps(doc: dict) -> list[dict]:
    (job,) = doc["jobs"].values()
    return job["steps"]


def _find_step(steps: list[dict], needle: str) -> tuple[int, dict]:
    for i, step in enumerate(steps):
        if needle in str(step.get("run", "")):
            return i, step
    raise AssertionError(f"找不到 run 含 {needle!r} 的步骤")


# ---------------------------------------------------------------- 结构


def test_workflow_parses(workflow):
    assert "jobs" in workflow


def test_runs_on_push_and_pull_request(workflow):
    trig = _triggers(workflow)
    assert "push" in trig
    assert "pull_request" in trig


def test_matrix_is_two_os_by_three_python(workflow):
    """验收要求：PR 上能看到 6 个 job。"""
    (job,) = workflow["jobs"].values()
    matrix = job["strategy"]["matrix"]

    assert matrix["os"] == ["windows-latest", "ubuntu-latest"]
    assert matrix["python-version"] == ["3.10", "3.12", "3.13"]
    assert len(matrix["os"]) * len(matrix["python-version"]) == 6


def test_fail_fast_is_on(workflow):
    (job,) = workflow["jobs"].values()
    assert job["strategy"]["fail-fast"] is True, "失败即停：不能让 5 个绿勾掩盖 1 个红叉"


# ---------------------------------------------------------------- 检查顺序


def test_checks_run_fast_to_slow(workflow):
    """ruff（秒）→ compileall（秒）→ pytest（十秒）。顺序反了就是浪费时间。"""
    steps = _steps(workflow)
    ruff_i, _ = _find_step(steps, "ruff check")
    compile_i, _ = _find_step(steps, "compileall")
    pytest_i, _ = _find_step(steps, "pytest")

    assert ruff_i < compile_i < pytest_i, "检查项没有按 快 → 慢 排列"


def test_lint_and_compile_only_run_on_windows(workflow):
    """本项目是 Windows 专有实现；在 ubuntu 上跑静态检查会给错的安全感。"""
    steps = _steps(workflow)
    for needle in ("ruff check", "compileall"):
        _, step = _find_step(steps, needle)
        assert step.get("if") == "runner.os == 'Windows'", f"{needle} 应只在 Windows 跑"


def test_pytest_step_does_not_exclude_by_name(workflow):
    """平台差异必须由 pytest.mark.skipif 表达，不能用 -k 把用例藏起来。"""
    steps = _steps(workflow)
    _, step = _find_step(steps, "pytest")
    run = str(step["run"])

    assert " -k " not in run, f"pytest 步骤里出现了 -k：{run}"
    assert "pytest -q" in run


def test_windows_stdio_is_utf8(workflow):
    """Windows runner 默认 cp936，断言里的中文/符号会在输出时 encode 失败。"""
    (job,) = workflow["jobs"].values()
    assert job["env"]["PYTHONUTF8"] == "1"


def test_python_is_set_up_before_dependencies(workflow):
    steps = _steps(workflow)
    setup_i = next(
        i for i, s in enumerate(steps)
        if str(s.get("uses", "")).startswith("actions/setup-python")
    )
    install_i, _ = _find_step(steps, "pip install")
    assert setup_i < install_i


# ---------------------------------------------------------------- pytest 配置


def test_network_tests_are_skipped_by_default(pytestconfig):
    addopts = pytestconfig.getini("addopts")
    assert "not network" in addopts, f"默认没有跳过 network 用例：{addopts!r}"


def test_gui_is_not_excluded_by_marker(pytestconfig):
    """gui 靠 skipif 自己跳，不能在 addopts 里被排除，否则 Windows 也跑不到。"""
    addopts = pytestconfig.getini("addopts")
    assert "not gui" not in addopts, "gui 用例不该用 marker 排除，应由 skipif 决定"


def test_both_markers_are_registered(pytestconfig):
    markers = "\n".join(pytestconfig.getini("markers"))
    assert "gui:" in markers
    assert "network:" in markers
