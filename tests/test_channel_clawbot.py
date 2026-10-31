"""ClawBot 通道（W10 §3）。

**为什么值得单独一个文件**：这条通道有两个别处没有的性质——

1. 凭证**可能不在配置里**，而是从 WorkBuddy 的运行时状态目录自动探测出来。
   探测会踩四种失败（目录没有 / JSON 坏 / base64 坏 / 正则不匹配），每一种
   都必须返回人话，不能是 `TypeError` —— 它跑在「发通知」的热路径上，
   抛异常等于整条通知丢了。
2. 它带着一个真 token 出门。**token 一个字符都不许进日志**：`redact()`
   兜不住 `<8hex>@im.bot:060000<32hex>` 这种形状，必须自己截成前 8 位。

另外 `_post_json` 原来只会设 `Content-Type`，这次要加 `Authorization` 一族
头，但**现有 6 个调用点一个字都不许改**——所以 `headers` 必须可选且默认 `None`。
"""

from __future__ import annotations

import base64
import inspect
import json

import pytest

from wbn import channels

# ---------------------------------------------------------------- _post_json


def test_post_json_headers_is_optional_and_defaults_to_none():
    """`headers` 默认 None：现有 6 个调用点靠这个保持行为不变。"""
    sig = inspect.signature(channels._post_json)
    assert "headers" in sig.parameters
    assert sig.parameters["headers"].default is None


def _capture_request(monkeypatch) -> list:
    """把 urlopen 换掉，返回它收到的 Request 列表。"""
    seen = []

    class _Resp:
        status = 200

        def read(self, n: int = -1) -> bytes:
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake(req, timeout=None):
        seen.append(req)
        return _Resp()

    monkeypatch.setattr(channels.urllib.request, "urlopen", _fake)
    return seen


def test_post_json_without_headers_sends_only_content_type(monkeypatch):
    """不传 headers 时，请求头里不许冒出 Authorization 之类的东西。"""
    seen = _capture_request(monkeypatch)
    channels._post_json("https://example.com/x", {"a": 1}, 5)

    hdrs = {k.lower() for k in seen[0].headers}
    assert "content-type" in hdrs
    assert "authorization" not in hdrs
    assert "authorizationtype" not in hdrs


def test_post_json_passes_extra_headers(monkeypatch):
    """传了 headers 就要真的带上——ClawBot 全靠这三个头。"""
    seen = _capture_request(monkeypatch)
    channels._post_json(
        "https://example.com/x", {"a": 1}, 5,
        headers={"Authorization": "Bearer abc", "AuthorizationType": "ilink_bot_token"},
    )

    assert seen[0].get_header("Authorization") == "Bearer abc"
    assert seen[0].get_header("Authorizationtype") == "ilink_bot_token"


# ---------------------------------------------------------------- 凭证探测


def _write_cursor(directory, name: str, token: str, *, mtime: float = 0.0) -> None:
    """造一个 WorkBuddy 的游标文件：token 藏在 base64 的 get_updates_buf 里。"""
    payload = json.dumps({"token": token}).encode()
    buf = base64.b64encode(payload).decode()
    p = directory / name
    p.write_text(json.dumps({"get_updates_buf": buf}), encoding="utf-8")
    if mtime:
        import os

        os.utime(p, (mtime, mtime))


TOKEN_A = "aaaa1111@im.bot:060000" + "b" * 32
TOKEN_B = "bbbb2222@im.bot:060000" + "c" * 32


def test_discover_takes_the_newest_cursor_file(tmp_path):
    """重新绑定后 bot_id 会变，目录里会有多个文件——取 mtime 最新的。"""
    _write_cursor(tmp_path, "old_im.bot.cursor.json", TOKEN_A, mtime=1_000_000)
    _write_cursor(tmp_path, "new_im.bot.cursor.json", TOKEN_B, mtime=2_000_000)

    token, why = channels.clawbot_discover(tmp_path)

    assert token == TOKEN_B
    assert why == ""


def test_discover_missing_dir_says_so(tmp_path):
    """目录不存在 → 人话，不抛。"""
    token, why = channels.clawbot_discover(tmp_path / "nope")

    assert token == ""
    assert "没找到" in why


def test_discover_broken_json_says_so(tmp_path):
    """游标文件不是合法 JSON → 人话，不抛。"""
    (tmp_path / "x_im.bot.cursor.json").write_text("{坏 JSON", encoding="utf-8")

    token, why = channels.clawbot_discover(tmp_path)

    assert token == ""
    assert "JSON" in why or "读不出" in why


def test_discover_broken_base64_says_so(tmp_path):
    """get_updates_buf 不是合法 base64 → 人话，不抛。"""
    (tmp_path / "x_im.bot.cursor.json").write_text(
        json.dumps({"get_updates_buf": "这不是 base64!!"}), encoding="utf-8"
    )

    token, why = channels.clawbot_discover(tmp_path)

    assert token == ""
    assert "base64" in why.lower()


def test_discover_without_token_says_so(tmp_path):
    """base64 解得开但里面没有 token（宿主改了编码方式）→ 人话，不抛。"""
    buf = base64.b64encode(b'{"nothing": "here"}').decode()
    (tmp_path / "x_im.bot.cursor.json").write_text(
        json.dumps({"get_updates_buf": buf}), encoding="utf-8"
    )

    token, why = channels.clawbot_discover(tmp_path)

    assert token == ""
    assert "bot_token" in why or "格式" in why


# ---------------------------------------------------------------- 三级来源


def test_manual_token_wins_over_discovery(monkeypatch, tmp_path):
    """填了 token 就**不看**游标文件——宿主改了目录结构也不影响手动配置。"""
    called = []
    monkeypatch.setattr(
        channels, "clawbot_discover", lambda *a, **k: called.append(1) or (TOKEN_B, "")
    )

    token, source = channels.clawbot_credential({"bot_token": TOKEN_A})

    assert (token, source) == (TOKEN_A, "manual")
    assert called == [], "手动填了 token 就不该再去翻 WorkBuddy 的目录"


def test_auto_discover_used_when_token_empty(monkeypatch):
    monkeypatch.setattr(channels, "clawbot_discover", lambda *a, **k: (TOKEN_B, ""))

    assert channels.clawbot_credential({"bot_token": ""}) == (TOKEN_B, "auto")


def test_source_labels_are_three_distinct_strings(monkeypatch):
    """界面要能一眼看出凭证是哪来的——三态，不许合并成两态。"""
    monkeypatch.setattr(channels, "clawbot_discover", lambda *a, **k: (TOKEN_B, ""))

    manual = channels.clawbot_source_label({"bot_token": TOKEN_A})
    auto = channels.clawbot_source_label({"bot_token": ""})
    monkeypatch.setattr(channels, "clawbot_discover", lambda *a, **k: ("", "没找到"))
    none = channels.clawbot_source_label({"bot_token": ""})

    assert manual == "手动填写"
    assert auto.startswith("自动探测")
    assert "bbbb2222" in auto, "自动探测要显示 bot_id 前 8 位，用户才知道探到的是哪一个"
    assert none == "未配置"
    assert len({manual, auto, none}) == 3


# ---------------------------------------------------------------- 发送


def _capture_send(monkeypatch, responses):
    """把 `_post_json` 换成按顺序返回 responses 的假实现，返回收到的 body 列表。"""
    bodies = []
    queue = list(responses)

    def _fake(url, body, timeout=channels.SEND_TIMEOUT, headers=None):
        bodies.append((url, body, headers))
        return queue.pop(0) if queue else (200, "{}")

    monkeypatch.setattr(channels, "_post_json", _fake)
    return bodies


def test_send_clawbot_body_shape_and_headers(monkeypatch):
    """端点、鉴权头、`msg.item_list` 的纯文本结构——三样都不能错。"""
    bodies = _capture_send(monkeypatch, [(200, "{}")])

    ok, msg = channels.send_one(
        {"type": "clawbot", "bot_token": TOKEN_A}, "标题", "正文", {}, 7
    )

    assert ok is True, msg
    url, body, headers = bodies[0]
    assert url.endswith("/ilink/bot/sendmessage")
    assert headers["Authorization"] == f"Bearer {TOKEN_A}"
    assert headers["AuthorizationType"] == "ilink_bot_token"
    assert headers["X-WECHAT-UIN"], "X-WECHAT-UIN 必须每次都有（防重放）"
    assert body["msg"]["message_type"] == 2
    assert body["msg"]["item_list"][0] == {
        "type": 1, "text_item": {"text": "标题\n\n正文"}
    }
    assert body["base_info"]["channel_version"]


def test_send_clawbot_retries_without_context_token_on_ret_minus_2(monkeypatch):
    """`ret=-2` = 会话不热。去掉 context_token 再试一次，这是任务书 §3.4.5。"""
    bodies = _capture_send(
        monkeypatch, [(200, json.dumps({"ret": -2})), (200, "{}")]
    )

    ok, msg = channels.send_one(
        {"type": "clawbot", "bot_token": TOKEN_A, "context_token": "ctx-abc"},
        "标题", "正文", {}, 7,
    )

    assert ok is True, msg
    assert len(bodies) == 2, "第一次失败后必须重试一次"
    assert bodies[0][1]["msg"]["context_token"] == "ctx-abc"
    assert "context_token" not in bodies[1][1]["msg"], "第二次必须去掉 context_token"


def test_send_clawbot_retries_on_errcode_minus_14(monkeypatch):
    bodies = _capture_send(
        monkeypatch, [(200, json.dumps({"errcode": -14})), (200, "{}")]
    )

    ok, _ = channels.send_one(
        {"type": "clawbot", "bot_token": TOKEN_A, "context_token": "ctx"}, "t", "c", {}, 7
    )

    assert ok is True
    assert len(bodies) == 2


def test_send_clawbot_no_retry_when_first_call_succeeds(monkeypatch):
    """成功就一次，别为了「稳」多发一条——微信里会看到两条一样的。"""
    bodies = _capture_send(monkeypatch, [(200, "{}")])

    ok, _ = channels.send_one({"type": "clawbot", "bot_token": TOKEN_A}, "t", "c", {}, 7)

    assert ok is True
    assert len(bodies) == 1


def test_send_clawbot_second_failure_is_reported_verbatim(monkeypatch):
    """两次都失败就如实说，别把第二次的错误吞掉。"""
    _capture_send(
        monkeypatch,
        [(200, json.dumps({"ret": -2, "errmsg": "no active session"})),
         (200, json.dumps({"ret": -2, "errmsg": "no active session"}))],
    )

    ok, msg = channels.send_one({"type": "clawbot", "bot_token": TOKEN_A}, "t", "c", {}, 7)

    assert ok is False
    assert "no active session" in msg


def test_send_clawbot_never_leaks_the_token(monkeypatch):
    """服务端把 token 回显在错误里也不能原样写进日志——只留前 8 位。"""
    _capture_send(
        monkeypatch,
        [(200, json.dumps({"ret": -1, "errmsg": f"bad token {TOKEN_A}"})),
         (200, json.dumps({"ret": -1, "errmsg": f"bad token {TOKEN_A}"}))],
    )

    ok, msg = channels.send_one({"type": "clawbot", "bot_token": TOKEN_A}, "t", "c", {}, 7)

    assert ok is False
    assert TOKEN_A not in msg, f"完整 token 进了错误信息：{msg}"
    assert TOKEN_A[:8] in msg, "至少留前 8 位，否则用户不知道该修哪一条"


def test_send_clawbot_without_any_credential_says_how_to_fix(monkeypatch):
    """两级都拿不到 → 告诉用户「去 WorkBuddy 里绑微信」，不是干巴巴的失败。"""
    monkeypatch.setattr(channels, "clawbot_discover", lambda *a, **k: ("", "没找到"))

    ok, msg = channels.send_one({"type": "clawbot", "bot_token": ""}, "t", "c", {}, 7)

    assert ok is False
    assert "绑定微信" in msg or "bot_token" in msg


@pytest.mark.parametrize(
    "make_state, hint",
    [
        (lambda d: None, "没找到"),                       # ① 目录不存在
        (lambda d: (d / "x_im.bot.cursor.json").write_text("{坏", encoding="utf-8"), "JSON"),
        (                                                  # ③ base64 坏
            lambda d: (d / "x_im.bot.cursor.json").write_text(
                json.dumps({"get_updates_buf": "这不是 base64!!"}), encoding="utf-8"
            ),
            "base64",
        ),
        (                                                  # ④ 解得开但没有 token
            lambda d: (d / "x_im.bot.cursor.json").write_text(
                json.dumps({"get_updates_buf": base64.b64encode(b'{"a":1}').decode()}),
                encoding="utf-8",
            ),
            "bot_token",
        ),
    ],
)
def test_send_clawbot_survives_every_discovery_failure(
    monkeypatch, tmp_path, make_state, hint
):
    """四种探测失败都走**发送**这条路验一遍：必须 `(False, 人话)`，不许抛。

    只测 `clawbot_discover` 不够——它跑在 `send_one` 的热路径上，
    真正要保证的是「整条通知不因为探测炸掉」。
    """
    state = tmp_path / "weixin"
    state.mkdir()
    make_state(state)
    monkeypatch.setattr(channels, "CLAWBOT_STATE_DIR", state)

    ok, msg = channels.send_one({"type": "clawbot", "bot_token": ""}, "t", "c", {}, 7)

    assert ok is False
    assert hint in msg, f"没给出可读原因：{msg}"
    assert "Traceback" not in msg and "Error" not in msg, msg


# ------------------------------------------------- 协议对齐（2026-10-31 从宿主读出）
#
# 这三条守的是「和宿主 WeixinApi 逐字对齐」。宿主一改，这三条先红，
# 不用等用户发现通道发不出去。


def test_channel_version_matches_the_host_client():
    """版本号必须是宿主那个串。

    服务端拿它做协议协商，`1.0.2` 这种「看着像版本号」的值会被直接拒
    （实测 `ret:-1`，0.2 秒返回，压根没进长轮询）。
    """
    assert channels.CLAWBOT_CHANNEL_VERSION == "workbuddy-desktop-1.0.0"

    body = {"msg": {"to_user_id": "x"}, "base_info": {"channel_version": channels.CLAWBOT_CHANNEL_VERSION}}
    assert "workbuddy-desktop-1.0.0" in json.dumps(body)


def test_wechat_uin_is_base64_of_the_decimal_string(monkeypatch):
    """`X-WECHAT-UIN` = 十进制字符串的 base64，**不是** 4 个原始字节的 base64。

    宿主原文：`Buffer.from(String(e), 'utf-8').toString('base64')`。
    两种写法长度差一倍，一眼能分开：十进制串 base64 是 16 字符，
    4 字节 base64 只有 8 字符。
    """
    monkeypatch.setattr(channels.random, "getrandbits", lambda n: 1234567890)

    headers = channels._clawbot_headers("tok")

    expected = base64.b64encode(b"1234567890").decode()
    assert headers["X-WECHAT-UIN"] == expected
    assert len(headers["X-WECHAT-UIN"]) == 16, "4 字节 base64 只有 8 字符，16 才说明是十进制串"


def test_uin_is_regenerated_per_request(monkeypatch):
    """每次请求换一个 UIN：服务端拿它做重放判断，复用会被当重复消息丢掉。"""
    seq = iter([111111111, 222222222])
    monkeypatch.setattr(channels.random, "getrandbits", lambda n: next(seq))

    a = channels._clawbot_headers("tok")["X-WECHAT-UIN"]
    b = channels._clawbot_headers("tok")["X-WECHAT-UIN"]

    assert a == base64.b64encode(b"111111111").decode()
    assert b == base64.b64encode(b"222222222").decode()
    assert a != b


@pytest.mark.parametrize("ctype", ["clawbot"])
def test_clawbot_is_registered_everywhere(ctype):
    """注册表三处齐了才算「加进通道」：元数据 / 分组 / 必填项。

    `REQUIRED` 必须是**空元组**：自动探测能拿到凭证，写成 `("bot_token",)`
    会让「探测成功」也显示成「待配置」。
    """
    assert ctype in channels.CHANNEL_META
    assert any(ctype in types for _, types in channels.CHANNEL_GROUPS)
    assert channels.REQUIRED[ctype] == ()
    assert channels.is_ready(ctype, {"bot_token": ""}) is True
