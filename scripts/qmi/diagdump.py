#!/usr/bin/python3
# Dump the modem's own DIAG log frames for chosen log codes.
#
# Usage: diagdump.py <seconds> <code> [code ...]

import os
import select
import struct
import sys
import time

sys.path.insert(0, "/home/defaultuser")
from diagsip import set_log_mask, frames
from diagefs import Diag

seconds = float(sys.argv[1])
codes = [int(c, 0) for c in sys.argv[2:]]

d = Diag()
for e in range(0, 16):
    set_log_mask(d, e)

print("listening %.0fs for %s" % (seconds, " ".join("0x%04x" % c for c in codes)))
end = time.time() + seconds
seen = {c: 0 for c in codes}
LIMIT = 4

while time.time() < end:
    r, _, _ = select.select([d.fd], [], [], max(0.05, end - time.time()))
    if not r:
        continue
    try:
        buf = os.read(d.fd, 1 << 20)
    except OSError:
        continue
    for pkt in frames(buf):
        if pkt[:1] != b"\x10" or len(pkt) < 12:
            continue
        code, = struct.unpack_from("<H", pkt, 6)
        if code not in seen or seen[code] >= LIMIT:
            continue
        seen[code] += 1
        body = pkt[16:]
        txt = "".join(chr(c) if 32 <= c < 127 else "." for c in body[:220])
        print("\n--- 0x%04x #%d, %d bytes ---" % (code, seen[code], len(body)))
        print("  hex: %s" % body[:96].hex())
        print("  txt: %s" % txt)
    if all(v >= LIMIT for v in seen.values()):
        break

d.close()
