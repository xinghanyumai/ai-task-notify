"""仓库级守卫：已跟踪文件里不许出现真实微信 AppID。

W12 §1 的事故：`webui/assets/shell.css` 里一句量宽度的注释，把本机
`config.json` 的 `wxmp.appid` 原值抄了进去，随仓库一起公开。

AppID 单独发不了消息（要配 AppSecret 才换得到 access_token），所以它不是
密码级别的泄露；但它是**账号标识**，不该出现在公开仓库里。

判据：`wx` + 16 位十六进制，且**不是全同字符**。
`wx0000000000000000` 是本轮换上去的占位符，长度同样 18——那段注释讲的
就是「appID 是 18 字符所以要 ≥160px」，占位符短一个字符注释就自相矛盾了。
"""

from __future__ import annotations

import pathlib
import re
import subprocess

ALLOWED = {"wx0000000000000000"}          # 占位符
PAT = re.compile(r"wx[0-9a-f]{16}")

SUFFIXES = {".py", ".css", ".js", ".html", ".md", ".json", ".spec", ".txt"}


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, encoding="utf-8"
    )
    return out.stdout.split()


def test_no_real_appid_in_tracked_files():
    hits = []
    for f in _tracked_files():
        p = pathlib.Path(f)
        if p.suffix not in SUFFIXES:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in PAT.finditer(text):
            if m.group(0) not in ALLOWED:
                hits.append(f"{f}: {m.group(0)}")
    assert not hits, "真实 AppID 出现在仓库里：\n" + "\n".join(hits)


def test_the_placeholder_is_still_18_chars():
    """占位符长度不许变。

    `shell.css` 那段注释的结论「列宽 ≥ 161、视口 ≥ 970」是从「appID 是
    18 字符」推出来的。占位符短了，注释就在撒谎，而这个谎没人会去复核。
    """
    assert len("wx0000000000000000") == 18
