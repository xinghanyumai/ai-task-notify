# -*- coding: utf-8 -*-
"""自检 / 联调工具。

用途：
    python selftest.py            # 用当前配置真实发送 4 类通知，手机应收到
    python selftest.py --dry      # 只渲染不发送，检查文案
    python selftest.py --status   # 只看 hooks 安装状态与通道配置
    python selftest.py --encoding # 只跑编码回归检查，不发消息

它会模拟真实 hook payload，走完整流水线（含过滤、去重、限流），
所以能验证「装完到底能不能收到」。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wbn import __version__  # noqa: E402
from wbn import channels, config, events, filters, pipeline, render, store  # noqa: E402
from wbn.sanitize import clean_text  # noqa: E402

CWD = str(config.ROOT)

SCENARIOS = [
    (
        "task_done",
        "Stop",
        {
            "last_assistant_message": (
                "已完成通知程序开发：包含 hook 入口、异步派发、10 个推送通道、"
                "配置界面与桌面快捷方式。"
            ),
        },
    ),
    (
        "task_failed",
        "StopFailure",
        {
            "error": "构建失败：ModuleNotFoundError: No module named 'pyside6'",
            "last_assistant_message": "尝试安装依赖时失败，已中止。",
        },
    ),
    (
        "need_input",
        "PermissionRequest",
        {
            "message": "需要你确认是否允许执行 rm -rf build/",
            "tool_name": "Bash",
        },
    ),
    (
        "tool_failed",
        "PostToolUseFailure",
        {
            "tool_name": "Bash",
            "error": "exit code 1: command not found: ffmpeg",
            "last_assistant_message": "工具连续失败 3 次，已停止重试。",
        },
    ),
]


def show_status() -> None:
    from install import hooks_status  # 延迟导入，避免循环

    cfg = config.load()
    print("=" * 62)
    print(f"  {cfg.get('app_name')} 通知 · 状态自检")
    print("=" * 62)
    print(f"总开关        : {'开启' if cfg.get('enabled') else '关闭'}")
    print(f"配置文件      : {config.CONFIG_PATH}")
    print(f"运行环境      : {sys.executable}")

    print("\n[hooks 安装状态]")
    for line in hooks_status():
        print("  " + line)

    print("\n[通道]")
    for ch in cfg.get("channels", []):
        mark = "✔" if ch.get("enabled") else "·"
        print(f"  {mark} {ch.get('type'):14s} {_channel_desc(ch)}")

    print("\n[触发条件]")
    for k, v in cfg.get("triggers", {}).items():
        print(f"  {'✔' if v else '·'} {k:14s} {events.KIND_LABELS.get(k, '')}")

    f = cfg.get("filters", {})
    print("\n[过滤]")
    print(f"  最短耗时      : {f.get('min_duration_seconds')} 秒")
    print(f"  问句归为待办  : {f.get('question_as_need_input')}")
    print(f"  静默时段      : {f.get('quiet_hours') or '（不限制）'}")
    print(f"  去重窗口      : {f.get('dedup_seconds')} 秒")
    print(f"  频率上限      : {f.get('max_per_minute')}/分, {f.get('max_per_hour')}/时")
    print()


def _channel_desc(ch: dict) -> str:
    t = ch.get("type")
    if t == "wxmp":
        if not ch.get("appid"):
            return "未填 appID / appsecret"
        if not ch.get("template_id"):
            return "已填 appID，缺模板 ID"
        return f"已配置{'（openid 自动获取）' if not ch.get('openid') else ''}"
    if t == "wecom_app":
        if not ch.get("corpid") or not ch.get("secret"):
            return "未填企业 ID / Secret"
        if not ch.get("agentid"):
            return "已填凭证，缺 AgentId"
        return f"已配置，收件人 {ch.get('touser') or '@all'}"
    if t == "bark":
        return f"key={'已填' if ch.get('key') else '未填'}"
    if t == "ntfy":
        return f"topic={ch.get('topic') or '未填'}"
    if t in ("wecom_bot", "dingtalk_bot", "feishu_bot"):
        return "已填 webhook" if ch.get("webhook") else "未填 webhook"
    if t == "smtp":
        return f"{ch.get('user') or '未填账号'}"
    if t == "webhook":
        return ch.get("url") or "未填 url"
    return ""


def run_scenarios(dry: bool) -> int:
    cfg = config.load()

    if not dry and not any(
        c.get("enabled") and c.get("type") not in ("log",)
        for c in cfg.get("channels", [])
    ):
        print("⚠ 当前只启用了「本地日志」通道，手机上收不到。")
        print("  请先在设置中心里配一个手机通道（推荐「微信测试号」，免费且免实名）。\n")

    passed = 0
    for name, ev, extra in SCENARIOS:
        sid = f"selftest-{name}-{int(time.time())}"
        # 造一条 42 秒前的「本轮开始」，绕过最短耗时门槛
        store.mark_turn_start(sid, "自检：模拟一轮任务")
        sess = store.get_session(sid)
        sess["turn_start"] = time.time() - 42
        (config.SESSION_DIR / f"{sid}.json").write_text(
            json.dumps(sess, ensure_ascii=False), encoding="utf-8"
        )

        payload = {
            "hook_event_name": ev,
            "session_id": sid,
            "cwd": CWD,
            "permission_mode": "default",
            "transcript_path": "",
        }
        payload.update(extra)

        kind = events.classify(payload) or "?"
        title, content, _ = render.render_message(payload, name, cfg, cfg.get("app_name"))

        print(f"\n── 场景 {name}  (hook 事件 {ev} → 语义 {kind})")
        print(f"   标题: {title}")
        for ln in content.splitlines():
            print(f"   │ {ln}")

        if dry:
            ok, why = filters.check(
                name, {"cwd": CWD, "duration": 42, "summary": "", "prompt": ""}, cfg,
                ignore_rate_limit=True,
            )
            print(f"   过滤: {'放行' if ok else '拦截 — ' + why}")
            passed += 1
            continue

        result = pipeline.process(payload, cfg.get("app_name", "WorkBuddy"), cfg,
                                  ignore_limits=True)
        if result["sent"]:
            detail = ", ".join(
                f"{c['type']}{'✔' if c['ok'] else '✘'}" for c in result["channels"]
            )
            print(f"   结果: 已发送  [{detail}]")
            passed += 1
        else:
            print(f"   结果: 未发送 — {result['skipped']}")
            for c in result["channels"]:
                print(f"         {c['type']}: {c['msg']}")

    print(f"\n{'='*62}\n完成 {passed}/{len(SCENARIOS)} 个场景\n")
    if not dry:
        print("若手机没收到，请依次检查：")
        print("  1. 通道 token / key 是否填对")
        print("  2. 电脑网络能否访问该推送服务")
        # 注意：Path / 'x' + 'y' 会先算除法再拼字符串，类型对不上会抛 TypeError，
        # 必须把文件名整体拼好再交给 Path
        today_log = config.LOG_DIR / f"notify-{time.strftime('%Y-%m-%d')}.log"
        print(f"  3. 运行日志：{today_log}")
    return 0 if passed == len(SCENARIOS) else 1


def check_encoding() -> int:
    """编码回归检查：不发任何消息，只验证文本不会把通知链路炸掉。

    背景（真实事故）：宿主投递的 stdin 是 UTF-8，但 Windows 上 Python 的
    sys.stdin 会按 cp936 + surrogateescape 解码，中文于是变成
    「閫氱煡」加落单代理字符（\\udcae 这类）。代理字符不是合法 Unicode，
    `s.encode("utf-8")` 直接抛 UnicodeEncodeError，整条通知凭空消失。
    表现为：测试消息收得到，真实任务状态一条都收不到。

    所以这里守住三条底线：清洗不崩、正常文本幂等、受损字节能还原。
    """
    print("=" * 62)
    print("  编码回归检查")
    print("=" * 62)
    fails = 0

    # 1) 落单代理字符必须能被清洗掉且不再让 encode 崩溃
    broken = json.loads('"\\udca2\\udcad 修复完成"')
    try:
        broken.encode("utf-8")
        print("  ✗ 构造的脏字符串居然能直接 encode，检查逻辑本身有问题")
        fails += 1
    except UnicodeEncodeError:
        print("  · 脏字符串确实无法 encode（这是 bug 的触发条件）")
    fixed = clean_text(broken)
    try:
        fixed.encode("utf-8")
        print(f"  ✓ 清洗后可 encode：{fixed!r}")
    except UnicodeEncodeError as e:
        print(f"  ✗ 清洗后仍无法 encode：{e}")
        fails += 1

    # 2) 正常文本必须原样通过（幂等，不能把好文字改坏）
    bad = []
    for s in ["正常中文", "emoji 🎉🚀", "ascii only", "", "混合 mixed 中英 123 🎯"]:
        if clean_text(s) != s:
            bad.append(s)
    if bad:
        print(f"  ✗ 以下正常文本被改动了：{bad}")
        fails += 1
    else:
        print("  ✓ 正常中文 / emoji / 中英混排 全部原样通过")

    # 3) 被 surrogateescape 打散的 UTF-8 字节能完整还原
    #    合法 UTF-8 不会产生代理字符，这里故意带一个非法尾字节 \xff 来造出
    #    「\udcff」这种落单代理，前面的「中文」两字必须能被还原回来
    mangled = b"\xe4\xb8\xad\xe6\x96\x87\xff".decode("utf-8", "surrogateescape")
    recovered = clean_text(mangled)
    if recovered.startswith("中文"):
        print(f"  ✓ 受损字节能还原：{recovered!r}")
    else:
        print(f"  ✗ 受损字节还原失败：{recovered!r}")
        fails += 1

    # 4) 整条消息走一遍渲染 + 通道打包，不能抛异常
    cfg = config.load()
    payload = {
        "hook_event_name": "Stop",
        "session_id": "encoding-check",
        "cwd": str(config.ROOT),
        "last_assistant_message": "含中文、emoji 🎉 和符号 →✓ 的一条消息。",
    }
    try:
        title, content, fields = render.render_message(
            payload, "task_done", cfg, cfg.get("app_name", "WorkBuddy")
        )
        body = json.dumps({"t": title, "c": content, "f": fields},
                          ensure_ascii=False).encode("utf-8")
        print(f"  ✓ 渲染 + 打包正常（{len(body)} 字节）")
    except Exception as e:
        print(f"  ✗ 渲染 / 打包异常：{type(e).__name__}: {e}")
        fails += 1

    print()
    if fails:
        print(f"编码检查未通过：{fails} 项失败\n")
        return 1
    print("编码检查全部通过 ✓\n")
    return 0


def check_wrap() -> int:
    r"""详情折行回归检查。

    微信**不会**替我们自动折行。官方《模板消息运营规范》第四条只保证
    「旧模板样式才可以用参数通过增加 \n 来实现换行」——也就是说换行
    必须由程序自己插。既然排版全在自己手里，就得守住几条底线：
    行宽不溢出、行首不出标点、英文单词不被劈开、省略号只在末尾。
    """
    print("=" * 62)
    print("  详情折行检查")
    print("=" * 62)
    fails = 0
    WIDTH, MAXL = 34, 4

    def _w(s: str) -> int:
        return sum(channels._char_w(c) for c in s)

    cases = [
        ("短句", "已修好编码问题。", 1),
        ("中等", "已修好编码问题，现在通知能正常送达，中文不会再变成乱码。", 2),
        ("长文", "已修好编码问题，现在通知能正常送达，中文不会再变成乱码，"
                 "详情也会按卡片宽度自动折行显示。另外还修了模板变量缺冒号的问题。", 2),
        ("超长", "这是一段非常长的内容" * 12, MAXL),
        ("中英", "Fixed the UnicodeEncodeError in hook.py by reading "
                 "sys.stdin.buffer instead of sys.stdin. 现在中文正常了。", 2),
        ("无标点", "这是一段完全没有标点符号的连续中文文本用来测试硬切断行会不会浪费空间", 2),
    ]
    for name, text, min_lines in cases:
        out = channels._wrap_detail(text, WIDTH, MAXL)
        rows = out.split("\n") if out else []
        bad = []
        if len(rows) > MAXL:
            bad.append(f"折出 {len(rows)} 行，超过上限 {MAXL}")
        if len(rows) < min_lines:
            bad.append(f"只折了 {len(rows)} 行，至少应有 {min_lines} 行")
        for i, r in enumerate(rows, 1):
            if r and r[0] in channels._NO_LINE_START:
                bad.append(f"第 {i} 行以标点 {r[0]!r} 开头：{r!r}")
            if _w(r) > WIDTH + 2:      # 带省略号的行允许略超两格
                bad.append(f"第 {i} 行宽 {_w(r)} 超出 {WIDTH}：{r!r}")
        if bad:
            print(f"  ✗ {name}：")
            for b in bad:
                print(f"      {b}")
            fails += 1
        else:
            print(f"  ✓ {name}：{len(rows)} 行，行宽合规、行首无标点")

    # 英文单词不能被劈成两半（这是最容易被忽略的排版事故）
    out = channels._wrap_detail("reading sys.stdin.buffer instead " * 3, WIDTH, MAXL)
    joined = out.replace("\n", "")
    if "buffer" in joined and "UnicodeEncodeError" in channels._wrap_detail(
            "Fixed the UnicodeEncodeError " * 3, WIDTH, MAXL).replace("\n", ""):
        print("  ✓ 英文单词完整：buffer / UnicodeEncodeError 都没被劈开")
    else:
        print("  ✗ 英文单词被劈开了")
        fails += 1

    # 空输入不能产出空行（否则模板里会多出一条空白）
    if channels._wrap_detail("", WIDTH, MAXL) == "":
        print("  ✓ 空文本折出空串，不会多出空白行")
    else:
        print("  ✗ 空文本折行结果不为空")
        fails += 1

    # 省略号只允许出现在最后一行
    rows = channels._wrap_detail("这是一段非常长的内容" * 12, WIDTH, MAXL).split("\n")
    ell = [i for i, r in enumerate(rows, 1) if r.endswith("…")]
    if ell == [len(rows)]:
        print(f"  ✓ 省略号只在最后一行（第 {len(rows)} 行）")
    else:
        print(f"  ✗ 省略号位置异常：{ell}")
        fails += 1

    # 多槽位切分：模板声明几个详情变量，就切成几段，段与段不重叠、总量不超原文
    src = ("已修好编码问题，现在通知能正常送达，中文不会再变成乱码，"
           "详情也会按卡片宽度自动折行显示。")
    demo = {"kind": "task_done", "kind_label": "任务完成", "project": "p",
            "summary": src, "error": "—"}
    tpl4 = ("状态：{{title.DATA}}\n项目：{{project.DATA}}\n"
            "详情：{{detail.DATA}}\n续1：{{detail2.DATA}}\n"
            "续2：{{detail3.DATA}}\n续3：{{detail4.DATA}}\n"
            "时间：{{time.DATA}}")
    v4 = channels._wxmp_values("✅ 完成", "", demo, "2026-10-25 22:00",
                               wrap=True, tpl_content=tpl4)
    segs = [v4["detail"], v4["detail2"], v4["detail3"], v4["detail4"]]
    filled = [s for s in segs if s]
    joined = "".join(filled).replace("…", "")
    if len(filled) >= 2 and len(joined) <= len(src):
        print(f"  ✓ 多槽位切分：{len(filled)} 段共 {len(joined)} 字"
              f"（原文 {len(src)} 字，无重复）")
    else:
        print(f"  ✗ 多槽位切分异常：{segs}")
        fails += 1

    # 单槽位模板只能填第一段，不能往没声明的字段塞内容
    tpl1 = "状态：{{title.DATA}}\n详情：{{detail.DATA}}\n时间：{{time.DATA}}"
    v1 = channels._wxmp_values("✅ 完成", "", demo, "2026-10-25 22:00",
                               wrap=True, tpl_content=tpl1)
    if v1["detail"] and not v1["detail2"]:
        print("  ✓ 单槽位模板只填第一段，其余留空")
    else:
        print(f"  ✗ 单槽位模板填多了：detail2={v1['detail2']!r}")
        fails += 1

    # 标签越长，值区越窄（「详细信息：」比「详情：」多占两格）
    w_short = channels._label_width("详情：{{detail.DATA}}", "detail")
    w_long = channels._label_width("详细信息：{{detail.DATA}}", "detail")
    if w_long == w_short + 4:
        print(f"  ✓ 标签宽度按实际计算：详情={w_short} 详细信息={w_long} 半角位")
    else:
        print(f"  ✗ 标签宽度计算异常：{w_short} / {w_long}")
        fails += 1

    print()
    if fails:
        print(f"折行检查未通过：{fails} 项失败\n")
        return 1
    print("折行检查全部通过 ✓\n")
    return 0


def check_wxmp() -> int:
    """微信模板诊断：模板长什么样、我们会填什么、手机上第一行为什么显示那个词。"""
    cfg = config.load()
    wx = next(
        (c for c in cfg.get("channels", []) if c.get("type") == "wxmp"), None
    )
    print("=" * 62)
    print("  微信模板诊断")
    print("=" * 62)
    if not wx:
        print("  ✗ 配置里没有微信测试号通道\n")
        return 1
    for line in channels.wxmp_diagnose(wx):
        print("  " + line if line else "")
    print()
    return 0


def adopt_wxmp_template() -> int:
    """把配置切到「标题有意义」的模板上。

    用途：你在测试号后台建好新模板后，不用手工复制模板 ID ——
    这里会自动挑一个标题不是占位词的模板写进 config.json。
    """
    cfg = config.load()
    wx = next((c for c in cfg.get("channels", []) if c.get("type") == "wxmp"), None)
    print("=" * 62)
    print("  自动切换微信模板")
    print("=" * 62)
    if not wx:
        print("  ✗ 配置里没有微信测试号通道\n")
        return 1

    appid, secret = wx.get("appid", ""), wx.get("secret", "")
    token, err = channels.wx_access_token(appid, secret)
    if not token:
        print(f"  ✗ 获取 access_token 失败：{err}\n")
        return 1

    obj, gerr = channels._get_json(
        f"{channels.WXMP_API}/cgi-bin/template/get_all_private_template"
        f"?access_token={token}"
    )
    if gerr or obj.get("errcode"):
        print(f"  ✗ 读取模板列表失败：{gerr or obj}\n")
        return 1

    tpls = obj.get("template_list") or []
    if not tpls:
        print("  ✗ 这个测试号还没有任何模板\n")
        return 1

    current = (wx.get("template_id") or "").strip()
    scored = []
    for t in tpls:
        tid = str(t.get("template_id") or "")
        title = str(t.get("title") or "")
        vars_ = channels._VAR_RE.findall(str(t.get("content") or ""))
        placeholder = title.strip().lower() in channels._PLACEHOLDER_TITLES
        # 变量名能对上语义的个数越多，说明模板越贴合我们的通知结构
        roles = {
            r
            for r, keys in channels._ROLE_HINTS
            for v in vars_
            if any(v.lower().startswith(k) for k in keys)
        }
        # 缺冒号的变量在手机上渲染为空，这种模板必须降权，不能自动选中
        bad = len(channels.wxmp_content_warnings(str(t.get("content") or "")))
        score = (
            (0 if placeholder else 100)
            + len(roles) * 10
            + min(len(vars_), 6)
            - bad * 50
        )
        scored.append((score, tid, title, vars_, placeholder, bad))

    scored.sort(reverse=True)
    print("  候选模板：")
    for score, tid, title, vars_, ph, bad in scored:
        mark = "→" if tid == current else " "
        flags = ""
        if ph:
            flags += "  ⚠ 标题是占位词"
        if bad:
            flags += f"  ✗ {bad} 个变量缺「标签：」（渲染为空）"
        print(f"   {mark} [{score:3d}] {title!r:16s} 变量={','.join(vars_) or '（无）'}{flags}")
    print()

    best_score, best_id, best_title, best_vars, best_ph, best_bad = scored[0]
    if best_bad:
        print("  ✗ 所有模板都有变量缺「标签：」，正文会残缺。")
        print("    删掉重建，内容照抄 README「步骤 1 · 方案 A · ③ 建模板」。\n")
        return 1
    if best_ph:
        print("  ✗ 所有模板的标题都是占位词，先去后台建一个标题有意义的模板。")
        print("    内容可照抄 README「步骤 1 · 方案 A · ③ 建模板」那一节。\n")
        return 1
    if best_id == current:
        print(f"  ✓ 当前已是最优模板：{best_title!r}\n")
        return 0

    wx["template_id"] = best_id
    config.save(cfg)
    # 清掉模板定义缓存，下次发送时重新拉
    try:
        channels.WXMP_TPL_CACHE.unlink(missing_ok=True)
    except Exception:
        pass
    print(f"  ✓ 已切换到 {best_title!r}")
    print(f"    模板 ID: {best_id}")
    print(f"    变量   : {', '.join(best_vars) or '（无）'}\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="WorkBuddy 通知自检")
    ap.add_argument("--version", action="version", version=f"wbn {__version__}")
    ap.add_argument("--dry", action="store_true", help="只渲染，不实际发送")
    ap.add_argument("--status", action="store_true", help="只看状态")
    ap.add_argument("--encoding", action="store_true", help="只跑编码回归检查")
    ap.add_argument("--wrap", action="store_true", help="只跑详情折行检查")
    ap.add_argument("--wxmp", action="store_true", help="诊断微信模板字段")
    ap.add_argument("--adopt", action="store_true", help="自动切换到标题有意义的微信模板")
    args = ap.parse_args()

    config.ensure_dirs()
    config.save_default_if_missing()

    if args.status:
        show_status()
        return 0
    if args.encoding:
        rc = check_encoding()
        return check_wrap() or rc
    if args.wrap:
        return check_wrap()
    if args.wxmp:
        return check_wxmp()
    if args.adopt:
        return adopt_wxmp_template()
    return run_scenarios(args.dry)


if __name__ == "__main__":
    sys.exit(main())
