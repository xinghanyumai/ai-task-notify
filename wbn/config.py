"""配置加载 / 保存 / 默认值。

设计要点：
- 用户 config.json 只写需要覆盖的字段，其余从 DEFAULT_CONFIG 深合并补全，
  这样升级版本时新增配置项会自动出现，不会因为缺字段报 KeyError。
- 所有路径基于 `_resolve_root()` 推导，整个目录可以整体搬走。
- **打包后数据目录和资源目录是两个地方**：资源（webui/、图标）在
  `sys._MEIPASS`（只读、退出即删），配置 / 日志 / 状态在 exe 旁边（或回落
  `%LOCALAPPDATA%`）。两者混用就是 R2：用户填的 10 个通道凭证重启全没。
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path


def _resolve_root() -> Path:
    """**数据**根目录：配置、日志、状态写这里。

    - 源码态：仓库根（`wbn/` 的上一级）
    - 打包后：**exe 所在目录**（绿色便携，拷走整个文件夹就带走配置）
    - exe 目录不可写时（放在 `C:\\Program Files`）：回落 `%LOCALAPPDATA%\\WBNotify`

    为什么不能用 `Path(__file__)`：PyInstaller onefile 把代码解压到
    `%TEMP%\\_MEIxxxxxx`，`__file__` 指着那里，**退出即删**。照那个路径存配置，
    用户重启后所有凭证、日志、状态全没——而且不会有任何报错。
    """
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent.parent

    exe_dir = Path(sys.executable).resolve().parent
    # 真去写一个文件再删，而不是 `os.access(dir, W_OK)`：Windows 上后者对
    # 目录基本恒为 True（它只看只读属性，不看 ACL），Program Files 会漏判。
    try:
        probe = exe_dir / ".wbn_write_probe"
        probe.touch()
        probe.unlink()
    except OSError:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "WBNotify"
    return exe_dir


def _resolve_bundle() -> Path:
    """**资源**根目录：webui/、assets/ 这些只读文件从这里读。

    源码态和 `_resolve_root()` 是同一个目录；打包后是 `_MEIPASS` 那个临时解压
    目录。分开两个函数是为了让「哪个路径能写」在调用处一眼可辨——
    `ROOT` 能写、`BUNDLE` 不能写。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


ROOT = _resolve_root()
BUNDLE = _resolve_bundle()
CONFIG_PATH = ROOT / "config.json"
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"
SESSION_DIR = STATE_DIR / "sessions"


def resource(*parts: str) -> Path:
    """拼一个**只读资源**路径（webui/index.html、assets/icon.ico…）。

    永远不要拿它当写入目标——打包后那是 `%TEMP%` 里的解压目录，写进去的东西
    进程退出就没了。

    每次调用重新解析而不是复用模块级 `BUNDLE`：`_MEIPASS` 是**运行时**才有的，
    而且测试要能 monkeypatch 它。这个函数一次启动只被调几次，不值得缓存。
    """
    return _resolve_bundle().joinpath(*parts)


# 已下线的通道类型。老版本 config.json 里可能还留着，加载时静默丢弃，
# 免得界面上冒出无法配置、也发不出去的僵尸通道。
#   pushplus —— 需要实名 + 付费，改用 wxmp（公众号测试号，原理相同且免费）
#   desktop  —— 宿主本身已有完成提示，重复弹气泡没有意义
RETIRED_CHANNELS = {"pushplus", "desktop"}

# ---------------------------------------------------------------- 默认配置

DEFAULT_CONFIG = {
    "version": 1,
    "enabled": True,
    # 通知里显示的宿主名称，会写进 {app} 占位符
    "app_name": "WorkBuddy",

    # 通道列表：可同时启用多个，任一成功即视为送达
    "channels": [
        # 自建微信推送：走公众号模板消息接口，无需实名、不限条数
        {
            "type": "wxmp",
            "enabled": False,
            "appid": "",
            "secret": "",
            "template_id": "",
            "openid": "",
            "url": "",
            # 详情过长时主动折行（插 \n）。旧模板样式才支持，测试号自建模板正是
            # 旧样式，所以默认开着；关掉就退回单行截断。
            "wrap": True,
        },
        # 企业微信自建应用：markdown 自由排版，不限条数
        {
            "type": "wecom_app",
            "enabled": False,
            "corpid": "",
            "secret": "",
            "agentid": "",
            "touser": "@all",
        },
        {
            "type": "bark",
            "enabled": False,
            "server": "https://api.day.app",
            "key": "",
            "group": "AI 任务",
            "sound": "bell",
            "level": "active",
            "icon": "",
        },
        {
            "type": "ntfy",
            "enabled": False,
            "server": "https://ntfy.sh",
            "topic": "",
            "priority": 3,
            "tags": "robot",
        },
        {"type": "wecom_bot", "enabled": False, "webhook": ""},
        {"type": "dingtalk_bot", "enabled": False, "webhook": "", "secret": ""},
        {"type": "feishu_bot", "enabled": False, "webhook": ""},
        {
            "type": "smtp",
            "enabled": False,
            "host": "smtp.qq.com",
            "port": 465,
            "ssl": True,
            "user": "",
            "password": "",
            "to": "",
        },
        {
            "type": "webhook",
            "enabled": False,
            "url": "",
            "method": "POST",
            "headers": {},
            "body_template": "",
        },
        # 落盘日志：永远开着，方便排查
        {"type": "log", "enabled": True},
    ],

    # 哪些语义事件要通知
    "triggers": {
        "task_done": True,      # 一轮任务正常结束
        "task_failed": True,    # 明确失败
        "need_input": True,     # 需要你确认 / 在等你回答
        "tool_failed": False,   # 工具连续失败（默认关，噪音较大）
        "session_start": False, # 会话开始
        "session_end": False,   # 会话结束
    },

    "filters": {
        # 一轮任务耗时低于该秒数不通知，避免「你好」也弹窗
        "min_duration_seconds": 20,
        # 助手最后一句是问句时，归为「等你处理」而不是「已完成」
        "question_as_need_input": True,
        # 静默时段，24 小时制，可写多段："23:00-08:00,12:00-13:30"，留空表示不限制
        "quiet_hours": "",
        # 只通知这些项目（按 cwd 子串匹配，留空表示全部）
        "projects_include": [],
        # 排除这些项目
        "projects_exclude": [],
        # 摘要必须包含这些关键词之一才通知（留空表示不限制）
        "keywords_include": [],
        # 相同内容在该秒数内只推一次
        "dedup_seconds": 180,
        # 频率闸门（微信模板消息接口有分钟级频次限制，留点余量）
        "max_per_minute": 4,
        "max_per_hour": 20,
        # 同一工具连续失败多少次才通知
        "tool_fail_threshold": 3,
    },

    # 内容模板：{占位符} 见 README，未知占位符渲染为空字符串
    "templates": {
        "task_done": (
            "**项目**：{project}\n"
            "**耗时**：{duration}\n"
            "**时间**：{time}\n"
            "\n"
            "{summary}"
        ),
        "task_failed": (
            "**项目**：{project}\n"
            "**原因**：{error}\n"
            "**时间**：{time}\n"
            "\n"
            "{summary}"
        ),
        "need_input": (
            "**项目**：{project}\n"
            "**提示**：{summary}\n"
            "**时间**：{time}"
        ),
        "tool_failed": (
            "**项目**：{project}\n"
            "**工具**：{tool}\n"
            "**错误**：{error}\n"
            "**时间**：{time}"
        ),
        "session_start": "**项目**：{project}\n**时间**：{time}",
        "session_end": "**项目**：{project}\n**时间**：{time}",
        "test": "通道工作正常。\n**时间**：{time}\n**主机**：{host}",
    },

    "titles": {
        "task_done": "✅ {app} 任务完成",
        "task_failed": "❌ {app} 任务失败",
        "need_input": "🙋 {app} 等你确认",
        "tool_failed": "⚠️ 工具失败 · {tool}",
        "session_start": "▶️ {app} 会话开始",
        "session_end": "⏹ {app} 会话结束",
        "test": "🔔 通知测试",
    },

    "advanced": {
        "http_timeout": 8,
        "retries": 2,
        # 是否把助手最后一段回复摘要进通知
        "include_summary": True,
        "summary_max_chars": 200,
        # 日志保留天数
        "log_retention_days": 14,
        # hook 自身最长占用时间（毫秒），超时直接放弃，保证不卡住 agent
        "hook_budget_ms": 1500,
    },

    # 设置中心自己的偏好
    "ui": {
        # light / dark / auto（跟随系统）。默认浅色，与 WorkBuddy 出厂外观一致
        "theme": "light",
    },
}


# ---------------------------------------------------------------- 工具函数


def deep_merge(base: dict, override: dict) -> dict:
    """把 override 深合并进 base 的副本。

    dict 递归合并；list 与标量直接替换（用户显式写了就以用户为准）。
    """
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def ensure_dirs() -> None:
    for d in (STATE_DIR, LOG_DIR, SESSION_DIR):
        d.mkdir(parents=True, exist_ok=True)


def harden_file(path: Path) -> None:
    """把含凭证的文件收紧成「只有当前用户能读写」。

    config.json 里存着 appsecret、SMTP 授权码、webhook key，
    state/token_cache.json 里存着 access_token。默认继承的 ACL 可能让
    Administrators 组或其他本地账户也读得到，没必要。

    Windows 用 icacls 断开继承后只授当前用户；其他平台用 0600。
    失败就静默跳过——收紧权限失败不应该让保存配置这件事本身失败。
    """
    try:
        if not path.exists():
            return
        if os.name != "nt":
            os.chmod(path, 0o600)
            return

        user = os.environ.get("USERNAME") or ""
        if not user:
            return
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            timeout=8,
            check=False,
        )
    except Exception:
        pass


def _merge_channels(defaults: list, user: list | None) -> list:
    """按 type 逐项深合并通道列表。

    deep_merge 对 list 是整体替换，会让升级后新增的通道凭空消失。
    这里改成：以用户列表为准、顺序保留，逐项与同名默认项合并，
    最后把默认里有、用户没有的通道追加到末尾。
    """
    if not isinstance(user, list) or not user:
        return copy.deepcopy(defaults)

    by_type = {d.get("type"): d for d in defaults}
    merged_map: dict = {}
    extras: list = []
    for item in user:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t in RETIRED_CHANNELS:  # 已下线的通道，老配置里可能还留着，直接丢弃
            continue
        base = by_type.get(t)
        merged_map[t] = deep_merge(base, item) if isinstance(base, dict) else copy.deepcopy(item)

    # 顺序以默认列表为准，保证界面里通道排序稳定
    out: list = [merged_map.pop(d.get("type"), None) or copy.deepcopy(d) for d in defaults]
    for item in user:  # 用户自定义的额外通道（默认里没有的）追加到末尾
        if isinstance(item, dict) and item.get("type") in merged_map:
            extras.append(merged_map.pop(item["type"]))
    return out + extras


def load() -> dict:
    """读取配置并与默认值合并。配置文件缺失或损坏时回退到默认值。"""
    user = {}
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            user = {}
    cfg = deep_merge(DEFAULT_CONFIG, user)
    cfg["channels"] = _merge_channels(
        DEFAULT_CONFIG["channels"], user.get("channels") if isinstance(user, dict) else None
    )
    return cfg


def save(cfg: dict) -> None:
    """原子写入配置。"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, CONFIG_PATH)
    harden_file(CONFIG_PATH)   # 里面有明文凭证，收紧权限


def save_default_if_missing() -> bool:
    """首次运行时生成一份 config.json，返回是否新建。"""
    if CONFIG_PATH.exists():
        return False
    save(DEFAULT_CONFIG)
    return True


def get_channel(cfg: dict, ctype: str) -> dict | None:
    for c in cfg.get("channels", []):
        if c.get("type") == ctype:
            return c
    return None


def enabled_channels(cfg: dict) -> list[dict]:
    return [c for c in cfg.get("channels", []) if c.get("enabled")]
