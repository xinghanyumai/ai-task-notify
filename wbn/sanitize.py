"""脱敏与输入校验。

集中放在这里的原因：`channels` 和 `store` 都要用，但两者互相依赖
（channels 依赖 store 写日志），谁也不能反过来 import 对方，
所以抽成一个不依赖任何内部模块的叶子模块。

覆盖三类风险：
1. 错误信息把凭证带出来 —— 有的异常文本会回显完整 URL / 请求参数，
   而 webhook 地址里通常就藏着 key、token。写日志前必须抹掉。
2. 错误信息把本地文件内容带出来 —— urllib 支持 file:// 协议，
   若不限制 scheme，一个配错的 URL 就能把本地文件读进错误消息。
3. 邮件头注入 —— 收件人 / 标题里混入换行可以伪造额外头部。

另外负责清理「落单代理字符」（lone surrogate），见 clean_text 的说明。
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# 落单代理字符：U+D800–U+DFFF。UTF-8 编不出来，写文件 / json.dumps 都会炸。
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
# surrogateescape 只会产出 U+DC80–U+DCFF，一段连续的这类码位对应一串原始字节
_SUR_RUN = re.compile("[\udc80-\udcff]+")


def _decode_run(run: str) -> str:
    """把一段代理字符还原成能读的文字。

    先按 UTF-8 试（宿主正常传 UTF-8 时能 100% 复原中文），
    再按 GBK 试（宿主在中文 Windows 上按 ANSI 读文件时会落到这里），
    都不行才用 U+FFFD 占位——占位比崩溃好，至少消息能发出去。
    """
    raw = run.encode("utf-8", "surrogateescape")
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def clean_text(text):
    """去掉落单代理字符。不是 str 就原样返回。

    为什么会有这种东西：
    宿主进程（Node）读到的原始字节如果本身不是合法 UTF-8，
    经 JSON.stringify 会写成 "\\udca2" 这类转义，Python 解出来就是
    一个孤零零的代理码位。它不是合法字符，`s.encode("utf-8")` 直接抛
    UnicodeEncodeError，整条通知就此消失（这正是「只收到测试消息」的原因）。

    处理方式不是简单删掉，而是尽量**还原**：
    U+DC80–U+DCFF 恰好是 surrogateescape 对字节 0x80–0xFF 的一一映射，
    所以先把连续的一段编回字节，再重新解码，原本的中文就能复原。
    """
    if not isinstance(text, str) or not text:
        return text
    if not _LONE_SURROGATE.search(text):
        return text  # 绝大多数情况走这里，零开销
    out = _SUR_RUN.sub(lambda m: _decode_run(m.group(0)), text)
    # 非 DC80–DCFF 的孤立代理（真·半个 UTF-16 字符）无从还原，直接删
    return _LONE_SURROGATE.sub("", out)


def clean_obj(obj, _depth: int = 0):
    """递归清洗 dict / list / tuple / str 里的落单代理字符。

    在「事件进入系统」的边界调用一次，下游就不必到处防御。
    """
    if _depth > 32:  # 防御异常深的嵌套，正常情况下用不到
        return obj
    if isinstance(obj, str):
        return clean_text(obj)
    if isinstance(obj, dict):
        return {clean_obj(k, _depth + 1): clean_obj(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_obj(v, _depth + 1) for v in obj]
    if isinstance(obj, tuple):
        return tuple(clean_obj(v, _depth + 1) for v in obj)
    return obj

# 形如 secret=xxx / access_token=xxx / key=xxx 的查询参数或表单字段
_KV = re.compile(
    r"(?i)\b(secret|appsecret|access_token|token|key|password|passwd|pwd|sign)"
    r"\s*[=:]\s*([^\s&,;\"'}\]]+)"
)

# 长十六进制串（企业微信 key、钉钉 sign、各种 token 都是这种形态）
_HEX = re.compile(r"\b[0-9a-fA-F]{24,}\b")

# URL 里 path 段的长随机串，例如 /hook/3f2a9c...
_LONG_PATH = re.compile(r"(/[A-Za-z0-9_\-]*?)([A-Za-z0-9_\-]{24,})(?=[/?#\s]|$)")

# 需要掩码时保留的首尾长度，方便自己辨认是哪一个凭证
_HEAD, _TAIL = 3, 2


def _mask(value: str) -> str:
    if len(value) <= _HEAD + _TAIL:
        return "***"
    return f"{value[:_HEAD]}***{value[-_TAIL:]}"


def redact(text: str, limit: int = 400) -> str:
    """抹掉文本里的凭证痕迹，并截断。用于一切要落盘或展示的错误信息。"""
    if not text:
        return ""
    # 先清代理字符：带代理的字符串连写文件都会失败，日志会整条丢掉
    out = str(clean_text(str(text)))

    out = _KV.sub(lambda m: f"{m.group(1)}={_mask(m.group(2))}", out)
    out = _HEX.sub(lambda m: _mask(m.group(0)), out)
    out = _LONG_PATH.sub(lambda m: f"{m.group(1)}{_mask(m.group(2))}", out)

    # 换行必须压成单个空格：\r\n 逐个 replace 会留下两个空格，
    # 落进日志后每行前面多一格，读起来像缩进错位
    out = re.sub(r"[\r\n]+", " ", out).strip()
    if len(out) > limit:
        out = out[: limit - 1] + "…"
    return out


def check_http_url(url: str) -> str:
    """只放行 http / https。返回错误说明，空字符串表示通过。

    必须校验：urllib 默认支持 file:// / ftp://，
    一旦配置里出现 file:///C:/... ，请求就会去读本地文件，
    而读到的内容可能顺着错误信息进日志。
    """
    raw = (url or "").strip()
    if not raw:
        return "URL 为空"
    try:
        parts = urlsplit(raw)
    except Exception:
        return "URL 格式不正确"
    if parts.scheme.lower() not in ("http", "https"):
        return f"只支持 http/https，当前是 {parts.scheme or '（无协议）'}"
    if not parts.hostname:
        return "URL 缺少主机名"
    return ""


def one_line(text: str) -> str:
    """压成单行。用于邮件头这类不能含换行的位置，防头部注入。"""
    return re.sub(r"[\r\n]+", " ", str(text or "")).strip()
