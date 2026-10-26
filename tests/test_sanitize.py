"""输入清洗与脱敏回归。

这里守的是两个真实事故：

1. **编码事故**（README 8.1）：宿主按 cp936 + surrogateescape 解 stdin，
   中文变成落单代理字符，`s.encode("utf-8")` 抛 UnicodeEncodeError，
   整条通知凭空消失。表现为「测试消息收得到，真实任务一条都没有」。
2. **凭证外泄**：通道报错原文常常回显完整 URL，而 webhook 地址里就藏着
   key / token。这些东西一旦进日志就等于二次泄露。
"""

from __future__ import annotations

import pytest

from wbn.sanitize import clean_obj, clean_text, redact

# ---------------------------------------------------------------- clean_text


def test_lone_surrogates_from_utf8_bytes_are_restored_to_chinese():
    """UTF-8 字节被 surrogateescape 打散后，必须按原编码还原成中文。"""
    mangled = b"\xe4\xb8\xad\xe6\x96\x87\xff".decode("utf-8", "surrogateescape")
    assert "\udcff" in mangled  # 前提：确实产生了落单代理字符

    cleaned = clean_text(mangled)

    assert cleaned.startswith("中文"), f"中文没被还原：{cleaned!r}"
    assert not any("\ud800" <= ch <= "\udfff" for ch in cleaned)


def test_lone_surrogates_from_gbk_bytes_fall_back_to_gbk():
    """宿主按 ANSI 读文件时字节是 GBK，UTF-8 解不通就得退回 GBK。"""
    mangled = b"\xd6\xd0\xce\xc4".decode("utf-8", "surrogateescape")
    assert clean_text(mangled) == "中文"


def test_clean_text_always_returns_encodable_text():
    """这条是事故的根因：清洗后必须能 encode，否则写日志/发请求时炸掉。"""
    payloads = [
        b"\xe4\xb8\xad\xe6\x96\x87\xff".decode("utf-8", "surrogateescape"),
        "\ud800孤立高代理",
        "混合 \udcff 与中文",
    ]
    for raw in payloads:
        clean_text(raw).encode("utf-8")  # 不抛异常即通过


def test_true_lone_surrogate_is_dropped_not_raised():
    """真·半个 UTF-16 字符（U+D800）无从还原，删掉即可，不能抛。"""
    assert clean_text("\ud800abc") == "abc"


@pytest.mark.parametrize(
    "text",
    ["正常中文", "emoji 🎉🚀", "ascii only", "", "混合 mixed 中英 123 🎯", "  带空格  "],
)
def test_normal_text_is_left_untouched(text):
    """幂等：清洗不能把好文字改坏。"""
    assert clean_text(text) == text


@pytest.mark.parametrize("value", [None, 123, 4.5, b"bytes", ["list"], {"k": "v"}])
def test_non_string_values_pass_through_unchanged(value):
    assert clean_text(value) is value


# ---------------------------------------------------------------- clean_obj


def test_clean_obj_cleans_nested_strings():
    dirty = {
        "text": b"\xe4\xb8\xad\xe6\x96\x87".decode("utf-8", "surrogateescape") + "\udcff",
        "list": ["ok", "\udca2"],
        "nested": {"deep": "\udcad"},
    }
    cleaned = clean_obj(dirty)

    assert cleaned["text"].startswith("中文")
    assert cleaned["list"][0] == "ok"
    assert not any("\ud800" <= ch <= "\udfff" for ch in str(cleaned))


def test_clean_obj_preserves_container_types():
    out = clean_obj({"t": ("a", "b"), "l": [1, 2], "s": "x"})
    assert isinstance(out["t"], tuple)
    assert isinstance(out["l"], list)
    assert out == {"t": ("a", "b"), "l": [1, 2], "s": "x"}


def test_clean_obj_survives_absurd_nesting():
    """深度防御：超过 32 层直接原样返回，不能 RecursionError。"""
    deep: object = "leaf"
    for _ in range(60):
        deep = [deep]
    clean_obj(deep)  # 不抛异常即通过


# ---------------------------------------------------------------- redact


def test_redact_masks_key_in_url_query():
    """webhook / bark 的 key 就藏在 URL 里，报错回显时不能原样落盘。"""
    url = "https://api.day.app/AbCdEfGhIjKlMnOpQrStUvWx/key123?secret=abcdef123456&x=1"
    out = redact(url)

    assert "abcdef123456" not in out
    assert "secret=abc***56" in out


def test_redact_masks_long_hex_credentials():
    secret = "48a973b0431cb46b7105772847384240"
    out = redact(f"wecom error, key: {secret}")

    assert secret not in out
    assert "48a***40" in out


def test_redact_masks_long_random_path_segment():
    out = redact("https://qyapi.weixin.qq.com/cgi-bin/hook/AbCdEfGhIjKlMnOpQrStUvWx/send")
    assert "AbCdEfGhIjKlMnOpQrStUvWx" not in out


def test_redact_collapses_newlines_to_block_header_injection():
    """错误文本里的换行可以伪造额外邮件头 / 日志行。"""
    out = redact("boom\r\nInjected-Header: evil")
    assert "\n" not in out and "\r" not in out
    assert out == "boom Injected-Header: evil"


def test_redact_truncates_to_limit():
    out = redact("x" * 1000, limit=100)
    assert len(out) == 100
    assert out.endswith("…")


def test_redact_of_empty_is_empty():
    assert redact("") == ""
    assert redact(None) == ""
