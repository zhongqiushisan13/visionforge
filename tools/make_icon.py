"""生成应用图标（.ico / .png），不依赖外部素材。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
S = 1024


def build() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # 底：深海蓝竖向渐变
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        gd.line([(0, y), (S, y)], fill=(int(5 + t * 6), int(16 + t * 24), int(34 + t * 56), 255))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([26, 26, S - 26, S - 26], radius=210, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img, "RGBA")

    # 星点
    stars = [(250, 250, 7), (790, 300, 5), (300, 760, 4), (720, 800, 8), (520, 190, 4)]
    for x, y, r in stars:
        d.ellipse([x - r, y - r, x + r, y + r], fill=(180, 225, 255, 190))

    # 轨道弧
    d.arc([210, 210, 814, 814], start=195, end=345, fill=(77, 163, 255, 220), width=16)
    d.arc([210, 210, 814, 814], start=15, end=165, fill=(77, 163, 255, 90), width=10)

    # 光晕
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([300, 300, 724, 724], fill=(77, 163, 255, 55))
    img = Image.alpha_composite(img, glow.filter(__import__("PIL.ImageFilter", fromlist=["GaussianBlur"]).GaussianBlur(70)))
    d = ImageDraw.Draw(img, "RGBA")

    # 播放三角（带一点圆角感）
    d.polygon([(404, 330), (404, 694), (734, 512)], fill=(110, 231, 255, 255))
    d.line([(404, 330), (404, 694)], fill=(180, 245, 255, 220), width=10)

    # 外框
    d.rounded_rectangle([26, 26, S - 26, S - 26], radius=210, outline=(110, 190, 255, 90), width=6)
    return img


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    img = build()
    img.save(ASSETS / "visionforge.png")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(ASSETS / "visionforge.ico", sizes=sizes)
    print("icon ->", ASSETS / "visionforge.ico")


if __name__ == "__main__":
    main()
