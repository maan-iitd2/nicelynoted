"""Draws the NicelyNoted logo: a sound wave on the left that turns into lines of notes on
the right. One shape list feeds both the SVG and the PNGs, so they cannot drift apart.

    python assets/make_logo.py        # writes logo.svg and icon-{16,48,128,512}.png here
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

OUT = Path(__file__).parent
C1, C2 = (79, 70, 229), (124, 58, 237)   # indigo -> violet, top-left to bottom-right
# (x, y, w, h, opacity) on a 128 grid; every shape is a pill (radius = w/2 or h/2)
SHAPES = [
    (20, 50, 9, 28, 1.0), (34, 36, 9, 56, 1.0), (48, 26, 9, 76, 1.0), (62, 44, 9, 40, 1.0),  # wave
    (84, 41, 24, 9, 0.95), (84, 59.5, 24, 9, 0.95), (84, 78, 15, 9, 0.95),                   # notes
]
SIZE, RADIUS = 128, 30


def svg() -> str:
    pills = "\n  ".join(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{min(w, h) / 2}" fill="#fff" fill-opacity="{o}"/>'
        for x, y, w, h, o in SHAPES)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" width="256" height="256">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="rgb{C1}"/><stop offset="1" stop-color="rgb{C2}"/></linearGradient></defs>
  <rect width="{SIZE}" height="{SIZE}" rx="{RADIUS}" fill="url(#g)"/>
  {pills}
</svg>
'''


def png(px: int) -> Image.Image:
    s = px * 4   # draw big, shrink: Pillow has no anti-aliasing of its own
    k = s / SIZE
    t = (np.add.outer(np.arange(s), np.arange(s)) / (2 * s - 2))[..., None]
    grad = (np.array(C1) * (1 - t) + np.array(C2) * t).astype(np.uint8)
    img = Image.fromarray(grad, "RGB").convert("RGBA")
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), RADIUS * k, fill=255)
    img.putalpha(mask)
    for x, y, w, h, o in SHAPES:
        layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle(
            (x * k, y * k, (x + w) * k, (y + h) * k), min(w, h) / 2 * k, fill=(255, 255, 255, int(255 * o)))
        img = Image.alpha_composite(img, layer)
    return img.resize((px, px), Image.LANCZOS)


if __name__ == "__main__":
    (OUT / "logo.svg").write_text(svg(), encoding="utf-8")
    for px in (16, 48, 128, 512):
        png(px).save(OUT / f"icon-{px}.png")
    assert png(48).getpixel((0, 0))[3] == 0, "corners must be transparent"
    print("wrote logo.svg + icon-{16,48,128,512}.png")
