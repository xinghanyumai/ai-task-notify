"""W3.5 验收场景：造一份临时配置 + 日志，给截图和断言共用。

每个场景都是「一个自包含的临时目录」，四个路径常量一起改（`CONFIG_PATH` /
`LOG_DIR` / `STATE_DIR` / `SESSION_DIR`）——只改 `CONFIG_PATH` 会读到真实仓库
的日志，这条是 W3.2 验收踩过的坑。

场景：
    drafts     两条通道都有草稿可改（验 D：跨通道保存不冲掉）
    empty      今天一条通知都没有（验空态文案）
    nolog      关掉 log 通道 + 一条真发出去的通知（验 B）
    yesterday  今天一条 + 昨天一条（验 C 跨天）
    console    一个连不通的 webhook，测试要跑满 8 秒（验 F 切页清理）
"""

from __future__ import annotations

import datetime
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 黑洞地址：TCP 连不上也回不来，正好把「测试此通道」拖到 http_timeout。
BLACKHOLE = "http://10.255.255.1/hook"


def _isolate(tmp: Path) -> None:
    from wbn import config

    config.CONFIG_PATH = tmp / "config.json"
    config.LOG_DIR = tmp / "logs"
    config.STATE_DIR = tmp / "state"
    config.SESSION_DIR = tmp / "sessions"
    config.ensure_dirs()


def _base_cfg() -> dict:
    from wbn import config

    cfg = config.load()
    cfg["enabled"] = True
    cfg["triggers"] = {k: (k in ("task_done", "task_failed"))
                       for k in cfg["triggers"]}
    for ch in cfg["channels"]:
        ch["enabled"] = False
        ch["manual_off"] = True
    return cfg


def _ch(cfg: dict, ctype: str) -> dict:
    for ch in cfg["channels"]:
        if ch["type"] == ctype:
            return ch
    raise KeyError(ctype)


def build(name: str, base: Path | None = None) -> Path:
    """造好场景目录并返回它。**调用方负责把四个路径常量指过去。**"""
    tmp = Path(base or tempfile.mkdtemp(prefix=f"wbn-w35-{name}-"))
    _isolate(tmp)

    from wbn import config

    cfg = _base_cfg()
    today = datetime.date.today()
    day = today.isoformat()

    if name == "drafts":
        # 两条远端通道都填齐并启用，用户随便改哪条都能保存
        _ch(cfg, "bark").update({"key": "bark-key-on-disk", "enabled": True,
                                 "manual_off": False})
        _ch(cfg, "wxmp").update({
            "appid": "wxid_on_disk", "secret": "secret_on_disk",
            "template_id": "tpl_on_disk", "openid": "openid_on_disk",
            "enabled": True, "manual_off": False,
        })

    elif name == "empty":
        _ch(cfg, "wxmp").update({
            "appid": "a", "secret": "s", "template_id": "t", "openid": "o",
            "enabled": True, "manual_off": False,
        })

    elif name == "nolog":
        # 远端通道指向本机 200 服务（由 accept 脚本起），log 通道**关掉**
        _ch(cfg, "webhook").update({"url": "http://127.0.0.1:8765/hook",
                                    "enabled": True, "manual_off": False})
        _ch(cfg, "log")["enabled"] = False
        _ch(cfg, "log")["manual_off"] = True

    elif name == "yesterday":
        _ch(cfg, "wxmp").update({
            "appid": "a", "secret": "s", "template_id": "t", "openid": "o",
            "enabled": True, "manual_off": False,
        })
        y = (today - datetime.timedelta(days=1)).isoformat()
        (config.LOG_DIR / f"notify-{y}.log").write_text(
            "[23:50:00] event=Stop kind=task_done sent=True remote_ok=True "
            "elapsed=1.00s title=✅ 昨晚跑完的那次\n",
            encoding="utf-8",
        )
        (config.LOG_DIR / f"notify-{day}.log").write_text(
            "[00:10:00] event=Stop kind=task_done sent=True remote_ok=True "
            "elapsed=1.00s title=✅ 今天凌晨那次\n",
            encoding="utf-8",
        )

    elif name == "console":
        # 一个连不通的 webhook：测试会一直等到 http_timeout，切页时它还在飞
        _ch(cfg, "webhook").update({"url": BLACKHOLE, "enabled": True,
                                    "manual_off": False})
        cfg.setdefault("advanced", {})["http_timeout"] = 8

    else:
        raise SystemExit(f"不认识的场景：{name}")

    config.save(cfg)
    return tmp


def clean(tmp: Path) -> None:
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("用法：python tools/w35_scenarios.py <场景名>")
    d = build(sys.argv[1])
    print(d)
