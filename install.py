"""安装器：把 hooks 写进宿主配置，并创建桌面 / 开机自启快捷方式。

用法：
    python install.py install      # 写入 hooks（默认 WorkBuddy + CodeBuddy）
    python install.py shortcut     # 创建桌面快捷方式 + 开机自启
    python install.py all          # 上面两步一起做
    python install.py status       # 查看安装状态
    python install.py uninstall    # 移除 hooks 与快捷方式

设计原则：
- 幂等：重复执行只会替换掉「我们自己的」hook 条目，不碰别人的。
- 安全：改动前自动备份 settings.json。
- 可逆：uninstall 能干净移除。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from wbn import config  # noqa: E402

HOOK_PATH = ROOT / "hook.py"
UI_PATH = ROOT / "ui.py"
TRAY_PATH = ROOT / "tray.py"
ICON_PATH = ROOT / "assets" / "icon.ico"

# 需要挂 hook 的事件 -> 超时秒数
HOOK_EVENTS: dict[str, int] = {
    "SessionStart": 10,
    "UserPromptSubmit": 10,
    "Stop": 15,
    "StopFailure": 10,
    "PostToolUseFailure": 10,
    "PermissionRequest": 10,
    "Notification": 10,
    "TaskCompleted": 10,
    "SessionEnd": 10,
}

# 宿主配置位置。第一项是 Windows 上 WorkBuddy 的配置主目录。
TARGETS = [
    ("WorkBuddy", Path.home() / ".workbuddy-ai" / "settings.json"),
    ("CodeBuddy", Path.home() / ".codebuddy" / "settings.json"),
]
LEGACY_TARGET = ("WorkBuddy(legacy)", Path.home() / ".workbuddy" / "settings.json")


# ------------------------------------------------------------------ 基础


def python_exe() -> Path:
    """hook 使用的解释器：优先当前 venv 的 pythonw.exe（不闪黑框）。"""
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return cand if cand.exists() else exe


def hook_command(app: str) -> str:
    """生成 hook 命令行。路径统一用正斜杠并加引号，兼容含空格 / 中文的目录。"""
    py = str(python_exe()).replace("\\", "/")
    hp = str(HOOK_PATH).replace("\\", "/")
    return f'"{py}" "{hp}" --app {app}'


def _norm(p: str) -> str:
    return str(p or "").replace("\\", "/").lower()


def is_our_hook(entry: dict) -> bool:
    """判断某条 hook 配置是不是本程序写入的。"""
    target = _norm(str(HOOK_PATH))
    for h in entry.get("hooks", []) or []:
        cmd = _norm(str(h.get("command", "")))
        if "hook.py" in cmd and (target in cmd or "wbn" in cmd):
            return True
    return False


def build_hooks(app: str) -> dict:
    cmd = hook_command(app)
    out = {}
    for ev, timeout in HOOK_EVENTS.items():
        out[ev] = [
            {
                "hooks": [
                    {"type": "command", "command": cmd, "timeout": timeout}
                ]
            }
        ]
    return out


def merge_hooks(existing: dict, app: str) -> dict:
    """把我们的 hooks 合并进已有配置，保留第三方 hook。"""
    ours = build_hooks(app)
    merged = dict(existing or {})
    for ev, entries in ours.items():
        current = merged.get(ev)
        current = current if isinstance(current, list) else []
        kept = [e for e in current if not (isinstance(e, dict) and is_our_hook(e))]
        merged[ev] = kept + entries
    return merged


def strip_hooks(existing: dict) -> dict:
    """移除我们的 hooks，其它原样保留。"""
    out = {}
    for ev, entries in (existing or {}).items():
        if not isinstance(entries, list):
            out[ev] = entries
            continue
        kept = [e for e in entries if not (isinstance(e, dict) and is_our_hook(e))]
        if kept:
            out[ev] = kept
    return out


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        # from None：这是给用户看的 CLI 提示，不需要 Python 再把 JSON 解析器的
        # 原始 traceback 挂在后面
        raise SystemExit(f"✘ 无法解析 {path}：{e}\n  请先手动修好该文件再重试。") from None


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
        print(f"   已备份 → {backup.name}")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ------------------------------------------------------------------ 图标


def ensure_icon() -> Path | None:
    """确保 assets/icon.ico 存在。

    图标由 tools/make_icon.py 统一生成（品牌青绿 + 白铃铛，9 档尺寸），
    这里只负责在文件缺失时补生成一次，不再另画一套，
    避免「快捷方式用一套图标、窗口用另一套」的割裂。
    """
    if ICON_PATH.exists():
        return ICON_PATH
    try:
        # make_icon 住在 tools/，不在 ROOT 上。只在这里补一次路径，
        # 不往全局 sys.path 里塞——安装器的其它部分不需要它。
        tools_dir = str(ROOT / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import make_icon
    except Exception:
        return None
    try:
        make_icon.main()
    except Exception:
        return None
    return ICON_PATH if ICON_PATH.exists() else None


# ------------------------------------------------------------------ 快捷方式


def _ps(script: str) -> str:
    """执行一段 PowerShell，返回 stdout。"""
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=0x08000000,
        )
        return (proc.stdout or "").strip()
    except Exception as e:
        return f"__ERR__{e}"


def _desktop_dir() -> Path:
    out = _ps("[Environment]::GetFolderPath('Desktop')")
    if out and not out.startswith("__ERR__"):
        return Path(out)
    return Path.home() / "Desktop"


def _startup_dir() -> Path:
    out = _ps("[Environment]::GetFolderPath('Startup')")
    if out and not out.startswith("__ERR__"):
        return Path(out)
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft/Windows/Start Menu/Programs/Startup"


def _psq(text: str) -> str:
    """包成 PowerShell 单引号字符串。

    单引号串里唯一的转义是「两个单引号表示一个单引号」。
    Windows 文件名允许出现单引号，不转义的话
    `D:\\it's here\\` 这样的路径会直接把脚本截断。
    """
    return "'" + str(text).replace("'", "''") + "'"


def _psdq(text: str) -> str:
    """包成 PowerShell 双引号字符串。

    双引号串里 ` $ 都是元字符，必须用反引号转义。
    Windows 文件名不允许 "，但路径里可能有 $（如 C:\\$Recycle.Bin），
    所以 $ 这一条是实打实需要的。
    """
    esc = str(text).replace("`", "``").replace('"', '`"').replace("$", "`$")
    return f'"{esc}"'


def make_shortcut(
    lnk: Path,
    script: Path,
    args: str = "",
    desc: str = "",
    window_style: int = 1,
) -> bool:
    """用 WScript.Shell 创建 .lnk。window_style: 1 正常 / 7 最小化。"""
    icon = ensure_icon()
    py = str(python_exe())
    arg_line = str(script)
    if args:
        arg_line += f" {args}"

    ps = (
        "$ws = New-Object -ComObject WScript.Shell;"
        f"$s = $ws.CreateShortcut({_psdq(lnk)});"
        f"$s.TargetPath = {_psdq(py)};"
        f"$s.Arguments = {_psq(arg_line)};"
        f"$s.WorkingDirectory = {_psdq(ROOT)};"
        f"$s.Description = {_psdq(desc or 'AI 任务通知')};"
        + (f"$s.IconLocation = {_psdq(icon)};" if icon else "")
        + f"$s.WindowStyle = {int(window_style)};"
        "$s.Save();"
        'Write-Output "OK"'
    )
    out = _ps(ps)
    return out.strip().endswith("OK") and lnk.exists()


def create_shortcuts(with_autostart: bool = True) -> None:
    print("\n[创建快捷方式]")
    desktop = _desktop_dir()
    ok1 = make_shortcut(
        desktop / "AI 任务通知.lnk",
        UI_PATH,
        "",
        "AI 任务通知 · 设置中心",
        window_style=1,
    )
    print(f"  {'✔' if ok1 else '✘'} 桌面：{desktop / 'AI 任务通知.lnk'}")

    if with_autostart:
        startup = _startup_dir()
        target = TRAY_PATH if TRAY_PATH.exists() else UI_PATH
        ok2 = make_shortcut(
            startup / "AI 任务通知.lnk",
            target,
            "--silent",
            "AI 任务通知 · 开机自启",
            window_style=7,
        )
        print(f"  {'✔' if ok2 else '✘'} 开机自启：{startup / 'AI 任务通知.lnk'}")
        if ok2:
            print("     （托盘常驻，可在托盘菜单里暂停 / 测试 / 打开设置）")


def remove_shortcuts() -> None:
    print("\n[移除快捷方式]")
    for d in (_desktop_dir(), _startup_dir()):
        p = d / "AI 任务通知.lnk"
        try:
            if p.exists():
                p.unlink()
                print(f"  ✔ 已删除 {p}")
        except OSError as e:
            print(f"  ✘ 删除失败 {p}: {e}")


# ------------------------------------------------------------------ 主流程


def install(also_legacy: bool = False) -> None:
    config.ensure_dirs()
    created = config.save_default_if_missing()
    if created:
        print(f"✔ 已生成默认配置 {config.CONFIG_PATH}")

    targets = list(TARGETS)
    if also_legacy:
        targets.append(LEGACY_TARGET)

    print("\n[写入 hooks]")
    for app, path in targets:
        if not path.parent.exists():
            print(f"  · 跳过 {app}（未安装：{path.parent}）")
            continue
        try:
            data = read_json(path)
            data["hooks"] = merge_hooks(data.get("hooks", {}), app)
            write_json(path, data)
            print(f"  ✔ {app}: {path}")
        except SystemExit:
            raise
        except Exception as e:
            print(f"  ✘ {app} 写入失败: {e}")

    print(
        "\n⚠ 重要：hooks 配置在宿主启动时读取并缓存，"
        "\n   必须【完全退出并重启】WorkBuddy / CodeBuddy 才会生效。"
        "\n   注意 WorkBuddy 点窗口关闭按钮只是隐藏到托盘，"
        "\n   要在托盘图标上右键 → 退出，或用任务管理器结束进程。"
    )


def uninstall() -> None:
    print("[移除 hooks]")
    for app, path in TARGETS + [LEGACY_TARGET]:
        if not path.exists():
            continue
        try:
            data = read_json(path)
            if "hooks" in data:
                data["hooks"] = strip_hooks(data.get("hooks", {}))
                if not data["hooks"]:
                    data.pop("hooks", None)
                write_json(path, data)
                print(f"  ✔ {app}: 已清理 {path}")
        except Exception as e:
            print(f"  ✘ {app}: {e}")
    remove_shortcuts()
    print("\n完成。配置文件与日志保留在项目目录，可手动删除。")


def hooks_status() -> list[str]:
    """返回各宿主的 hooks 安装状态描述，供 selftest / UI 使用。"""
    lines = []
    for app, path in TARGETS:
        if not path.exists():
            lines.append(f"· {app:10s} 未安装（{path.parent} 不存在）")
            continue
        try:
            data = read_json(path)
        except SystemExit as e:
            lines.append(f"✘ {app:10s} 配置损坏：{e}")
            continue
        hooks = data.get("hooks", {}) or {}
        installed = [ev for ev, entries in hooks.items() if any(
            isinstance(e, dict) and is_our_hook(e) for e in (entries or [])
        )]
        if installed:
            lines.append(
                f"✔ {app:10s} 已挂载 {len(installed)} 个事件（{', '.join(sorted(installed)[:4])}…）"
            )
        else:
            lines.append(f"· {app:10s} 未安装 hooks")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 任务通知 · 安装器")
    ap.add_argument(
        "action",
        nargs="?",
        default="all",
        choices=["install", "shortcut", "all", "uninstall", "status"],
    )
    ap.add_argument("--no-autostart", action="store_true", help="不创建开机自启")
    ap.add_argument("--also-legacy", action="store_true", help="同时写入 ~/.workbuddy")
    args = ap.parse_args()

    if args.action == "status":
        from selftest import show_status

        show_status()
        return 0

    if args.action == "uninstall":
        uninstall()
        return 0

    if args.action in ("install", "all"):
        install(also_legacy=args.also_legacy)
    if args.action in ("shortcut", "all"):
        create_shortcuts(with_autostart=not args.no_autostart)

    if args.action == "all":
        print("\n[安装后自检]")
        for line in hooks_status():
            print("  " + line)
        print("\n下一步：双击桌面「AI 任务通知」，选一个手机通道并点『发送测试』。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
