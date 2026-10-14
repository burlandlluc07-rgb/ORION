"""Render the ORION arc-reactor icon (PNG + multi-size ICO) with Pillow."""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "orion" / "assets"
S = 1024


def render() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = S / 2
    d.rounded_rectangle([40, 40, S - 40, S - 40], radius=220, fill=(0, 0, 0, 255))
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse([c - 300, c - 300, c + 300, c + 300], outline=(255, 255, 255, 110), width=60)
    glow = glow.filter(ImageFilter.GaussianBlur(40))
    img.alpha_composite(glow)
    d.ellipse([c - 300, c - 300, c + 300, c + 300], outline=(235, 235, 235, 255), width=26)
    for i in range(10):  # segmented inner ring
        a0 = i * 36 + 4
        d.arc([c - 225, c - 225, c + 225, c + 225], a0, a0 + 26, fill=(170, 170, 170, 255), width=34)
    for i in range(3):  # orbit triangle accents
        a = math.radians(i * 120 - 90)
        x, y = c + 300 * math.cos(a), c + 300 * math.sin(a)
        d.ellipse([x - 34, y - 34, x + 34, y + 34], fill=(255, 255, 255, 255))
    core = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(core).ellipse([c - 120, c - 120, c + 120, c + 120], fill=(230, 230, 230, 255))
    img.alpha_composite(core.filter(ImageFilter.GaussianBlur(30)))
    d.ellipse([c - 92, c - 92, c + 92, c + 92], fill=(255, 255, 255, 255))
    return img


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    im = render()
    im.resize((256, 256), Image.LANCZOS).save(OUT / "orion.png")
    im.save(OUT / "orion.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icons written to", OUT)
