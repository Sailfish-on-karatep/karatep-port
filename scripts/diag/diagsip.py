#!/usr/bin/python3
# Ask the modem for its own log packets and look for SIP.
#
# This is the question that splits the remaining space: with IMS enabled, a
# complete IMS profile in NV and an IMS bearer available, does the modem ever
# put a REGISTER on the wire? "Never attempts" and "attempts and is refused"
# have completely different fixes, and qcril's logging cannot tell them apart
# -- it only relays what IMSA reports, which is "not registered" either way.
#
# Qualcomm carries SIP inside DIAG log packets, where it is plain text rather
# than a QSR hash, so it can be found without a message database. Rather than
# guess the log code, enable an equipment id wholesale for a short window and
# scan the raw frames for SIP tokens.
#
#   DIAG_LOG_CONFIG_F = 0x73
#     request  [0x73][3 pad][op u32 = 3][equip_id u32][last_item u32][mask]
#     response [0x73][3 pad][op u32][status u32][equip_id u32][last_item u32]
#
# Usage: diagsip.py [seconds] [equip_id ...]

import os
import select
import struct
import sys
import time

sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag, USER_SPACE_DATA_TYPE, hdlc_decode

DIAG_LOG_CONFIG_F = 0x73
LOG_CONFIG_SET_MASK = 3

SIP_TOKENS = (b"SIP/2.0", b"REGISTER sip:", b"sip:", b"P-CSCF", b"IMPI",
              b"@ims.mnc", b"3gpp-service.ims")


def set_log_mask(d, equip_id, last_item=0x0FFF):
    nbytes = (last_item + 8) // 8
    req = (struct.pack("<B3x", DIAG_LOG_CONFIG_F)
           + struct.pack("<III", LOG_CONFIG_SET_MASK, equip_id, last_item)
           + b"\xff" * nbytes)
    d.send(req)
    rsp = d.recv(struct.pack("<B3x", DIAG_LOG_CONFIG_F), timeout=5.0)
    if rsp is None:
        return None
    _op, status = struct.unpack_from("<II", rsp, 4)
    return status


def frames(buf):
    """Yield decoded DIAG frames out of one read() from /dev/diag."""
    if len(buf) < 8:
        return
    data_type, num = struct.unpack_from("<ii", buf, 0)
    if data_type != USER_SPACE_DATA_TYPE:
        return
    off = 8
    for _ in range(num):
        if off + 4 > len(buf):
            return
        ln, = struct.unpack_from("<i", buf, off)
        off += 4
        if ln <= 0 or off + ln > len(buf):
            return
        for raw in buf[off:off + ln].split(b"\x7e"):
            if raw:
                try:
                    yield hdlc_decode(raw + b"\x7e")
                except Exception:
                    pass
        off += ln


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    equips = [int(x) for x in sys.argv[2:]] or [1, 4, 11]

    d = Diag()
    for e in equips:
        st = set_log_mask(d, e)
        print("log mask equip_id %d: %s" % (e, "ok" if st == 0 else st))

    print("listening %.0fs ..." % seconds)
    end = time.time() + seconds
    total = hits = 0
    codes = {}
    while time.time() < end:
        r, _, _ = select.select([d.fd], [], [], max(0.05, end - time.time()))
        if not r:
            continue
        try:
            buf = os.read(d.fd, 1 << 20)
        except OSError:
            continue
        for pkt in frames(buf):
            total += 1
            if pkt[:1] == b"\x10" and len(pkt) >= 12:
                code, = struct.unpack_from("<H", pkt, 6)
                codes[code] = codes.get(code, 0) + 1
            if any(t in pkt for t in SIP_TOKENS):
                hits += 1
                code = struct.unpack_from("<H", pkt, 6)[0] \
                    if pkt[:1] == b"\x10" and len(pkt) >= 12 else -1
                txt = "".join(chr(c) if 32 <= c < 127 else "."
                              for c in pkt[:400])
                print("\n--- SIP-bearing frame, log code 0x%04x ---\n%s"
                      % (code, txt))
                if hits >= 12:
                    break
        if hits >= 12:
            break

    print("\n%d frames, %d SIP-bearing" % (total, hits))
    if codes:
        top = sorted(codes.items(), key=lambda kv: -kv[1])[:15]
        print("top log codes: " + " ".join("0x%04x:%d" % c for c in top))
    d.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
