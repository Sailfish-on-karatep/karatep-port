#!/usr/bin/env python3
"""Generate hybris-boot's bootsplash.gz for this device's framebuffer.

The splash is a raw framebuffer dump -- no image format, no scaling, and no
error if it does not fit. On karatep it is displayed by hybris-boot's fbsplash
helper (hybris/hybris-boot/fbsplash.c), which reads it on stdin:

    zcat /bootsplash.gz | /bin/fbsplash

NOT by upstream's `zcat /bootsplash.gz > /dev/fb0`, which on this panel always
fails with ENODEV. See fbsplash.c for why.

Two things about the geometry are easy to get wrong:

  * STRIDE, not width. The line length is /sys/class/graphics/fb0/stride, which
    on karatep is 4352 bytes = 1088 pixels, while only 1080 are visible. Writing
    1080*4 bytes per line shears the image diagonally.
  * virtual_size is double-buffered (1080,3840 = 1920*2). Only the first 1920
    lines are on screen, and fbsplash pans explicitly to that first buffer, so
    ONE screen of image data is all that is needed (--buffers 1, the default).
    The old redirect had to duplicate the frame into both buffers because it
    could not choose which one was live.

Read the real values from the device rather than trusting these defaults:

    cat /sys/class/graphics/fb0/{virtual_size,stride,bits_per_pixel}

...and confirm them against what fbsplash logs to /init.log on the way past:

    fbsplash: fb0 xres=1080 yres=1920 yres_virtual=3840 bpp=32 \
              line_length=4352 smem_len=16711680

INK
---
assets/sfosboot.png is the official Sailfish wordmark: BLACK artwork on a
TRANSPARENT background. Compositing that onto a black splash would produce a
black rectangle, so the default (--ink white) ignores the artwork's own colour
and uses its alpha channel as ink coverage, painted white on black. That keeps
the anti-aliasing -- a half-covered pixel becomes mid-grey -- and is what a boot
logo wants. Pass --ink source for artwork that already carries the colours it
should be shown in.

The framebuffer is 32bpp B,G,R,X on MDSS; the byte order is handled below.

Usage:
    make-bootsplash.py [-o bootsplash.gz] [-i assets/sfosboot.png]
                       [--width 1080] [--height 1920] [--stride 4352]
                       [--preview splash.png]
"""
import argparse
import gzip
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_IMAGE = os.path.join(HERE, os.pardir, "assets", "sfosboot.png")


def build(image_path, width, height, stride, ink, fit_w, fit_h, center_y,
          preview=None):
    """Return one screen of raw framebuffer bytes: the artwork, fit and centred."""
    src = Image.open(image_path)

    if ink == "white":
        if "A" not in src.getbands():
            raise SystemExit(
                "--ink white needs an alpha channel to use as ink coverage; "
                "%s has bands %s. Use --ink source." % (image_path, src.getbands()))
        # Alpha *is* the artwork: coverage 0..255 becomes black..white.
        art = src.getchannel("A").convert("L")
    else:
        # Composite the artwork's own colours over black, honouring alpha.
        src = src.convert("RGBA")
        flat = Image.new("RGBA", src.size, (0, 0, 0, 255))
        flat.alpha_composite(src)
        art = flat.convert("RGB")

    # Contain: scale to fit inside the box, preserving aspect ratio. Never
    # upscale past the box and never stretch -- one scale factor for both axes.
    box_w = max(1, int(width * fit_w))
    box_h = max(1, int(height * fit_h))
    scale = min(box_w / src.width, box_h / src.height)
    new_w = max(1, round(src.width * scale))
    new_h = max(1, round(src.height * scale))
    art = art.resize((new_w, new_h), Image.LANCZOS)

    # Horizontally centred; vertically placed so the artwork's midpoint sits at
    # center_y of the panel. Slightly above true centre reads better on a tall
    # panel -- optical centre is above geometric centre.
    top = int(round(height * center_y)) - new_h // 2
    top = max(0, min(top, height - new_h))

    img = Image.new("RGB", (width, height), (0, 0, 0))
    img.paste(art, ((width - new_w) // 2, top))

    if preview:
        img.save(preview)

    # MDSS wants B,G,R,X per pixel. Building an RGBA image whose channels are
    # (B, G, R, 0xFF) and dumping it gives exactly that byte order, and does it
    # in C rather than in a 2-million-iteration Python loop.
    r, g, b = img.split()
    x = Image.new("L", img.size, 0xFF)
    raw = Image.merge("RGBA", (b, g, r, x)).tobytes()

    # Pad each line from width*4 out to the hardware stride.
    row_bytes = width * 4
    pad = stride - row_bytes
    if pad < 0:
        raise SystemExit("stride %d is smaller than width*4 (%d)"
                         % (stride, row_bytes))
    if not pad:
        return raw
    filler = bytes(pad)
    return b"".join(raw[y * row_bytes:(y + 1) * row_bytes] + filler
                    for y in range(height))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-o", "--output", default="bootsplash.gz")
    p.add_argument("-i", "--image", default=DEFAULT_IMAGE,
                   help="source artwork (default: assets/sfosboot.png)")
    p.add_argument("--ink", choices=("white", "source"), default="white",
                   help="'white' (default) paints the alpha channel white on "
                        "black; 'source' composites the artwork's own colours")
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument("--stride", type=int, default=4352)
    p.add_argument("--fit-width", type=float, default=0.62,
                   help="fraction of the panel width the artwork may occupy")
    p.add_argument("--fit-height", type=float, default=0.5,
                   help="fraction of the panel height the artwork may occupy")
    p.add_argument("--center-y", type=float, default=0.42,
                   help="where the artwork's vertical midpoint sits, as a "
                        "fraction of panel height (0.5 = geometric centre)")
    p.add_argument("--buffers", type=int, default=1,
                   help="how many screen-sized buffers to fill. fbsplash pans "
                        "to buffer 0 explicitly, so 1 is correct; use 2 only "
                        "if writing the file straight to /dev/fb0 on a device "
                        "where that works and the live buffer is unknown.")
    p.add_argument("--preview")
    a = p.parse_args()

    frame = build(a.image, a.width, a.height, a.stride, a.ink,
                  a.fit_width, a.fit_height, a.center_y, a.preview)

    # virtual_size on karatep is 1080,3840 -- two 1920-line buffers. fbsplash
    # sets fb_var_screeninfo.yoffset = 0 before FBIOPAN_DISPLAY, which is what
    # MDP uses to locate the pipe's source buffer, so buffer 0 is always the
    # live one and a single screen is enough. (Duplicating the frame into both
    # buffers was needed only for the old `zcat > /dev/fb0` redirect, which
    # always landed at offset 0 with no say in which buffer was displayed.)
    raw = frame * a.buffers

    expected = a.stride * a.height * a.buffers
    assert len(raw) == expected, (len(raw), expected)

    with gzip.open(a.output, "wb", compresslevel=9) as f:
        f.write(raw)

    print("%s: %d bytes raw (%d x %d, stride %d, %d buffer(s)) -> %d gzipped"
          % (a.output, len(raw), a.width, a.height, a.stride, a.buffers,
             os.path.getsize(a.output)))


if __name__ == "__main__":
    main()
