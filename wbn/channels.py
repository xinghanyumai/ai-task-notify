"""推送通道实现。

统一接口：
    send_one(channel, title, content, fields, timeout) -> (ok, message)

所有通道只用 urllib / smtplib 标准库，不引入 requests，
这样 hook 进程启动开销极小（≈60ms），不会拖慢 agent。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from . import config, store
from .sanitize import check_http_url, clean_obj, clean_text, one_line, redact

UA = "wbn-notifier/1.0 (+personal-use)"

# ---------------------------------------------------------------- 超时口径
#
# 一次「微信测试号」发送最多打两次网络请求：先拿 access_token（GET），
# 再发消息（POST）。两个超时是**串联**的，所以后端最坏耗时是两者之和。
#
# **改这两个值要同步改前端的兜底超时**：`webui/assets/pages/channels.js`
# 的 TEST_TIMEOUT 由 `(TOKEN_TIMEOUT + http_timeout + 2) * 1000` 派生，
# 常量通过 `bridge.channels_meta()["timeouts"]` 下发，前端不自己猜。
# 前端比后端短 = 后端还在跑、前端已经报「等待超时」，然后真实结果被
# `_push()` 静默丢弃（W3.1 §2.1 那个反向说谎的 bug）。
TOKEN_TIMEOUT = 10   # 取 access_token / 换 openid 这类 GET
SEND_TIMEOUT = 8     # 发消息的默认值，实际由 config.advanced.http_timeout 覆盖


# ---------------------------------------------------------------- 元数据
# 供设置中心自动生成表单：(字段名, 标签, 控件类型, 默认值, 提示)
# 控件类型：text / password / bool / int / textarea

CHANNEL_META: dict[str, dict] = {
    "wxmp": {
        "label": "微信测试号（自建，推荐）",
        "summary": "官方免费、无需实名、不限条数，消息直接进微信聊天列表",
        "help": (
            "用的是微信官方「公众号模板消息」接口，个人可申请，完全免费：\n"
            "\n"
            "1) 电脑打开 https://mp.weixin.qq.com/debug/cgi-bin/sandbox?t=sandbox/login\n"
            "   微信扫码登录，页面会给出 appID 和 appsecret，填到下面\n"
            "2) 用同一个微信扫页面上的「测试号二维码」关注它\n"
            "3) 页面下半部分「模板消息接口」→ 点「新增测试模板」，标题随便写，\n"
            "   内容粘贴下面这段，保存后得到模板 ID：\n"
            "\n"
            "   状态：{{title.DATA}}\n"
            "   项目：{{project.DATA}}\n"
            "   详情：{{detail.DATA}}\n"
            "   时间：{{time.DATA}}\n"
            "\n"
            "   ⚠ 每个变量前面必须带「标签：」，否则那一行会渲染成空白。\n"
            "   想多留一行放详情，可以再加一行「补充：{{detail2.DATA}}」。\n"
            "\n"
            "4) openid 可以留空，程序会自动从关注者列表里取；也可以点右侧「自动获取」\n"
            "\n"
            "模板消息会以公众号消息的形式直接出现在微信聊天列表里，"
            "手机锁屏也能弹通知，点开就是完整内容。"
        ),
        "fields": [
            ("appid", "appID", "text", "", "测试号页面上的 appID"),
            ("secret", "appsecret", "password", "", "测试号页面上的 appsecret"),
            ("template_id", "模板 ID", "text", "", "新增测试模板后得到"),
            ("openid", "openid", "text", "", "留空自动获取关注者"),
            ("wrap", "详情自动折行", "bool", "1", "模板里有几个详情变量就显示几行"),
            # url 放最后：它是长链接，独占一整行才够宽
            ("url", "点击跳转链接", "text", "", "可选，留空则点击不跳转"),
        ],
    },
    "wecom_app": {
        "label": "企业微信自建应用",
        "summary": "markdown 自由排版、不限条数，比群机器人更灵活",
        "help": (
            "1) 打开 https://work.weixin.qq.com/ 注册（个人可选「团队」，微信扫码即可，无需营业执照）\n"
            "2) 管理后台 → 我的企业 → 复制「企业 ID」\n"
            "3) 应用管理 → 自建 → 创建应用 → 记下 AgentId 和 Secret\n"
            "4) 应用详情页 → 「可见范围」把自己加进去\n"
            "5) 手机装企业微信 App 扫码登录，就能收到消息\n"
            "\n"
            "收件人填 @all 即可（发给应用可见范围内所有人，个人团队就你自己）。\n"
            "想直接在微信里收：企业微信后台 → 我的企业 → 微信插件 → 扫码关注。"
        ),
        "fields": [
            ("corpid", "企业 ID", "text", "", "我的企业 → 企业 ID"),
            ("secret", "应用 Secret", "password", "", "应用详情页"),
            ("agentid", "AgentId", "text", "", "应用详情页，纯数字"),
            ("touser", "收件人", "text", "@all", "@all 或成员 UserID"),
        ],
    },
    "bark": {
        "label": "Bark（iOS 原生推送）",
        "summary": "完全免费、无需注册，iPhone 首选",
        "help": (
            "1) App Store 搜索 Bark 安装\n"
            "2) 打开 App，首页那串 https://api.day.app/XXXXXXXX 里的 XXXX 就是 key\n"
            "3) 自建服务器的话把 server 改成自己的地址"
        ),
        "fields": [
            ("key", "设备 Key", "password", "", "必填"),
            ("server", "服务器", "text", "https://api.day.app", ""),
            ("group", "分组", "text", "AI 任务", "通知列表里的分组名"),
            ("sound", "铃声", "text", "bell", "如 bell / alarm / none"),
            ("level", "中断级别", "text", "active", "active / timeSensitive / critical"),
            ("icon", "图标 URL", "text", "", "可选"),
        ],
    },
    "ntfy": {
        "label": "ntfy（跨平台，可自建）",
        "summary": "零注册，装 App 订阅一个 topic 即可；安卓/桌面端都好用",
        "help": (
            "1) 手机装 ntfy App（或浏览器打开 https://ntfy.sh）\n"
            "2) 起一个别人猜不到的 topic 名，填到下面\n"
            "3) App 里订阅同名 topic\n"
            "国内访问公共服务器可能偏慢，可自建后改 server。"
        ),
        "fields": [
            ("topic", "Topic", "text", "", "必填，建议用随机长字符串"),
            ("server", "服务器", "text", "https://ntfy.sh", ""),
            ("priority", "优先级", "int", "3", "1~5，3 为默认"),
            ("tags", "标签", "text", "robot", "逗号分隔，可留空"),
        ],
    },
    "wecom_bot": {
        "label": "企业微信群机器人",
        "summary": "有企业微信的话最稳，消息进群不打扰个人微信",
        "help": (
            "企业微信群 → 右上角「…」→ 群机器人 → 添加 → 复制 Webhook 地址。\n"
            "个人可在 https://work.weixin.qq.com/ 免费注册一个企业自用。"
        ),
        "fields": [("webhook", "Webhook 地址", "password", "", "必填")],
    },
    "dingtalk_bot": {
        "label": "钉钉群机器人",
        "summary": "钉钉群自定义机器人，支持加签",
        "help": "群设置 → 智能群助手 → 添加机器人 → 自定义 → 复制 Webhook；若开了加签，把密钥填到 secret。",
        "fields": [
            ("webhook", "Webhook 地址", "password", "", "必填"),
            ("secret", "加签密钥", "password", "", "可选"),
        ],
    },
    "feishu_bot": {
        "label": "飞书群机器人",
        "summary": "飞书群自定义机器人 Webhook",
        "help": "群设置 → 群机器人 → 添加自定义机器人 → 复制 Webhook 地址。",
        "fields": [("webhook", "Webhook 地址", "password", "", "必填")],
    },
    "smtp": {
        "label": "邮件（SMTP）",
        "summary": "推到邮箱，手机邮件 App 会弹通知；QQ/163 需用授权码",
        "help": (
            "以 QQ 邮箱为例：设置 → 账户 → 开启 SMTP 服务 → 生成授权码，"
            "把授权码当密码填进来。\n服务器 smtp.qq.com / 端口 465 / 勾选 SSL。"
        ),
        "fields": [
            ("host", "SMTP 服务器", "text", "smtp.qq.com", "必填"),
            ("port", "端口", "int", "465", "465(SSL) 或 587(STARTTLS)"),
            ("ssl", "使用 SSL", "bool", "true", ""),
            ("user", "账号", "text", "", "完整邮箱地址"),
            ("password", "密码/授权码", "password", "", "必填"),
            ("to", "收件人", "text", "", "多个用逗号分隔，留空则发给自己"),
        ],
    },
    "webhook": {
        "label": "自定义 Webhook",
        "summary": "推给你自己的服务，或其他支持 HTTP 的通道",
        "help": (
            "默认 POST JSON：{\"title\":..,\"content\":..,\"kind\":..,\"project\":..}\n"
            "如需自定义请求体，在 body_template 里写模板，可用 {title} {content} 等占位符。"
        ),
        "fields": [
            ("url", "URL", "password", "", "必填"),
            ("method", "方法", "text", "POST", "POST / GET"),
            ("body_template", "自定义请求体", "textarea", "", "留空用默认 JSON"),
        ],
    },
    "log": {
        "label": "写入本地日志",
        "summary": "始终建议开着，方便回溯和排查",
        "help": "记录在 logs/notifications.log。",
        "fields": [],
    },
}


# 每个通道的列表图标。单独一张表，改图标不用动上面的元数据。
_CHANNEL_ICONS = {
    "wxmp": "chat",
    "wecom_app": "briefcase",
    "bark": "phone",
    "ntfy": "bolt",
    "wecom_bot": "chat",
    "dingtalk_bot": "chat",
    "feishu_bot": "chat",
    "smtp": "mail",
    "webhook": "link",
    "log": "file",
}
for _t, _ic in _CHANNEL_ICONS.items():
    if _t in CHANNEL_META:
        CHANNEL_META[_t]["icon"] = _ic


# 通道页的分组。放在这里而不是界面层：它是**通道元数据**，不是布局。
# 当初留在 tkinter 版的 ui.py 里的话，webview 版要 import 那个 6000 行的文件
# 才能拿到一份常量——W5 已经把那个文件删了，这个理由现在更硬。
# 分组顺序是「自建 → 第三方 → 本机」，越靠前越推荐。
CHANNEL_GROUPS: list[tuple[str, list[str]]] = [
    ("自建推送", ["wxmp", "wecom_app"]),
    ("第三方", ["bark", "ntfy", "wecom_bot", "dingtalk_bot",
                "feishu_bot", "smtp", "webhook"]),
    ("本机", ["log"]),
]

# 「本机落盘」的通道：写盘成功**不代表通知发出去了**（D14）。
# 这是通道注册表的属性，不是某个消费者的判断——统计口径（`logs`）和
# 「有没有远端通道」（`has_remote_channel`）都从这里取，别各自写一份。
LOCAL_CHANNELS: frozenset[str] = frozenset({"log"})


# ---------------------------------------------------------------- 完整性判定
# 「填齐了才算配好」的必填字段。openid 不在里面——它会自动从关注者列表拉取。
REQUIRED: dict[str, tuple[str, ...]] = {
    "wxmp": ("appid", "secret", "template_id"),
    "wecom_app": ("corpid", "secret", "agentid"),
    "bark": ("key",),
    "ntfy": ("topic",),
    "wecom_bot": ("webhook",),
    "dingtalk_bot": ("webhook",),
    "feishu_bot": ("webhook",),
    "smtp": ("host", "user", "password"),
    "webhook": ("url",),
    "log": (),
}

# 通道 → 字段 key → 中文名，用于「还差什么」的提示。
# 必须按通道分开存：`secret` 在 wecom_app 是「应用 Secret」，
# 在 dingtalk_bot 是「加签密钥」，拍平成一个字典会串味。
FIELD_LABELS: dict[str, dict[str, str]] = {
    ctype: {key: label for key, label, *_ in (meta.get("fields") or [])}
    for ctype, meta in CHANNEL_META.items()
}


def missing_fields(ctype: str, ch: dict | None) -> list[str]:
    """返回该通道还没填的必填字段（中文名）。空列表表示配好了。"""
    ch = ch or {}
    labels = FIELD_LABELS.get(ctype, {})
    return [labels.get(key, key) for key in missing_keys(ctype, ch)]


def missing_keys(ctype: str, ch: dict | None) -> list[str]:
    """同上，但返回字段 key，方便界面定位到具体输入框。"""
    ch = ch or {}
    return [key for key in REQUIRED.get(ctype, ())
            if not str(ch.get(key) or "").strip()]


def is_ready(ctype: str, ch: dict | None) -> bool:
    """凭证是否填齐。填齐才允许启用，否则开着也只会刷失败日志。"""
    return not missing_fields(ctype, ch)


def short_label(ctype: str) -> str:
    """去掉括号补充说明的短名字，列表里用。"""
    return (CHANNEL_META.get(ctype, {}).get("label") or ctype).split("（")[0]


def auto_enable(cfg: dict) -> tuple[list[str], list[str]]:
    """按凭证完整度自动维护启用状态。

    最常见的「配了却收不到」就是：appid / secret 都填好了，
    但忘了拨那个开关。所以这里不再让用户记这件事：

    - 凭证齐全、且用户没手动关过 → 自动打开
    - 凭证不全 → 强制关闭（开着也发不出去，只会刷一屏失败日志）

    「手动关过」记在 `manual_off` 上：用户明确关掉某个通道后，
    自动逻辑不再去动它，否则会变成「关了又自己开」，很吓人。

    返回 (被自动打开的通道, 被自动关闭的通道)，供界面提示用。
    """
    turned_on: list[str] = []
    turned_off: list[str] = []
    for ch in cfg.get("channels", []):
        ctype = ch.get("type")
        if ctype not in CHANNEL_META:
            continue
        ready = is_ready(ctype, ch)
        if ready:
            if not ch.get("enabled") and not ch.get("manual_off"):
                ch["enabled"] = True
                turned_on.append(short_label(ctype))
        else:
            if ch.get("enabled"):
                ch["enabled"] = False
                turned_off.append(short_label(ctype))
            ch.pop("manual_off", None)
    return turned_on, turned_off


# ---------------------------------------------------------------- 工具


def _http(
    url: str,
    data: bytes | None = None,
    headers: dict | None = None,
    method: str = "POST",
    timeout: int = SEND_TIMEOUT,
) -> tuple[int, str]:
    """发一个 HTTP 请求。返回 (状态码, 响应文本)。状态码 0 表示请求本身没发出去。

    两道防护：
    - 只允许 http/https。urllib 默认也认 file://，不拦住的话一个配错的
      URL 就能把本地文件读出来，再顺着错误信息写进日志。
    - 所有返回文本都过一遍脱敏。异常消息经常回显完整 URL，
      而 webhook 地址里就藏着 key。
    """
    bad = check_http_url(url)
    if bad:
        return 0, bad

    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    try:
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status or 0, resp.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(2048).decode("utf-8", "replace")
        except Exception:
            pass
        return e.code, redact(body, 300)
    except Exception as e:  # 网络不可达 / DNS / 超时 / URL 非法
        return 0, redact(f"{type(e).__name__}: {e}", 300)


def _post_json(url: str, body: dict, timeout: int = SEND_TIMEOUT) -> tuple[int, str]:
    return _http(
        url,
        json.dumps(body, ensure_ascii=False).encode("utf-8"),
        {"Content-Type": "application/json; charset=utf-8"},
        "POST",
        timeout,
    )


def _err(code: int, text: str) -> str:
    """统一的失败描述。

    code == 0 表示请求根本没发出去（DNS / 超时 / URL 非法），
    这时 text 本身就是原因，前面再挂个「HTTP 0」只会误导。
    """
    body = redact(text, 160)
    return body if code == 0 else f"HTTP {code}: {body}"


def _truncate_bytes(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[: limit - 20].decode("utf-8", "ignore") + "\n…（已截断）"


def plain_text(title: str, content: str) -> str:
    """把 markdown 粗体符号去掉，给纯文本通道用。

    标题为空时不能直接拼，否则正文前面会多出两个空行（微信里看就是
    消息开头空了两行）。
    """
    parts = [p for p in (title, content) if p and p.strip()]
    text = "\n\n".join(parts)
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


# ---------------------------------------------------------------- 凭证缓存
# 微信 / 企业微信的 access_token 有效期 7200 秒，且每天有获取次数上限，
# 必须缓存复用，否则高频通知会把配额打满（errcode 45009）。

TOKEN_CACHE = config.STATE_DIR / "token_cache.json"


def _load_tokens() -> dict:
    try:
        obj = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _save_tokens(data: dict) -> None:
    try:
        config.ensure_dirs()
        TOKEN_CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        config.harden_file(TOKEN_CACHE)   # 缓存里是 access_token，同样收紧权限
    except Exception:
        pass


def _cached_token(key: str) -> str:
    """取缓存 token；剩余有效期不足 5 分钟就当过期。"""
    item = _load_tokens().get(key) or {}
    token = item.get("token") or ""
    try:
        alive = float(item.get("expire_at") or 0) > time.time() + 300
    except (TypeError, ValueError):
        alive = False
    return token if (token and alive) else ""


def _store_token(key: str, token: str, expires_in) -> None:
    data = _load_tokens()
    try:
        ttl = float(expires_in or 7200)
    except (TypeError, ValueError):
        ttl = 7200
    data[key] = {"token": token, "expire_at": time.time() + ttl}
    if len(data) > 20:  # 防止文件无限增长
        keep = sorted(data, key=lambda k: data[k].get("expire_at", 0))[-(20):]
        data = {k: data[k] for k in keep}
    _save_tokens(data)


def _drop_token(key: str) -> None:
    data = _load_tokens()
    if data.pop(key, None) is not None:
        _save_tokens(data)


def _get_json(url: str, timeout: int = TOKEN_TIMEOUT) -> tuple[dict, str]:
    code, text = _http(url, method="GET", timeout=timeout)
    if code == 0:
        return {}, text
    try:
        obj = json.loads(text)
        return (obj if isinstance(obj, dict) else {}), ""
    except Exception:
        return {}, _err(code, text)


# ---------------------------------------------------------------- 微信测试号
# 走微信官方「公众号模板消息」接口（POST /cgi-bin/message/template/send）。
# 测试号扫码即得 appid/secret，可自定义模板，模板消息接口无实名要求。


WXMP_API = "https://api.weixin.qq.com"


def wx_access_token(appid: str, secret: str, force: bool = False) -> tuple[str, str]:
    """获取（并缓存）公众号 access_token。返回 (token, 错误说明)。"""
    key = f"wxmp:{appid}"
    if not force:
        cached = _cached_token(key)
        if cached:
            return cached, ""
    url = (
        f"{WXMP_API}/cgi-bin/token?grant_type=client_credential"
        f"&appid={urllib.parse.quote(appid)}&secret={urllib.parse.quote(secret)}"
    )
    obj, err = _get_json(url)
    if err:
        return "", err
    token = obj.get("access_token") or ""
    if token:
        _store_token(key, token, obj.get("expires_in"))
        return token, ""
    code = obj.get("errcode")
    hint = {
        40125: "appsecret 不正确",
        40164: "本机公网 IP 不在白名单（测试号无此限制，请确认用的是测试号的 secret）",
        40013: "appid 不正确",
    }.get(code, obj.get("errmsg", ""))
    return "", f"errcode={code} {hint}"


def wx_followers(appid: str, secret: str) -> tuple[list[str], str]:
    """拉取已关注测试号的 openid 列表，用于在界面里一键填入。"""
    token, err = wx_access_token(appid, secret)
    if not token:
        return [], err
    obj, err = _get_json(f"{WXMP_API}/cgi-bin/user/get?access_token={token}")
    if err:
        return [], err
    if obj.get("errcode"):
        return [], f"errcode={obj.get('errcode')} {obj.get('errmsg','')}"
    ids = ((obj.get("data") or {}).get("openid")) or []
    if not ids:
        return [], "还没有人关注这个测试号，请先微信扫码关注"
    return list(ids), ""


def _flatten(text: str) -> str:
    """压成单行纯文本，给模板字段用（模板里一个变量就是一行，不能带换行）。"""
    return re.sub(r"\s+", " ", plain_text("", text or "")).strip()


# ---------------------------------------------------------------- 微信测试号
#
# 关键认知：模板消息里那个「模板标题」是**创建时写死的**，收件人看到的消息卡片
# 第一行就是它。所以标题叫 test，手机上永远显示 test，跟发什么内容无关。
# 真正的动态信息只能靠 {{变量.DATA}} 那几行。
#
# 又因为测试号可以自建任意变量名（title/content/time、first/keyword1/remark…），
# 所以不能把字段名写死。做法：先用 get_all_private_template 把模板定义拉下来，
# 解析出它声明了哪些变量，再把我们的语义值映射上去。

WXMP_TPL_CACHE = config.STATE_DIR / "wxmp_template.json"
WXMP_TPL_TTL = 12 * 3600          # 模板定义很少变，缓存半天足够
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\.\s*DATA\s*\}\}")

# 变量名 -> 语义角色。按顺序匹配，先到先得。
# 注意 detail4 / detail3 / detail2 必须依次排在 detail 前面：匹配用的是
# startswith，否则 "detail2" 会先被 detail 的 "detail" 前缀吃掉。
_DETAIL_SLOTS = ("detail", "detail2", "detail3", "detail4")

_ROLE_HINTS = (
    ("headline", ("first", "title", "headline", "subject", "topic", "heading")),
    ("project", ("project", "program", "repo", "app")),
    ("detail4", ("detail4", "detailfour")),
    ("detail3", ("detail3", "detailthree")),
    ("detail2", ("detail2", "detailtwo", "extra", "more",
                 "supplement", "remark2", "note")),
    ("detail", ("content", "detail", "remark", "desc", "description",
                "message", "summary", "reason", "error")),
    ("duration", ("duration", "elapsed", "cost", "spent")),
    ("time", ("time", "date", "datetime", "when")),
    ("host", ("host", "device", "machine")),
)
# 名字对不上的变量（keyword1 / keyword2 …）按这个顺序捡剩下的语义值
_LEFTOVER_ORDER = ("project", "time", "detail", "detail2", "detail3", "detail4",
                   "duration", "host", "kind_label")


def _load_wxmp_tpl() -> dict:
    try:
        obj = json.loads(WXMP_TPL_CACHE.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _save_wxmp_tpl(data: dict) -> None:
    try:
        config.ensure_dirs()
        WXMP_TPL_CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def wxmp_template(appid: str, secret: str, tpl: str, force: bool = False) -> dict:
    """拉取模板定义。返回 {title, content, vars, error}。

    拿不到就返回缓存（或空），调用方会退回默认三字段，绝不因此发不出去。
    """
    key = f"{appid}:{tpl}"
    cache = _load_wxmp_tpl()
    item = cache.get(key) or {}
    fresh = time.time() - float(item.get("at") or 0) < WXMP_TPL_TTL
    if item and fresh and not force:
        return item

    token, err = wx_access_token(appid, secret)
    if not token:
        return item or {"error": err}

    obj, gerr = _get_json(
        f"{WXMP_API}/cgi-bin/template/get_all_private_template?access_token={token}"
    )
    if gerr:
        return item or {"error": gerr}
    if obj.get("errcode"):
        return item or {"error": f"errcode={obj.get('errcode')} {obj.get('errmsg','')}"}

    found = None
    for t in obj.get("template_list") or []:
        if str(t.get("template_id") or "") == tpl:
            found = t
            break
    if not found:
        return item or {"error": "模板 ID 不在这个测试号的模板列表里"}

    content = str(found.get("content") or "")
    info = {
        "at": time.time(),
        "title": str(found.get("title") or ""),
        "content": content,
        "vars": _VAR_RE.findall(content),
    }
    cache[key] = info
    _save_wxmp_tpl(cache)
    return info


def _clip_line(text: str, limit: int = 20) -> str:
    """按微信卡片一行的宽度截断，尽量断在句读处，不留半句话。

    模板消息的字段值在卡片里只占一行，约 20 个汉字宽（40 个半角字符），
    超出部分微信自己会加 "..." 截掉，而且是从字符中间硬切。

    这里的原则是「先把这一行填满，再考虑断得好看」：
    只有当标点落在行尾 70% 之后时，才为了断句牺牲几个字；
    否则宁可硬切，也不要把 20 字的空间浪费成 13 字。
    """
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    head = t[:limit]
    floor = max(1, int(limit * 0.7))

    cut = max(head.rfind(c) for c in "。！？；!?;")
    if cut >= floor:
        return head[: cut + 1]
    cut = max(head.rfind(c) for c in "，,、 ")
    if cut >= floor:
        return head[:cut] + "…"
    return head[: limit - 1] + "…"


def _on(value) -> bool:
    """配置里的开关值容错解析（JSON 里可能是 true / "1" / "yes"）。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _char_w(ch: str) -> int:
    """单字符在卡片里占的显示宽度：全角 2、半角 1。

    不能直接数 len()——中文和英文混排时字符数和视觉宽度差一倍，
    按字符数折行会让含英文的那几行明显短一截。
    """
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1


def _in_word(ch: str) -> bool:
    """算英文单词 / 数字的一部分——这类字符不该被折行劈开。"""
    return ch.isascii() and (ch.isalnum() or ch in "._-/:@%")


# 可以在其「之后」断行的 ASCII 字符。点号算断点，这样 sys.stdin.buffer
# 断成 sys.stdin / buffer，而不是把 buffer 劈成 bu + ffer。
_BREAK_AFTER = " \t(/.,-_:;@"

# 中文排版的行首禁则：这些符号不能出现在一行的开头，断到行首就直接吞掉，
# 因为换行本身已经起了停顿作用，留个逗号在行首反而难看。
_NO_LINE_START = "，,、。．.；;：:！!？?）)］]｝}」』》〉”’"


def _take_width(text: str, budget: int, break_at: float = 0.7) -> str:
    """取一段文本里能放进 budget 个半角单位的最长前缀。

    先硬切出一个装满的前缀，再回头找句读：只有当标点落在前缀 70%
    之后时才为断句少放几个字，否则宁可切在字中间——把一行的空间
    浪费掉比断在逗号后面更难看。
    """
    w = 0
    cut = 0
    for i, ch in enumerate(text):
        cw = _char_w(ch)
        if w + cw > budget:
            break
        w += cw
        cut = i + 1
    if cut >= len(text):
        return text

    # 英文单词不劈开：切点落在词中间时退回最近的断点。
    # 只退不超过 12 个字符——超长单词（比如 UnicodeEncodeError）该切还是得切，
    # 否则那一行会空掉一大半。
    if _in_word(text[cut - 1]) and _in_word(text[cut]):
        sp = max(text.rfind(c, 0, cut) for c in _BREAK_AFTER)
        if sp > 0 and cut - sp <= 12:
            cut = sp + 1               # 断点符号留在行尾，读起来才连贯

    head = text[:cut]
    floor = max(1, int(cut * break_at))
    for marks in ("。！？；!?;", "，,、：: "):
        pos = max(head.rfind(c) for c in marks)
        if pos >= floor:
            return head[: pos + 1]
    return head


def _wrap_detail(text: str, width: int = 34, max_lines: int = 4) -> str:
    r"""把长文本按卡片宽度折成多行，行间用 \n 连接。

    微信**不会**替你自动折行。官方《模板消息运营规范》第四条写得很明确：

        旧模板样式才可以用参数通过增加 "\n"，来实现换行，所以，一般建议
        将参数紧贴上一行，在需要时用 "\n" 自行换行。通过该技巧，可以让
        模板消息的拓展能力变得非常强。

    测试号的自建模板正是「旧模板样式」（内容可自由编辑），所以换行只能
    由我们自己插——插了才是多行卡片，不插就是一行被客户端硬切。

    width 按半角单位算，34 ≈ 17 个汉字，是「详情：」标签占位之后一行
    还能安全放下的宽度。max_lines 控制卡片高度：超出的部分折进最后一行
    并加省略号，免得卡片长得收不住。
    """
    flat = _flatten(text)
    if not flat:
        return ""

    lines: list[str] = []
    rest = flat
    while rest and len(lines) < max_lines:
        last = len(lines) == max_lines - 1
        head = _take_width(rest, width)
        if not head:                      # 防御：一个字符都放不下
            head = rest[:1]
        tail = rest[len(head):].lstrip(" 　" + _NO_LINE_START)

        # 到了最后一行还有内容没放下 → 按窄两格重切，腾出位置给省略号
        if last and tail:
            head = _take_width(rest, width - 2) or rest[:1]
            tail = rest[len(head):].lstrip(" 　" + _NO_LINE_START)
            head = head.rstrip(" 　") + "…"

        rest = tail
        lines.append(head.rstrip(" 　"))
    return "\n".join(x for x in lines if x)


def _declared_vars(info: dict) -> list[str]:
    """模板声明了哪些变量；拿不到定义就退回最常见的三字段。"""
    return [v for v in (info.get("vars") or []) if v] or ["title", "content", "time"]


def _role_of(name: str) -> str:
    """变量名对应的语义角色。不做去重，只用来判断模板里有哪几类变量。"""
    low = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    for role, keys in _ROLE_HINTS:
        if any(low == k or low.startswith(k) for k in keys):
            return role
    return ""


# 模板消息卡片的正文宽度，按半角单位算。实测一行放得下 21 个全角字符，
# 即 42 个半角位；值区域的可用宽度还要再减掉「标签：」占掉的那部分。
_CARD_WIDTH = 42


def _label_width(content: str, var: str) -> int:
    """模板里某个变量所在行的「标签：」宽度（半角单位）。

    卡片一行是定宽的，标签越长，留给值的空间越少。写成「详情：」时值能放
    18 个汉字，写成「详细信息：」就只剩 16 个。所以折行宽度得按实际标签算，
    不能写死——否则标签一长，值就溢出被微信硬切。
    """
    for line in (content or "").splitlines():
        m = _VAR_RE.search(line)
        if m and m.group(1) == var:
            return sum(_char_w(c) for c in line[: m.start()])
    return 6          # 拿不到定义就按「详情：」三字算


def _wxmp_values(
    title: str,
    content: str,
    fields: dict,
    now: str,
    wrap: bool = True,
    tpl_content: str = "",
) -> dict[str, str]:
    r"""把一条通知摊成一组语义值，供任意模板变量取用。

    **一个变量只占一行**——这是模板消息卡片的硬规则。`\n` 在新版模板样式里
    不但不换行，还会把后面的内容整段丢掉（实测：详情只剩第一行，连省略号
    都没有）。所以想让详情显示多行，唯一的办法是模板里多声明几个变量：
    detail / detail2 / detail3 / detail4，程序按宽度切段，一段填一个。

    wrap=False 时只填第一个变量，其余留空。
    """
    f = fields or {}
    kind = str(f.get("kind") or "")
    label = str(f.get("kind_label") or "")
    emoji = str(f.get("emoji") or "🔔")

    headline = (title or f"{emoji} {label}").strip()

    # 失败类优先显示原因，成功类显示摘要——这比把整段正文塞进一个字段有用得多
    if kind in ("task_failed", "tool_failed"):
        detail = str(f.get("error") or f.get("summary") or "")
    else:
        detail = str(f.get("summary") or "")
    if not detail.strip() or detail.strip() in ("（无摘要）", "—"):
        detail = _flatten(content)

    # 模板里声明了几个详情变量，就切成几段——微信卡片一个变量只占一行，
    # 想多显示几行只能多声明变量，插 \n 是无效的（实测会连后面内容一起丢）。
    declared = _VAR_RE.findall(tpl_content) or ["title", "content", "time"]
    slot_vars = [v for v in declared if _role_of(v) in _DETAIL_SLOTS]
    slots = max(1, len(slot_vars)) if wrap else 1

    label_w = _label_width(tpl_content, slot_vars[0]) if slot_vars else 6
    width = max(24, min(38, _CARD_WIDTH - label_w))

    flat_detail = _flatten(detail)
    if not flat_detail:
        segs: list[str] = []
    elif slots >= 2:
        segs = _wrap_detail(flat_detail, width, slots).split("\n")
    else:
        segs = [_clip_line(flat_detail, max(10, width // 2))]
    while len(segs) < len(_DETAIL_SLOTS):
        segs.append("")

    return {
        "headline": _clip_line(headline, 20),
        "project": _clip_line(str(f.get("project") or "—"), 20),
        "duration": str(f.get("duration") or "—"),
        "detail": segs[0],
        "detail2": segs[1],
        "detail3": segs[2],
        "detail4": segs[3],
        "time": now,
        "date": str(f.get("date") or now[:10]),
        "host": _clip_line(str(f.get("host") or "—"), 20),
        "kind_label": label or "通知",
        "content": _clip_line(_flatten(content), 200),
    }


def _map_wxmp_data(declared: list[str], values: dict[str, str]) -> dict:
    """把语义值分配到模板实际声明的变量上，返回微信要的 data 结构。"""
    out: dict[str, dict] = {}
    used: set[str] = set()
    pending: list[str] = []

    for name in declared:
        low = re.sub(r"[^a-z0-9]", "", name.lower())
        role = ""
        for r, keys in _ROLE_HINTS:
            if r in used:
                continue
            if any(low == k or low.startswith(k) for k in keys):
                role = r
                break
        if role:
            out[name] = {"value": values.get(role, "")}
            used.add(role)
        else:
            pending.append(name)

    # keyword1 / keyword2 这类无语义名字，按固定顺序捡剩下的
    for name in pending:
        for r in _LEFTOVER_ORDER:
            if r not in used and values.get(r):
                out[name] = {"value": values[r]}
                used.add(r)
                break
        else:
            out[name] = {"value": values.get("detail") or values.get("headline") or "—"}

    return out


def _send_wxmp(
    c: dict, title: str, content: str, fields: dict, timeout: int
) -> tuple[bool, str]:
    appid = (c.get("appid") or "").strip()
    secret = (c.get("secret") or "").strip()
    tpl = (c.get("template_id") or "").strip()
    openid = (c.get("openid") or "").strip()

    if not appid or not secret:
        return False, "未填写 appID / appsecret"
    if not tpl:
        return False, "未填写模板 ID"

    token, err = wx_access_token(appid, secret)
    if not token:
        return False, f"获取 access_token 失败：{err}"

    if not openid:
        ids, err = wx_followers(appid, secret)
        if not ids:
            return False, f"未填 openid 且自动获取失败：{err}"
        openid = ids[0]

    # 官方文档对 time 类字段的推荐格式是「2019年10月1日 15:01」，
    # 这里照抄，避免某些客户端把纯数字日期解析错。
    # 不用 strftime("%-m") —— 那是 POSIX 扩展，Windows 上不支持
    _n = datetime.now()
    now = f"{_n.year}年{_n.month}月{_n.day}日 {_n:%H:%M}"
    wrap = _on(c.get("wrap", True))

    # 先拿到模板真实声明的变量，才知道详情能占几行（几个详情变量 = 几行）
    info = wxmp_template(appid, secret, tpl)
    declared = _declared_vars(info)
    tpl_content = str(info.get("content") or "")
    values = _wxmp_values(title, content, fields, now, wrap=wrap,
                          tpl_content=tpl_content)
    data = _map_wxmp_data(declared, values)

    body = {"touser": openid, "template_id": tpl, "data": data}
    jump = (c.get("url") or "").strip()
    if jump:
        body["url"] = jump

    for attempt in (1, 2):
        code, text = _post_json(
            f"{WXMP_API}/cgi-bin/message/template/send?access_token={token}",
            body,
            timeout,
        )
        if code != 200:
            return False, _err(code, text)
        try:
            obj = json.loads(text)
        except Exception:
            return False, f"响应无法解析：{text[:160]}"
        errcode = obj.get("errcode")
        if errcode == 0:
            return True, "已送达微信"
        # token 失效：清缓存后重试一次
        if errcode in (40001, 40014, 42001) and attempt == 1:
            _drop_token(f"wxmp:{appid}")
            token, err = wx_access_token(appid, secret, force=True)
            if not token:
                return False, f"access_token 失效且刷新失败：{err}"
            continue
        # 47003 = 模板参数不匹配。多半是模板被改过，缓存过期了，重拉一次再试
        if errcode == 47003 and attempt == 1:
            info = wxmp_template(appid, secret, tpl, force=True)
            declared = _declared_vars(info)
            tpl_content = str(info.get("content") or "")
            values = _wxmp_values(title, content, fields, now, wrap=wrap,
                                  tpl_content=tpl_content)
            body["data"] = _map_wxmp_data(declared, values)
            continue
        reason = {
            40003: "openid 无效，请重新复制",
            40037: "模板 ID 无效",
            43004: "该微信还没关注你的测试号",
            45009: "接口调用频率超限，稍后再试",
            47003: "模板字段对不上（模板被改过？）",
            48001: "接口未授权（请确认用的是测试号）",
        }.get(errcode, obj.get("errmsg", ""))
        return False, f"errcode={errcode} {reason}"
    return False, "发送失败"


# 模板标题叫这些名字，说明是当初随手建的占位模板，手机上第一行会一直是它
_PLACEHOLDER_TITLES = {"", "test", "测试", "模板", "模板测试", "demo", "example", "tmp"}

# first / remark 是模板库保留字段，可以不跟冒号；其余变量都必须有「标签：」
_COLON_FREE_VARS = {"first", "remark"}


def wxmp_content_warnings(content: str) -> list[str]:
    """检查模板内容写法 —— 这是「收到消息但内容全空」的唯一原因。

    微信解析模板时，**变量前面必须有「标签：」**，否则它不认为这是个待替换的
    动态字段，那一行直接渲染成空。纯 `{{xxx.DATA}}` 单独占一行是无效的：

        项目：{{project.DATA}}   ✓ 会替换
        {{detail.DATA}}          ✗ 渲染为空

    first / remark 是模板库保留字段，不需要冒号。
    """
    warns: list[str] = []
    for line in (content or "").splitlines():
        for m in _VAR_RE.finditer(line):
            if m.group(1).lower() in _COLON_FREE_VARS:
                continue
            before = line[: m.start()].rstrip()
            if not before.endswith(("：", ":")):
                warns.append(
                    f"{{{{{m.group(1)}.DATA}}}} 前面缺「标签：」，这一行会是空的"
                )
    return warns


def wxmp_diagnose(c: dict) -> list[str]:
    """诊断微信模板：把「模板长什么样」和「我们会填什么」摊开给人看。"""
    appid = (c.get("appid") or "").strip()
    secret = (c.get("secret") or "").strip()
    tpl = (c.get("template_id") or "").strip()

    if not appid or not secret:
        return ["✗ 未填写 appID / appsecret"]
    if not tpl:
        return ["✗ 未填写模板 ID"]

    info = wxmp_template(appid, secret, tpl)
    if info.get("error"):
        return [f"✗ 读取模板失败：{info['error']}"]

    tpl_title = str(info.get("title") or "")
    tpl_content = str(info.get("content") or "")
    declared = _declared_vars(info)
    slot_vars = [v for v in declared if _role_of(v) in _DETAIL_SLOTS]

    lines = [
        f"模板标题 : {tpl_title!r}",
        f"模板内容 : {tpl_content.replace(chr(10), ' ⏎ ')!r}",
        f"声明变量 : {', '.join(declared)}",
        f"详情变量 : {', '.join(slot_vars) if slot_vars else '（没有）'} "
        f"→ 详情最多占 {max(1, len(slot_vars))} 行",
        "",
    ]

    # 用一条示例通知走一遍映射，让人看到实际会填进去什么。
    # 摘要故意写长一点，这样分段效果在诊断里就能直接看出来。
    demo_fields = {
        "kind": "task_done", "kind_label": "任务完成", "emoji": "✅",
        "project": "Work buddy 通知", "duration": "25 秒",
        "date": time.strftime("%Y-%m-%d"), "host": "本机",
        "summary": "已修好编码问题，现在通知能正常送达，中文不会再变成乱码，"
                   "详情也会按卡片宽度自动折行显示。",
        "error": "—",
    }
    wrap = _on(c.get("wrap", True))
    values = _wxmp_values("✅ WorkBuddy 任务完成", "", demo_fields,
                          time.strftime("%Y-%m-%d %H:%M"),
                          wrap=wrap, tpl_content=tpl_content)
    mapped = _map_wxmp_data(declared, values)

    lines.append("示例通知实际会填：")
    for name, item in mapped.items():
        shown = str(item["value"]).replace("\n", " ⏎ ")
        lines.append(f"  {{{{{name}.DATA}}}} = {shown!r}")
    lines.append("")

    # 分段状态：说明详情能占几行、为什么
    n_slots = max(1, len(slot_vars))
    filled = [i for i in range(n_slots) if values.get(_DETAIL_SLOTS[i])]
    if not wrap:
        lines.append("○ 详情分段已关闭：只填第一个详情变量，其余留空。")
    elif n_slots >= 2:
        avail = max(24, min(38, _CARD_WIDTH - _label_width(tpl_content, slot_vars[0])))
        lines.append(f"✓ 详情分段：模板有 {n_slots} 个详情变量，"
                     f"示例详情填满了 {len(filled)} 段。")
        lines.append(f"  每段占一行，值区宽 {avail} 个半角位（按标签实际宽度算）。")
    else:
        lines.append("⚠ 模板只有 1 个详情变量，详情只能显示一行，超出部分截断。")
        lines.append("  → 想多显示几行，在模板里多加一行「补充：{{detail2.DATA}}」这类字段，")
        lines.append("    一个详情变量占一行（最多 detail / detail2 / detail3 / detail4 四行）。")
        lines.append("    别指望 \\n：新版卡片不但不换行，还会把换行后面的内容整段丢掉。")
    lines.append("")

    # 写法检查——这是「内容全空」的直接原因
    warns = wxmp_content_warnings(str(info.get("content") or ""))
    if warns:
        lines.append("✗ 模板写法有问题（微信要求变量前有「标签：」）：")
        for w in warns:
            lines.append(f"    {w}")
        lines.append("  → 没冒号的行在手机上就是空白。删掉模板重建，内容照抄：")
        lines.append("      状态：{{title.DATA}}")
        lines.append("      项目：{{project.DATA}}")
        lines.append("      详情：{{detail.DATA}}")
        lines.append("      时间：{{time.DATA}}")
    else:
        lines.append("✓ 模板写法正常：每个变量前都有「标签：」")
    lines.append("")

    if tpl_title.strip().lower() in _PLACEHOLDER_TITLES:
        lines.append(f"⚠ 模板标题是 {tpl_title!r} —— 收件人看到的消息卡片第一行就是它，")
        lines.append("  所以手机上永远显示这一行，与发什么内容无关。")
        lines.append("  → 删掉这个模板，新建一个标题有意义的")
        lines.append("    （README「步骤 1 · 方案 A · ③ 建模板」有现成内容可复制）")
    else:
        lines.append(f"✓ 模板标题是 {tpl_title!r}，手机上第一行会显示它。")
    return lines


# ---------------------------------------------------------------- 企业微信自建应用
# 比群机器人更好用：直接推到个人，支持 markdown 自由排版，无条数限制。
# 个人可在 work.weixin.qq.com 用微信扫码注册「团队」，无需营业执照。


WECOM_API = "https://qyapi.weixin.qq.com"


def wecom_access_token(corpid: str, secret: str, force: bool = False) -> tuple[str, str]:
    key = f"wecom:{corpid}"
    if not force:
        cached = _cached_token(key)
        if cached:
            return cached, ""
    url = (
        f"{WECOM_API}/cgi-bin/gettoken?corpid={urllib.parse.quote(corpid)}"
        f"&corpsecret={urllib.parse.quote(secret)}"
    )
    obj, err = _get_json(url)
    if err:
        return "", err
    token = obj.get("access_token") or ""
    if token:
        _store_token(key, token, obj.get("expires_in"))
        return token, ""
    return "", f"errcode={obj.get('errcode')} {obj.get('errmsg','')}"


def _send_wecom_app(c: dict, title: str, content: str, timeout: int) -> tuple[bool, str]:
    corpid = (c.get("corpid") or "").strip()
    secret = (c.get("secret") or "").strip()
    agentid = (c.get("agentid") or "").strip()
    touser = (c.get("touser") or "").strip() or "@all"

    if not corpid or not secret:
        return False, "未填写企业 ID / 应用 Secret"
    if not agentid:
        return False, "未填写应用 AgentId"

    token, err = wecom_access_token(corpid, secret)
    if not token:
        return False, f"获取 access_token 失败：{err}"

    md = f"## {title}\n{content}" if content else f"## {title}"
    try:
        aid = int(agentid)
    except ValueError:
        return False, "AgentId 必须是数字"

    body = {
        "touser": touser,
        "msgtype": "markdown",
        "agentid": aid,
        "markdown": {"content": _truncate_bytes(md, 2000)},
    }
    for attempt in (1, 2):
        code, text = _post_json(
            f"{WECOM_API}/cgi-bin/message/send?access_token={token}", body, timeout
        )
        if code != 200:
            return False, _err(code, text)
        try:
            obj = json.loads(text)
        except Exception:
            return False, f"响应无法解析：{text[:160]}"
        errcode = obj.get("errcode")
        if errcode == 0:
            if obj.get("invaliduser"):
                return False, f"收件人无效：{obj['invaliduser']}"
            return True, "已送达企业微信"
        if errcode in (40014, 42001) and attempt == 1:
            _drop_token(f"wecom:{corpid}")
            token, err = wecom_access_token(corpid, secret, force=True)
            if not token:
                return False, f"access_token 失效且刷新失败：{err}"
            continue
        reason = {
            60020: "本机公网 IP 不在可信 IP 列表（应用详情页可配置）",
            40056: "AgentId 无效",
            81013: "收件人不在应用可见范围",
        }.get(errcode, obj.get("errmsg", ""))
        return False, f"errcode={errcode} {reason}"
    return False, "发送失败"


# ---------------------------------------------------------------- 各通道


def _send_bark(c: dict, title: str, content: str, timeout: int) -> tuple[bool, str]:
    key = (c.get("key") or "").strip().strip("/")
    if not key:
        return False, "未填写设备 Key"
    server = (c.get("server") or "https://api.day.app").rstrip("/")
    body = {
        "device_key": key,
        "title": title,
        "body": content or title,
    }
    for k in ("group", "sound", "level", "icon"):
        v = (c.get(k) or "").strip()
        if v:
            body[k] = v
    code, text = _post_json(f"{server}/push", body, timeout)
    if code != 200:
        return False, _err(code, text)
    try:
        obj = json.loads(text)
        if obj.get("code") == 200:
            return True, "已提交"
        return False, f"code={obj.get('code')} {obj.get('message','')}"
    except Exception:
        return True, "已提交"


def _send_ntfy(c: dict, title: str, content: str, timeout: int) -> tuple[bool, str]:
    topic = (c.get("topic") or "").strip()
    if not topic:
        return False, "未填写 topic"
    server = (c.get("server") or "https://ntfy.sh").rstrip("/")
    body = {
        "topic": topic,
        "title": title,
        "message": content or title,
    }
    try:
        body["priority"] = max(1, min(5, int(c.get("priority") or 3)))
    except (TypeError, ValueError):
        body["priority"] = 3
    tags = [t.strip() for t in str(c.get("tags") or "").split(",") if t.strip()]
    if tags:
        body["tags"] = tags
    code, text = _post_json(server + "/", body, timeout)
    if 200 <= code < 300:
        return True, "已提交"
    return False, _err(code, text)


def _send_wecom(c: dict, title: str, content: str, timeout: int) -> tuple[bool, str]:
    hook = (c.get("webhook") or "").strip()
    if not hook:
        return False, "未填写 Webhook"
    md = f"**{title}**\n{content}" if content else f"**{title}**"
    body = {"msgtype": "markdown", "markdown": {"content": _truncate_bytes(md, 4000)}}
    code, text = _post_json(hook, body, timeout)
    if code != 200:
        return False, _err(code, text)
    try:
        obj = json.loads(text)
        if obj.get("errcode") == 0:
            return True, "已提交"
        return False, f"errcode={obj.get('errcode')} {obj.get('errmsg','')}"
    except Exception:
        return False, f"响应无法解析: {text[:160]}"


def _send_dingtalk(c: dict, title: str, content: str, timeout: int) -> tuple[bool, str]:
    hook = (c.get("webhook") or "").strip()
    if not hook:
        return False, "未填写 Webhook"
    secret = (c.get("secret") or "").strip()
    if secret:
        ts = str(round(time.time() * 1000))
        sign_raw = f"{ts}\n{secret}".encode()
        digest = hmac.new(secret.encode("utf-8"), sign_raw, hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(digest))
        sep = "&" if "?" in hook else "?"
        hook = f"{hook}{sep}timestamp={ts}&sign={sign}"
    md = f"### {title}\n\n{content}" if content else f"### {title}"
    body = {"msgtype": "markdown", "markdown": {"title": title, "text": md}}
    code, text = _post_json(hook, body, timeout)
    if code != 200:
        return False, _err(code, text)
    try:
        obj = json.loads(text)
        if obj.get("errcode") == 0:
            return True, "已提交"
        return False, f"errcode={obj.get('errcode')} {obj.get('errmsg','')}"
    except Exception:
        return False, f"响应无法解析: {text[:160]}"


def _send_feishu(c: dict, title: str, content: str, timeout: int) -> tuple[bool, str]:
    hook = (c.get("webhook") or "").strip()
    if not hook:
        return False, "未填写 Webhook"
    text = plain_text(title, content)
    body = {"msg_type": "text", "content": {"text": text[:4000]}}
    code, resp = _post_json(hook, body, timeout)
    if code != 200:
        return False, _err(code, resp)
    try:
        obj = json.loads(resp)
        # 飞书成功返回 code=0 或 StatusCode=0
        if obj.get("code") in (0, None) and obj.get("StatusCode", 0) == 0:
            return True, "已提交"
        return False, f"code={obj.get('code')} {obj.get('msg','')}"
    except Exception:
        return False, f"响应无法解析: {resp[:160]}"


def _send_smtp(c: dict, title: str, content: str, timeout: int) -> tuple[bool, str]:
    # smtplib + email.* 只在真发邮件时才需要，放在这里延迟导入：
    # 模块级导入会拖慢启动（实测 smtplib 一项 47ms），而绝大多数用户
    # 用的是微信/Webhook 通道，一辈子走不到这个函数。
    import smtplib
    from email.header import Header
    from email.mime.text import MIMEText
    from email.utils import formataddr

    host = (c.get("host") or "").strip()
    user = (c.get("user") or "").strip()
    pwd = (c.get("password") or "").strip()
    to = (c.get("to") or user).strip()
    if not host or not user or not pwd:
        return False, "SMTP 服务器 / 账号 / 密码未填全"
    try:
        port = int(c.get("port") or 465)
    except (TypeError, ValueError):
        port = 465

    msg = MIMEText(content or title, "plain", "utf-8")
    # 标题 / 收件人 / 发件人都要压成单行：混入换行可以伪造额外邮件头（比如偷偷加 Bcc）
    msg["Subject"] = Header(one_line(title), "utf-8")
    msg["From"] = formataddr((str(Header("任务通知", "utf-8")), one_line(user)))
    msg["To"] = one_line(to)

    recipients = [x.strip() for x in one_line(to).replace(";", ",").split(",") if x.strip()]
    if not recipients:
        return False, "收件人为空"
    try:
        if c.get("ssl", True):
            server = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
            server.starttls()
        with server:
            server.login(user, pwd)
            server.sendmail(user, recipients, msg.as_string())
        return True, "已发送"
    except Exception as e:
        return False, redact(f"{type(e).__name__}: {e}", 200)


def _send_webhook(c: dict, title: str, content: str, fields: dict, timeout: int) -> tuple[bool, str]:
    url = (c.get("url") or "").strip()
    if not url:
        return False, "未填写 URL"
    method = (c.get("method") or "POST").upper()
    tpl = (c.get("body_template") or "").strip()

    if tpl:
        from .render import render

        payload = render(tpl, {**fields, "title": title, "content": content})
        data = payload.encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
    else:
        body = {
            "title": title,
            "content": content,
            "kind": fields.get("kind"),
            "kind_label": fields.get("kind_label"),
            "project": fields.get("project"),
            "app": fields.get("app"),
            "time": fields.get("datetime"),
            "event": fields.get("event"),
        }
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}

    custom = c.get("headers") or {}
    if isinstance(custom, dict):
        headers.update({str(k): str(v) for k, v in custom.items()})

    code, text = _http(url, data, headers, method, timeout)
    if 200 <= code < 300:
        return True, f"HTTP {code}"
    return False, _err(code, text)


def _send_log(c: dict, title: str, content: str, fields: dict) -> tuple[bool, str]:
    try:
        config.ensure_dirs()
        path = config.LOG_DIR / "notifications.log"
        with path.open("a", encoding="utf-8") as f:
            f.write(
                f"{fields.get('datetime')} [{fields.get('app')}] "
                f"{fields.get('kind')} | {title}\n"
                + "".join(f"    {ln}\n" for ln in (content or "").splitlines())
            )
        return True, "已记录"
    except Exception as e:
        return False, redact(f"{type(e).__name__}: {e}", 200)


# ---------------------------------------------------------------- 统一入口


def send_one(
    channel: dict, title: str, content: str, fields: dict, timeout: int = SEND_TIMEOUT
) -> tuple[bool, str]:
    ctype = channel.get("type", "")
    # 兜底清洗：落单代理字符会让 json.dumps(...).encode("utf-8") 直接抛异常，
    # 整条消息就此发不出去。在这里洗一次，所有通道（含测试发送）都安全。
    title = clean_text(title)
    content = clean_text(content)
    fields = clean_obj(fields) if isinstance(fields, dict) else fields
    try:
        if ctype == "wxmp":
            return _send_wxmp(channel, title, content, fields, timeout)
        if ctype == "wecom_app":
            return _send_wecom_app(channel, title, content, timeout)
        if ctype == "bark":
            return _send_bark(channel, title, content, timeout)
        if ctype == "ntfy":
            return _send_ntfy(channel, title, content, timeout)
        if ctype == "wecom_bot":
            return _send_wecom(channel, title, content, timeout)
        if ctype == "dingtalk_bot":
            return _send_dingtalk(channel, title, content, timeout)
        if ctype == "feishu_bot":
            return _send_feishu(channel, title, content, timeout)
        if ctype == "smtp":
            return _send_smtp(channel, title, content, timeout)
        if ctype == "webhook":
            return _send_webhook(channel, title, content, fields, timeout)
        if ctype == "log":
            return _send_log(channel, title, content, fields)
        return False, f"未知通道类型：{ctype}"
    except Exception as e:  # 任何通道异常都不允许冒泡
        return False, redact(f"{type(e).__name__}: {e}", 200)


def send_all(cfg: dict, title: str, content: str, fields: dict) -> list[tuple[str, bool, str]]:
    """向所有启用的通道发送。返回 [(通道类型, 是否成功, 说明)]。"""
    timeout = int(cfg.get("advanced", {}).get("http_timeout") or 8)
    results: list[tuple[str, bool, str]] = []
    for ch in config.enabled_channels(cfg):
        ok, msg = send_one(ch, title, content, fields, timeout)
        results.append((ch.get("type", "?"), ok, msg))
        store.log(
            f"channel={ch.get('type')} ok={ok} msg={msg[:120]}"
        )
    return results


def has_remote_channel(cfg: dict) -> bool:
    """是否存在「会发到手机」的通道（log 只是本地落盘，不算）。"""
    return any(c.get("type") not in LOCAL_CHANNELS
               for c in config.enabled_channels(cfg))
