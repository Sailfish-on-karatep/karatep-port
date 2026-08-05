#!/usr/bin/env python3
"""Generate hybris-boot's bootsplash.gz for this device's framebuffer.

hybris-boot displays the splash with a raw framebuffer dump:

    zcat /bootsplash.gz > /dev/fb0

so the file must match the panel exactly -- there is no image format, no
scaling and no error if it does not. Two things are easy to get wrong:

  * STRIDE, not width. The line length is /sys/class/graphics/fb0/stride, which
    on karatep is 4352 bytes = 1088 pixels, while only 1080 are visible. Writing
    1080*4 bytes per line shears the image diagonally.
  * virtual_size is double-buffered (1080,3840 = 1920*2). Only the first 1920
    lines are on screen.

Read the real values from the device rather than trusting these defaults:

    cat /sys/class/graphics/fb0/{virtual_size,bits_per_pixel,stride}

The artwork is deliberately WHITE ON BLACK. Qualcomm MDSS may be BGRA or RGBA
and we cannot tell without looking at the panel; a greyscale image is identical
either way, so the splash cannot come out colour-swapped.

Usage:
    make-bootsplash.py [-o bootsplash.gz] [--width 1080] [--height 1920]
                       [--stride 4352] [--preview splash.png]
"""
import argparse
import gzip
import math

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


def load_font(size):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_sail(draw, cx, cy, r):
    """A simple sail: a filled curved triangle with a mast, in white."""
    # Mast
    draw.rectangle([cx - r * 0.04, cy - r, cx + r * 0.04, cy + r * 0.9],
                   fill=(255, 255, 255))
    # Sail: straight leading edge on the mast, curved leech
    pts = [(cx, cy - r)]
    for i in range(65):
        t = i / 64.0
        # quadratic bezier from masthead to foot, bulging right
        x = (1 - t) ** 2 * cx + 2 * (1 - t) * t * (cx + r * 1.25) + t ** 2 * cx
        y = (1 - t) ** 2 * (cy - r) + 2 * (1 - t) * t * cy + t ** 2 * (cy + r * 0.72)
        pts.append((x, y))
    draw.polygon(pts, fill=(255, 255, 255))
    # Waterline
    draw.rectangle([cx - r * 1.05, cy + r * 0.95, cx + r * 1.35, cy + r * 1.02],
                   fill=(255, 255, 255))


def build(width, height, stride, preview=None):
    img = Image.new("RGB", (width, height), (0, 0, 0))
    d = ImageDraw.Draw(img)

    r = int(min(width, height) * 0.17)
    draw_sail(d, width // 2, int(height * 0.42), r)

    title = load_font(max(24, width // 13))
    text = "Sailfish OS"
    box = d.textbbox((0, 0), text, font=title)
    d.text(((width - (box[2] - box[0])) // 2 - box[0], int(height * 0.63)),
           text, font=title, fill=(255, 255, 255))

    if preview:
        img.save(preview)

    # Raw framebuffer: 32bpp little-endian, padded to `stride` bytes per line.
    # Byte order is B,G,R,X on MDSS; irrelevant here because the art is grey.
    px = img.load()
    pad = stride - width * 4
    if pad < 0:
        raise SystemExit("stride %d is smaller than width*4 (%d)" % (stride, width * 4))
    out = bytearray()
    for y in range(height):
        row = bytearray()
        for x in range(width):
            r_, g_, b_ = px[x, y]
            row += bytes((b_, g_, r_, 0xFF))
        out += row + bytes(pad)
    return bytes(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-o", "--output", default="bootsplash.gz")
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument("--stride", type=int, default=4352)
    p.add_argument("--buffers", type=int, default=2,
                   help="how many screen-sized buffers the framebuffer holds "
                        "(virtual_size height / visible height). The image is "
                        "repeated into each one.")
    p.add_argument("--preview")
    a = p.parse_args()

    frame = build(a.width, a.height, a.stride, a.preview)

    # virtual_size on karatep is 1080,3840 -- two 1920-line buffers. hybris-boot
    # writes the splash with a plain `zcat > /dev/fb0`, which always lands at
    # offset 0, so if the panel is currently showing the SECOND buffer the image
    # goes to the off-screen one and the display just stays black. Repeat the
    # frame into every buffer so it is visible whichever one is active.
    raw = frame * a.buffers

    expected = a.stride * a.height * a.buffers
    assert len(raw) == expected, (len(raw), expected)
    with gzip.open(a.output, "wb", compresslevel=9) as f:
        f.write(raw)
    print("%s: %d raw bytes (%dx%d, stride %d, %d buffer(s))"
          % (a.output, len(raw), a.width, a.height, a.stride, a.buffers))


if __name__ == "__main__":
    main()
