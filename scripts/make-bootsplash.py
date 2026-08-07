#!/usr/bin/env python3
"""Generate hybris-boot's bootsplash.gz for this device's framebuffer.

The splash is a raw framebuffer dump -- no image format, no scaling, no error
if it does not fit. On karatep it is displayed by hybris-boot's fbsplash helper
(hybris/hybris-boot/fbsplash.c) reading it on stdin, not by upstream's
`zcat /bootsplash.gz > /dev/fb0`, which fails with ENODEV on this panel.

Geometry comes from the device, so check it rather than trusting the defaults:

    cat /sys/class/graphics/fb0/{virtual_size,stride,bits_per_pixel}

Lines are STRIDE bytes, not width*4 (4352 vs 4320 here, or the image shears),
and virtual_size is double-buffered -- fbsplash pans to the first buffer, so
one screen of data is enough. The framebuffer is 32bpp B,G,R,X on MDSS.

--ink white (the default) uses the artwork's alpha as ink coverage painted
white on black, which is what assets/sfosboot.png needs: it is black artwork on
a transparent background. --ink source keeps the artwork's own colours.

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

    # fbsplash pans to buffer 0 before drawing, so one screen is enough even
    # though virtual_size covers two.
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
