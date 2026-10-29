"""生成多尺寸应用图标 assets/icon.ico。

设计：WorkBuddy 品牌青绿圆角方块 + 纯白铃铛。
- 底色竖直渐变（顶部亮、底部深），与界面品牌色一致
- 铃铛画成实心剪影而不是线性描边：16px 下细线会糊成一团，实心块才认得出
- 每个尺寸按 8 倍超采样绘制后降采样，边缘平滑

小尺寸单独走一档：去掉顶部提环、钟锤与口沿融为一体、整体放大 12%，
否则在 16~24px 下提环变成一个孤立小点、钟锤脱开成一颗"句号"。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icon.ico"

# 品牌色（与 webui/assets/tokens.css 的 --brand 一致）
TOP = (18, 214, 172)     # #12D6AC
BOTTOM = (0, 163, 132)   # #00A384

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
SS = 8                   # 超采样倍数
COMPACT_BELOW = 32       # 小于这个尺寸走简化版


def rounded_mask(size: int, radius_ratio: float) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    r = int(round(size * radius_ratio))
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=255)
    return m


def gradient(size: int) -> Image.Image:
    """竖直渐变，逐行插值。"""
    g = Image.new("RGB", (1, size))
    px = g.load()
    for y in range(size):
        t = (y / max(1, size - 1)) ** 0.85   # 轻微 ease-in，亮色集中在顶部
        px[0, y] = (
            int(TOP[0] + (BOTTOM[0] - TOP[0]) * t),
            int(TOP[1] + (BOTTOM[1] - TOP[1]) * t),
            int(TOP[2] + (BOTTOM[2] - TOP[2]) * t),
        )
    return g.resize((size, size), Image.NEAREST)


def bell_mask(size: int, compact: bool = False) -> Image.Image:
    """白色铃铛剪影。坐标归一化到 0~1，再乘 size。

    compact=True 时整体绕中心放大 12%，并去掉提环 —— 小尺寸下这些细节
    降采样后只会变成脏点，不如让铃身占满视觉面积。
    """
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)

    k = 1.12 if compact else 1.0

    def P(v: float) -> float:
        """归一化坐标 → 像素，同时施加 compact 缩放。"""
        return (0.5 + (v - 0.5) * k) * size

    def R(v: float) -> float:
        """归一化「长度」→ 像素。长度不能走 P，否则会被中心偏移拉成负数。"""
        return v * k * size

    if not compact:
        # 顶部提环：圆角短柱，与圆拱咬合（方角会在 128px+ 露出硬肩，像烟囱）
        d.rounded_rectangle(
            (P(0.448), P(0.092), P(0.552), P(0.208)),
            radius=R(0.032), fill=255,
        )

    # 钟体：上半圆拱（ry > rx，纵向拉长，才像铃铛而不是袋子）
    d.pieslice(
        (P(0.228), P(0.180), P(0.772), P(0.820)),
        start=180, end=360, fill=255,
    )

    # 钟体：直筒段，底部向外扩
    d.polygon(
        [
            (P(0.228), P(0.470)),
            (P(0.772), P(0.470)),
            (P(0.812), P(0.690)),
            (P(0.188), P(0.690)),
        ],
        fill=255,
    )

    # 口沿：比筒身宽，圆角横条
    d.rounded_rectangle(
        (P(0.175), P(0.650), P(0.825), P(0.740)),
        radius=R(0.045), fill=255,
    )

    # 钟锤：与口沿重叠 0.035，保证任何尺寸下都连成一体
    d.ellipse((P(0.428), P(0.706), P(0.572), P(0.852)), fill=255)

    return m


def render(size: int) -> Image.Image:
    n = size * SS
    compact = size < COMPACT_BELOW

    base = gradient(n)
    base.putalpha(rounded_mask(n, 0.200 if compact else 0.225))

    white = Image.new("RGBA", (n, n), (255, 255, 255, 255))
    white.putalpha(bell_mask(n, compact))

    out = Image.alpha_composite(base, white)
    return out.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames = [render(s) for s in SIZES]
    frames[-1].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    frames[-1].save(OUT.with_name("icon_256.png"))

    # 各尺寸拼一张对照图，方便肉眼验收小尺寸效果
    pad, scale = 12, 3
    w = sum(s * scale + pad for s in SIZES) + pad
    h = max(SIZES) * scale + pad * 2
    sheet = Image.new("RGBA", (w, h), (242, 242, 242, 255))
    x = pad
    for s, f in zip(SIZES, frames, strict=False):
        sheet.alpha_composite(f.resize((s * scale, s * scale), Image.NEAREST),
                              (x, (h - s * scale) // 2))
        x += s * scale + pad
    sheet.save(OUT.with_name("icon_sizes.png"))

    chk = Image.open(OUT)
    got = sorted(s for s, _ in chk.info.get("sizes", []))
    print(f"已生成 {OUT}")
    print("尺寸：", got)
    if got != sorted(SIZES):
        raise SystemExit("尺寸不完整，请检查 ICO 写入")


if __name__ == "__main__":
    main()
