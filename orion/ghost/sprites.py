"""Drawing ORION's ghost cursor and ghost-window frames (Pillow only, so it renders and tests anywhere).

Black, white and grey: a white arrow with a dark rim reads on any background. The "phase" look comes from two
faint copies of the shape offset sideways, like an image that hasn't quite settled, plus scanlines. A tiny star
spirit (ORION's avatar) floats beside the cursor so you always know whose it is.
"""

from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

CURSOR_BOX = 132  # the cursor window is this square; the hotspot sits at HOT
HOT = (44, 40)
ARROW = [(0, 0), (0, 26), (7, 20), (12, 31), (17, 29), (12, 18), (21, 18)]  # classic pointer, tip at (0, 0)
INK = (245, 245, 245)
RIM = (18, 18, 18)


def _font(size: int):
    for name in ("segoeuisb.ttf", "segoeui.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _star(d: ImageDraw.ImageDraw, x: float, y: float, r: float, fill) -> None:
    k = r * 0.3
    d.polygon([(x, y - r), (x + k, y - k), (x + r, y), (x + k, y + k), (x, y + r), (x - k, y + k), (x - r, y), (x - k, y - k)],
              fill=fill)


def spirit(size: int = 22, alpha: int = 230, blink: bool = False) -> Image.Image:
    """ORION's avatar in miniature: see-through ghost body, two star eyes, Orion's belt on the brow."""
    s = size * 4  # draw big, shrink for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = s * 0.3
    cx, cy = s / 2, s * 0.42
    hem = cy + r * 1.35
    body = [(cx - r, cy)]
    body += [(cx + r * math.cos(a), cy - r * math.sin(a)) for a in [math.pi - i * math.pi / 24 for i in range(25)]]
    body += [(cx + r, hem)]
    for k in range(6, -1, -1):  # wavy hem
        body.append((cx - r + 2 * r * k / 6, hem + (r * 0.16 if k % 2 else 0)))
    d.polygon(body, fill=(255, 255, 255, int(alpha * 0.32)))
    d.line(body + [body[0]], fill=(16, 16, 16, int(alpha * 0.7)), width=max(4, s // 12))  # dark rim: shows on white
    d.line(body + [body[0]], fill=(255, 255, 255, alpha), width=max(2, s // 28))
    eye = s * 0.075
    for side in (-1, 1):
        if blink:
            d.line([(cx + side * r * 0.42 - eye, cy + r * 0.05), (cx + side * r * 0.42 + eye, cy + r * 0.05)],
                   fill=(255, 255, 255, alpha), width=max(2, s // 30))
        else:
            _star(d, cx + side * r * 0.42, cy + r * 0.05, eye, (255, 255, 255, alpha))
    for i in (-1, 0, 1):
        _star(d, cx + i * r * 0.34, cy - r * 0.55 - i * r * 0.06, s * 0.03, (255, 255, 255, alpha))
    return img.resize((size, size), Image.LANCZOS)


def cursor(trail: list[tuple[float, float]] | None = None, ripple: float = -1.0, t: float = 0.0,
           working: bool = True) -> Image.Image:
    """The ghost cursor sprite. ``trail`` holds recent hotspot positions relative to the current one (newest last),
    ``ripple`` 0..1 draws a click ring, ``t`` (seconds) drives the phase flicker."""
    img = Image.new("RGBA", (CURSOR_BOX, CURSOR_BOX), (0, 0, 0, 0))
    hx, hy = HOT
    d = ImageDraw.Draw(img)

    for i, (tx, ty) in enumerate(trail or []):
        a = int(70 * (i + 1) / max(1, len(trail or [])))
        x, y = hx + tx, hy + ty
        if 0 <= x < CURSOR_BOX and 0 <= y < CURSOR_BOX:
            d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(255, 255, 255, a))

    if ripple >= 0:
        rr = 6 + ripple * 22
        a = int(200 * (1 - ripple))
        d.ellipse([hx - rr, hy - rr, hx + rr, hy + rr], outline=(255, 255, 255, a), width=2)
        d.ellipse([hx - rr - 1, hy - rr - 1, hx + rr + 1, hy + rr + 1], outline=(0, 0, 0, a // 3), width=1)

    arrow = [(hx + x, hy + y) for x, y in ARROW]
    # Soft halo so it shows on light and dark backgrounds alike.
    halo = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(halo).polygon(arrow, fill=(255, 255, 255, 120))
    halo = halo.filter(ImageFilter.GaussianBlur(5))
    img = Image.alpha_composite(img, halo)
    d = ImageDraw.Draw(img)

    # Phase echoes: two faint copies nudged sideways.
    shift = 2 + 1.5 * math.sin(t * 7)
    for dx, a in ((-shift, 55), (shift, 40)):
        d.polygon([(x + dx, y) for x, y in arrow], fill=(255, 255, 255, a))
    d.polygon(arrow, fill=INK + (150,), outline=RIM + (210,))
    d.line(arrow + [arrow[0]], fill=RIM + (210,), width=2)
    # Scanlines through the body only: the see-through, "not quite solid" look.
    lines = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(lines)
    for y in range(hy + 2, hy + 30, 3):
        ld.line([(hx, y), (hx + 22, y)], fill=(0, 0, 0, 45))
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).polygon(arrow, fill=255)
    img.paste(Image.alpha_composite(img, lines), (0, 0), mask)
    d = ImageDraw.Draw(img)
    d.line(arrow + [arrow[0]], fill=RIM + (220,), width=2)

    if working:
        sp = spirit(24, blink=int(t * 10) % 37 == 0)
        bob = int(2 * math.sin(t * 3))
        img.alpha_composite(sp, (hx + 20, hy + 26 + bob))
    return img


def frame(width: int, height: int, title: str, t: float = 0.0, pad: int = 10, label_h: int = 26) -> Image.Image:
    """The phased glow around a ghost window, with its "ORION · ghost" tag above the top-left corner. The window
    itself sits at (pad, pad + label_h) inside this image; the middle stays empty so the window shows through."""
    W, H = width + 2 * pad, height + 2 * pad + label_h
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    box = [pad - 2, pad + label_h - 2, pad + width + 1, pad + label_h + height + 1]
    g.rounded_rectangle(box, radius=10, outline=(255, 255, 255, 150), width=4)
    glow = glow.filter(ImageFilter.GaussianBlur(5))
    img.alpha_composite(glow)
    d = ImageDraw.Draw(img)
    breathe = 0.75 + 0.25 * math.sin(t * 2.2)
    for off, a in ((0, int(210 * breathe)), (3, 60), (-3, 45)):  # main edge + two phase echoes
        d.rounded_rectangle([box[0] + off, box[1], box[2] + off, box[3]], radius=10, outline=(235, 235, 235, a), width=1)
    # Dashes travelling along the top edge, so it reads as alive even when the app is still.
    step = 18
    start = int(t * 40) % step
    for x in range(box[0] + 12 + start, box[2] - 12, step):
        d.line([(x, box[1]), (x + 7, box[1])], fill=(255, 255, 255, 200), width=2)

    font = _font(13)
    text = f"ORION · ghost   {title}"[:64]
    tw = int(d.textlength(text, font=font)) if hasattr(d, "textlength") else 7 * len(text)
    tag = [pad, 2, pad + tw + 40, label_h - 2]
    d.rounded_rectangle(tag, radius=11, fill=(14, 14, 14, 215), outline=(255, 255, 255, 120))
    img.alpha_composite(spirit(18), (pad + 6, 4))
    d.text((pad + 30, 5), text, font=font, fill=(240, 240, 240, 255))
    return img


def activity(text: str, t: float = 0.0) -> Image.Image:
    """The pill shown on a ghost window while ORION works in it: his spirit, what he's doing, three breathing dots.
    Black glass with a white edge, so it reads on any app."""
    font = _font(15)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tw = int(probe.textlength(text, font=font)) if hasattr(probe, "textlength") else 8 * len(text)
    h = 38
    w = 14 + 24 + 10 + tw + 12 + 30 + 14
    img = Image.new("RGBA", (w + 16, h + 16), (0, 0, 0, 0))
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle([8, 8, 8 + w, 8 + h], radius=h // 2, fill=(255, 255, 255, 70))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(6)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 8, 8 + w, 8 + h], radius=h // 2, fill=(12, 12, 12, 235), outline=(255, 255, 255, 150))
    img.alpha_composite(spirit(24, blink=int(t * 2) % 7 == 0), (8 + 14, 8 + (h - 24) // 2))
    d.text((8 + 14 + 24 + 10, 8 + (h - 18) // 2), text, font=font, fill=(245, 245, 245, 255))
    x0 = 8 + 14 + 24 + 10 + tw + 12
    for i in range(3):
        a = int(110 + 145 * (0.5 + 0.5 * math.sin(t * 5 - i * 0.9)))
        cx, cy = x0 + 5 + i * 10, 8 + h // 2
        d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(255, 255, 255, a))
    return img


def bgra_premultiplied(img: Image.Image) -> bytes:
    """Bytes for UpdateLayeredWindow: 32-bit BGRA with colour premultiplied by alpha."""
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    r, g, b = (ImageChops.multiply(c, a) for c in (r, g, b))
    return Image.merge("RGBA", (b, g, r, a)).tobytes()
