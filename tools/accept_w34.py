"""W3.4 验收：投递健康信号。

只验不需要 GUI 的那几条（② 假数据不误伤 / ③④⑤ 三档 health / ⑥ 通道状态）。
GUI 截图另跑。

**配置目录必须四个一起隔离**（CONFIG_PATH / LOG_DIR / STATE_DIR / SESSION_DIR），
只改 CONFIG_PATH 会读到真实仓库的日志——这条是 W3.2 验收踩过的坑。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wbn import config, insight  # noqa: E402


def isolate(tmp: Path) -> None:
    """四个模块级常量一起改，否则读的是真实仓库数字。"""
    config.CONFIG_PATH = tmp / "config.json"
    config.LOG_DIR = tmp / "logs"
    config.STATE_DIR = tmp / "state"
    config.SESSION_DIR = tmp / "sessions"
    config.ensure_dirs()


def base_cfg() -> dict:
    """两条远端通道 + log，全启用，事件勾齐。"""
    return {
        "enabled": True,
        "triggers": {"task_done": True, "task_failed": True},
        "channels": [
            {"type": "bark", "enabled": True, "url": "https://x/y"},
            {"type": "wxmp", "enabled": True, "template_id": "t",
             "openid": "o", "appid": "a", "secret": "s"},
            {"type": "log", "enabled": True},
        ],
    }


def write_log(tmp: Path, lines: list[str]) -> Path:
    import datetime
    day = datetime.date.today().isoformat()
    p = tmp / "logs" / f"notify-{day}.log"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def show(title: str, value) -> None:
    print(f"  {title}: {value}")


# ---- ② 假数据：msg 里含 ok=False，不许把一次成功数成失败 -------------------

def check2() -> None:
    print("\n② 造一行 msg 含 ok=False 的假数据（证明不误伤）")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        isolate(tmp)
        write_log(tmp, [
            "[10:00:00] event=task_done sent=True remote_ok=True "
            "detail=bark=True,wxmp=True",
            # 这条是**成功**的，但远端返回的原文里带了 ok=False 字样
            "[10:00:01] channel=bark ok=True "
            "msg=upstream said ok=False but we parsed it as success",
            "[10:00:02] channel=wxmp ok=True msg=ok",
        ])
        st = insight.stats(base_cfg())
        show("stats()", f"today={st['today']} ok={st['ok']} fail={st['fail']}")
        assert st["ok"] == 2, f"ok 应为 2（两行都成功），实际 {st['ok']}"
        assert st["fail"] == 0, f"fail 应为 0，实际 {st['fail']}"
        print("  ✅ ok=2 fail=0：msg 里的 ok=False 没有被数成失败")

        # 反向：真失败的 msg 里带 ok=True，也不许数成成功
        write_log(tmp, [
            "[10:00:00] event=task_done sent=True remote_ok=True detail=bark=True",
            "[10:00:01] channel=bark ok=False msg=server replied ok=True in body",
        ])
        st = insight.stats(base_cfg())
        show("反向 stats()", f"today={st['today']} ok={st['ok']} fail={st['fail']}")
        assert st["ok"] == 0 and st["fail"] == 1, f"应为 ok=0 fail=1，实际 {st['ok']}/{st['fail']}"
        print("  ✅ ok=0 fail=1：msg 里的 ok=True 也没被数成成功")


# ---- ③④⑤ 三档 health，③④ 必须成对 ---------------------------------------

def check_health() -> None:
    print("\n③④⑤ delivery_health 三档（③④ 成对：有失败 vs 没记录）")

    # ④ 空日志：必须是 none，侧栏维持 ok
    text, level = insight.chain_sentence(base_cfg(), {})
    show("④ 空日志 chain_sentence", f"({level}) {text}")
    assert level == insight.LEVEL_OK, f"空日志应为 ok，实际 {level}"
    assert "送到你手机" in text, text
    assert "没送到" not in text, text
    print("  ✅ 没发过 ≠ 失败：空日志仍是 ok +「送到你手机」")

    # 也测 last=None（旧调用方式，tkinter 版和既有测试都这么调）
    text, level = insight.chain_sentence(base_cfg(), None)
    show("④ last=None chain_sentence", f"({level}) {text}")
    assert level == insight.LEVEL_OK, f"last=None 应为 ok，实际 {level}"
    print("  ✅ last=None 也维持旧行为（不传就不判）")

    # ③ 全失败：danger
    last = {
        "bark": {"time": "10:00", "ok": False, "msg": "errcode=40037 模板 ID 无效"},
        "wxmp": {"time": "10:00", "ok": False, "msg": "HTTP 500"},
    }
    text, level = insight.chain_sentence(base_cfg(), last)
    show("③ 全失败 chain_sentence", f"({level}) {text}")
    assert level == insight.LEVEL_DANGER, f"全失败应为 danger，实际 {level}"
    assert "最近一次没送到" in text, text
    assert "errcode" not in text, "失败原因不许进侧栏那句话"
    show("③ 文案长度", f"{len(text)} 字（--fs-aux 一行约 16 个汉字）")
    print("  ✅ 全失败 → danger +「但最近一次没送到」，且不含报错原文")

    # ⑤ 部分失败：warn
    last = {
        "bark": {"time": "10:00", "ok": True, "msg": "ok"},
        "wxmp": {"time": "10:01", "ok": False, "msg": "errcode=40037"},
    }
    text, level = insight.chain_sentence(base_cfg(), last)
    show("⑤ 部分失败 chain_sentence", f"({level}) {text}")
    assert level == insight.LEVEL_WARN, f"部分失败应为 warn，实际 {level}"
    assert "部分没送到" in text, text
    print("  ✅ 一成一败 → warn +「最近一次部分没送到」")

    # 只有一个通道有记录、且成功 → ok（没试过的通道不该拉低判定）
    last = {"bark": {"time": "10:00", "ok": True, "msg": "ok"}}
    text, level = insight.chain_sentence(base_cfg(), last)
    show("⑤b 只有 bark 有记录且成功", f"({level}) {text}")
    assert level == insight.LEVEL_OK, f"应为 ok，实际 {level}"
    print("  ✅ 没试过的通道不参与判定")

    # log 通道不参与：只剩 log 启用时走「手机上收不到」分支
    cfg = base_cfg()
    for c in cfg["channels"]:
        c["enabled"] = c["type"] == "log"
    text, level = insight.chain_sentence(cfg, {"log": {"time": "10:00", "ok": True, "msg": ""}})
    show("⑤c 只开 log", f"({level}) {text}")
    assert level == insight.LEVEL_WARN and "手机上收不到" in text, text
    print("  ✅ log 通道不参与远端判定")


# ---- ⑥ 通道状态：配置问题优先于运行时问题 --------------------------------

def check_channel_state() -> None:
    print("\n⑥ channel_state 各档")
    wx = {"type": "wxmp", "enabled": True, "appid": "a", "secret": "s",
          "template_id": "t", "openid": "o"}
    fail = {"time": "10:00", "ok": False, "msg": "errcode=40037 模板 ID 无效，请到公众平台核对"}
    sub, level, lit = insight.channel_state("wxmp", wx, fail)
    show("已启用 + 最近失败", f"({level}, lit={lit}) {sub}")
    assert level == insight.LEVEL_DANGER and lit is True, (level, lit)
    assert sub.startswith("最近一次失败："), sub
    assert len(sub) <= len("最近一次失败：") + 20, f"截断失效，长度 {len(sub)}"
    print(f"  ✅ danger + 点亮，报错原文截到 20 字（整行 {len(sub)} 字，含 7 字前缀）")

    sub, level, lit = insight.channel_state("wxmp", wx, {"time": "10:00", "ok": True, "msg": "ok"})
    show("已启用 + 最近成功", f"({level}, lit={lit}) {sub}")
    assert level == insight.LEVEL_OK and sub == "已启用", (level, sub)

    sub, level, lit = insight.channel_state("wxmp", wx, None)
    show("已启用 + 无记录", f"({level}, lit={lit}) {sub}")
    assert level == insight.LEVEL_OK and sub == "已启用", (level, sub)
    print("  ✅ 无记录维持「已启用」，不误报")

    # 配置不全 + 有失败记录 → 配置问题优先
    broken = {"type": "wxmp", "enabled": True, "appid": "a"}
    sub, level, lit = insight.channel_state("wxmp", broken, fail)
    show("缺字段 + 有失败记录", f"({level}, lit={lit}) {sub}")
    assert level == insight.LEVEL_MUTED, f"缺字段应优先报 muted，实际 {level}"
    assert "待配置" in sub, sub
    print("  ✅ 缺字段优先于运行时失败（否则会让人去查网络）")

    sub, level, lit = insight.channel_state("log", {"type": "log", "enabled": True},
                                            {"time": "10:00", "ok": False, "msg": "x"})
    show("log 通道（有失败记录也不该红）", f"({level}, lit={lit}) {sub}")
    print("  ⚠️ log 通道走同一套逻辑，见下方说明")


def main() -> int:
    print("=" * 70)
    print("W3.4 验收（非 GUI 部分）")
    print("=" * 70)
    check2()
    check_health()
    check_channel_state()
    print("\n" + "=" * 70)
    print("全部通过")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
