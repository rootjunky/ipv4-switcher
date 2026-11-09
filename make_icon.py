"""Generate ipv4.ico (and ipv4.png) for IPv4 Switcher.

Requires Pillow:  pip install pillow   then   python make_icon.py
Design: dark rounded tile, four glowing nodes (one per IPv4 octet) linked to a
central hub, plus an "IPv4" label at the larger sizes.
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024  # master canvas; each icon size is drawn at 4x and downsampled

BG_TOP, BG_BOTTOM = (17, 24, 39), (30, 41, 74)
CYAN, BLUE = (34, 211, 238), (59, 130, 246)
WHITE = (255, 255, 255)

FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


def font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def gradient(size, top, bottom, diagonal=False):
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(h):
        for x in range(w):
            t = ((x + y) / (w + h)) if diagonal else y / max(h - 1, 1)
            px[x, y] = lerp(top, bottom, t)
    return img


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size - 1, size - 1), radius, fill=255)
    return m


def render(px):
    """Render one icon size. Small sizes drop the label and thicken strokes."""
    small = px <= 40
    n = px * 4
    k = n / 256  # design units: a 256-wide grid

    # Tile background with a subtle vertical gradient and a 1-unit inner border.
    tile = gradient((n, n), BG_TOP, BG_BOTTOM).convert("RGBA")
    border = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle(
        (k * 2, k * 2, n - k * 2, n - k * 2), int(52 * k),
        outline=(96, 165, 250, 110), width=max(int(3 * k), 1))
    tile.alpha_composite(border)

    # Network glyph geometry.
    cx = n / 2
    cy = n * (0.50 if small else 0.40)
    r = n * (0.32 if small else 0.255)
    nodes = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    line_w = int((22 if small else 13) * k)
    node_r = (30 if small else 21) * k
    hub_r = (36 if small else 27) * k

    glyph = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    g = ImageDraw.Draw(glyph)
    # outer ring linking the four octets, spokes to the hub
    for i in range(4):
        g.line([nodes[i], nodes[(i + 1) % 4]], fill=BLUE + (255,), width=line_w)
    for x, y in nodes:
        g.line([(cx, cy), (x, y)], fill=CYAN + (255,), width=line_w)
    for x, y in nodes:
        g.ellipse((x - node_r, y - node_r, x + node_r, y + node_r), fill=CYAN + (255,))
        ir = node_r * 0.45
        g.ellipse((x - ir, y - ir, x + ir, y + ir), fill=WHITE + (255,))
    g.ellipse((cx - hub_r, cy - hub_r, cx + hub_r, cy + hub_r), fill=WHITE + (255,))

    if not small:
        # soft glow behind the glyph
        glow = glyph.copy().filter(ImageFilter.GaussianBlur(10 * k))
        alpha = glow.getchannel("A").point(lambda a: min(255, int(a * 1.4)))
        glow.putalpha(alpha)
        tile.alpha_composite(glow)
    tile.alpha_composite(glyph)

    if not small:
        d = ImageDraw.Draw(tile)
        f = font(int(58 * k))
        text = "IPv4"
        box = d.textbbox((0, 0), text, font=f)
        tw, th = box[2] - box[0], box[3] - box[1]
        tx, ty = (n - tw) / 2 - box[0], n * 0.79 - th / 2 - box[1]
        # gradient-filled text
        fill = gradient((n, n), CYAN, BLUE, diagonal=True).convert("RGBA")
        tmask = Image.new("L", (n, n), 0)
        ImageDraw.Draw(tmask).text((tx, ty), text, font=f, fill=255)
        shadow = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).text((tx, ty + 3 * k), text, font=f, fill=(0, 0, 0, 150))
        tile.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(3 * k)))
        fill.putalpha(tmask)
        tile.alpha_composite(fill)

    tile.putalpha(rounded_mask(n, int(54 * k)))
    return tile.resize((px, px), Image.LANCZOS)


def main():
    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    images = [render(s) for s in sizes]
    ico = os.path.join(HERE, "ipv4.ico")
    images[-1].save(ico, format="ICO", sizes=[(s, s) for s in sizes],
                    append_images=images[:-1])
    images[-1].save(os.path.join(HERE, "ipv4.png"))
    if "--preview" in sys.argv:
        sheet = Image.new("RGBA", (sum(sizes) + 12 * len(sizes) + 12, 280), (40, 40, 40, 255))
        x = 12
        for s, im in zip(sizes, images):
            sheet.alpha_composite(im, (x, 12))
            x += s + 12
        sheet.save(os.path.join(HERE, "icon_preview.png"))
    print(f"Wrote {ico}")


if __name__ == "__main__":
    main()
