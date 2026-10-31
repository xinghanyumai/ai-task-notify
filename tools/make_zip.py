#!/usr/bin/env python
"""把 `WBNotify/` 打成可分发的 zip。

    python tools/make_zip.py

产物：项目根目录的 `WBNotify-v<version>-win64.zip`，里面只有**一个顶层目录**
`WBNotify/`，对方解压即用。

--------------------------------------------------------------------------
为什么排除清单是安全项，不是优化项
--------------------------------------------------------------------------
`WBNotify/config.json` 里是用户的微信 appid / appsecret / openid。这个目录
只要被手工跑过一次，`config.json`、`state/`（含 access_token）、`logs/`
（含通知正文）就会出现在里面。**直接 zip 出去等于把推送权限送人。**

所以这里做两道：
1. 压之前先查——这四类只要还在 `WBNotify/` 里就**拒绝打包**，不「帮你删」。
   删用户的凭证这种事，脚本没资格替人决定。
2. 压完再开一次 zip 逐条核——零命中才算过。第 1 道挡的是「源头脏」，
   第 2 道挡的是「排除规则写漏了一个通配」。

--------------------------------------------------------------------------
压缩方式
--------------------------------------------------------------------------
`ZIP_DEFLATED`，不用 `ZIP_LZMA`：后者体积小一点，但解压慢，而且部分老解压
工具（7-Zip 老版本、Windows 自带资源管理器在某些版本上）不认。
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "WBNotify"

# 绝不能出现在包里的四类。前三条是**目录/文件**，最后一条是**后缀**。
EXCLUDE_NAMES = {"config.json", "state", "logs"}
EXCLUDE_SUFFIXES = {".log"}
EXCLUDE_PREFIXES = ("config.json.",)   # config.json.broken-* / config.json.tmp / config.json.w14-backup


def version() -> str:
    """从 `wbn/__init__.py` 里抠版本号。

    不 `import wbn`：那会把整个包拖进来（连带 config 的路径解析），而这个
    脚本只需要一个字符串。`__version__ = "x.y.z"` 是唯一真相，正则足够。
    """
    text = (ROOT / "wbn" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        sys.exit("✘ wbn/__init__.py 里没找到 __version__")
    return m.group(1)


def is_excluded(rel: Path) -> bool:
    """按**相对 WBNotify/ 的路径**判断，任一层命中就排除。"""
    for part in rel.parts:
        if part in EXCLUDE_NAMES:
            return True
        if part.startswith(EXCLUDE_PREFIXES):
            return True
    return rel.suffix.lower() in EXCLUDE_SUFFIXES


def dirty_files() -> list[Path]:
    """`WBNotify/` 里现存的、绝不该进包的东西。"""
    if not SRC.is_dir():
        return []
    return [p for p in SRC.rglob("*") if is_excluded(p.relative_to(SRC))]


def main() -> int:
    ap = argparse.ArgumentParser(description="把 WBNotify/ 打成可分发的 zip")
    ap.add_argument("--out", default="", help="输出路径（默认 WBNotify-v<版本>-win64.zip）")
    args = ap.parse_args()

    if not SRC.is_dir():
        sys.exit(f"✘ 没有 {SRC}——先跑 python tools/build_exe.py")

    # ---- 第一道：源头脏就拒绝，不替用户删凭证 ----------------------------
    dirty = dirty_files()
    if dirty:
        print("✘ 拒绝打包：WBNotify/ 里有不该分发的东西。", flush=True)
        for p in dirty[:12]:
            print(f"    {p.relative_to(ROOT).as_posix()}  ({p.stat().st_size} B)")
        if len(dirty) > 12:
            print(f"    …还有 {len(dirty) - 12} 个")
        print(
            "\n  这些是**你自己的凭证 / 通知记录**，zip 出去等于把推送权限送人。\n"
            "  先把它们挪走（或删掉）再打包：\n"
            f"    move {SRC / 'config.json'} {ROOT / 'config.json'}\n"
            "  也可以用一次干净构建来打：删掉整个 WBNotify/ 重跑 build_exe.py。",
            flush=True,
        )
        return 1

    ver = version()
    out = Path(args.out) if args.out else ROOT / f"WBNotify-v{ver}-win64.zip"
    if out.exists():
        out.unlink()

    entries = 0
    raw = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(SRC.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(SRC)
            if is_excluded(rel):
                continue
            # 顶层目录名固定 WBNotify/（不是 `通知/`，也不是变量）
            zf.write(p, arcname=(Path("WBNotify") / rel).as_posix())
            entries += 1
            raw += p.stat().st_size

    # ---- 第二道：开回来看一眼，零命中才算过 ------------------------------
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()

    tops = {n.split("/")[0] for n in names}
    bad = [
        n for n in names
        if n.startswith("WBNotify/") and is_excluded(Path(n).relative_to("WBNotify"))
    ]

    problems = []
    if tops != {"WBNotify"}:
        problems.append(f"顶层目录不是唯一的 WBNotify/，实际：{sorted(tops)}")
    if bad:
        problems.append(f"zip 里有 {len(bad)} 个排除项：{bad[:5]}")
    if problems:
        out.unlink(missing_ok=True)
        sys.exit("✘ " + "\n✘ ".join(problems))

    print(f"产物：{out.name}")
    print(f"  条目数    ：{entries}")
    print(f"  解压后体积：{raw / (1024 * 1024):.1f} MB")
    print(f"  zip 体积  ：{out.stat().st_size / (1024 * 1024):.1f} MB")
    print("  ✔ 顶层只有一个 WBNotify/，config.json / state/ / logs/ / *.log 零命中")
    return 0


if __name__ == "__main__":
    sys.exit(main())
