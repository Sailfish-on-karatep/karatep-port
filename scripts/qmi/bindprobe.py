#!/usr/bin/python3
#
# Is the imss INTERNAL class a missing precondition rather than a dead task?
#
# The sweep sent every message an empty request. 49 ids answered
# INVALID_MESSAGE_ID, so they are simply not in this firmware; 34 answered
# INTERNAL, so their handlers do exist and fail inside the modem. The obvious
# thing an empty request cannot express is which subscription the client means:
# qcril binds its imss client to a subscription before it ever calls 0x8f/0x90,
# and a per-subscription handler asked without one has nothing to look up.
#
# Phases A and B retry every INTERNAL id with a subscription selector in TLV
# 0x01, as u32 then as u8. Both are reads -- nothing is written -- so a flip to
# success there is free information.
#
# Phase C is the bind hunt proper and does send messages: the 54/17 group, which
# are setters that objected to an empty request, get the same selector, and any
# that succeeds is followed immediately by 0x90 on the *same* client, because a
# bind only counts for the client that issued it. Restricted to imss, whose
# state is IMS settings and nothing else; CS voice lives in a different service.
# 0x8f is never written to -- its request is 72 bytes and a 4-byte guess at it
# would be meaningless.
import socket
import struct
import sys

sys.path.insert(0, "/home/defaultuser")
from qmiims import Imss, QMI_RESULT_TLV  # noqa: E402

GET_ENABLE_CONFIG = 0x90

INTERNAL = [0x33, 0x47, 0x4d, 0x67, 0x68, 0x69, 0x6a, 0x6c, 0x6d, 0x6f, 0x70,
            0x72, 0x73, 0x74, 0x75, 0x77, 0x78, 0x7a, 0x7b, 0x7d, 0x7e, 0x80,
            0x81, 0x83, 0x84, 0x86, 0x87, 0x8a, 0x8c, 0x8d, 0x8f, 0x90, 0x93,
            0x94]
SETTERS = [0x66, 0x89,
           0x22, 0x27, 0x30, 0x31, 0x42, 0x43, 0x4e, 0x50, 0x51, 0x5a, 0x5c,
           0x60, 0x61]


def tlv(tag, value):
    return struct.pack("<BH", tag, len(value)) + value


def result(tlvs):
    for t, v in tlvs:
        if t == QMI_RESULT_TLV and len(v) >= 4:
            return struct.unpack("<HH", v[:4])
    return None


def probe(imss, mid, payload, timeout=1.5):
    try:
        return result(imss.call(mid, payload, timeout=timeout))
    except socket.timeout:
        return None


def phase(imss, name, ids, payload, baseline=3):
    print("\n== %s ==" % name)
    flipped, moved = [], {}
    for mid in ids:
        r = probe(imss, mid, payload)
        if r is None:
            continue
        if r[0] == 0:
            flipped.append(mid)
        elif r[1] != baseline:
            moved.setdefault(r[1], []).append(mid)
    print("  now succeeding: %s" %
          (" ".join("0x%02x" % m for m in flipped) or "(none)"))
    for err in sorted(moved):
        print("  moved to error %d: %s" %
              (err, " ".join("0x%02x" % m for m in moved[err])))
    return flipped


def main():
    imss = Imss()

    r = probe(imss, GET_ENABLE_CONFIG, b"", timeout=3.0)
    print("baseline 0x90 -> %s" % (r,))

    sub_u32 = tlv(0x01, struct.pack("<I", 0))
    sub_u8 = tlv(0x01, b"\x00")

    phase(imss, "A: INTERNAL ids retried with subscription u32 0",
          INTERNAL, sub_u32)
    phase(imss, "B: INTERNAL ids retried with subscription u8 0",
          INTERNAL, sub_u8)

    print("\n== C: bind hunt -- setter, then 0x90 on the same client ==")
    hits = []
    for mid in SETTERS:
        for label, payload in (("u32", sub_u32), ("u8", sub_u8)):
            r = probe(imss, mid, payload)
            if r is None or r[0] != 0:
                continue
            after = probe(imss, GET_ENABLE_CONFIG, b"", timeout=3.0)
            print("  0x%02x (%s) accepted -> 0x90 now %s" % (mid, label, after))
            if after and after[0] == 0:
                hits.append(mid)
            break
    if not hits:
        print("  no setter made 0x90 answer")

    print("\n== D: modem still healthy? ==")
    for mid in (0x28, 0x26, 0x48):
        r = probe(imss, mid, b"", timeout=3.0)
        print("  0x%02x -> %s" % (mid, r))
    imss.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
