#!/usr/bin/python3
#
# Raise the modem's F3 runtime mask for chosen SSID ranges and report which
# firmware source files are talking.
#
# DIAG_EXT_MSG_CONFIG_F (0x7D) sub-commands 2 and 3 are laid out as
#
#   [0x7D][sub u8][ss_first u16][ss_last u16][status u16][ rt_mask u32 * n ]
#
# with the subsystem range *before* the status word, not after it -- which the
# replies prove: asking for 6000..6003 with the range one field too late comes
# back describing 0..129, because the firmware read our zero padding as the
# range. Sub-command 1 has no range and puts its status straight after the
# sub-command.
#
# Setting every level on every subsystem at once buries the device, so the
# level mask and the ranges are both arguments.

import os
import struct
import sys
import time

sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag, USER_SPACE_DATA_TYPE, DiagInterrupt, interruptible

CFG = 0x7D
SUB_GET_RANGES = 1
SUB_GET_MASK = 2
SUB_SET_MASK = 3

DIAG_EXT_MSG_F = 0x79
QSR_TERSE = (0x92, 0x93)

# MSG_LVL_LOW 0x01, MED 0x02, HIGH 0x04, ERROR 0x08, FATAL 0x10
LVL_NOISY = 0x1F
LVL_QUIET = 0x1C          # HIGH | ERROR | FATAL


def get_ranges(d):
    d.send(struct.pack("<BBH", CFG, SUB_GET_RANGES, 0))
    r = d.recv(bytes([CFG]), timeout=4.0)
    if not r or len(r) < 8:
        return []
    n, = struct.unpack_from("<I", r, 4)
    out = []
    for i in range(n):
        o = 8 + i * 4
        if o + 4 > len(r):
            break
        out.append(struct.unpack_from("<HH", r, o))
    return out


def get_mask(d, first, last):
    d.send(struct.pack("<BBHHH", CFG, SUB_GET_MASK, first, last, 0))
    return d.recv(bytes([CFG]), timeout=4.0)


def set_mask(d, first, last, level):
    n = last - first + 1
    req = (struct.pack("<BBHHH", CFG, SUB_SET_MASK, first, last, 0)
           + struct.pack("<%dI" % n, *([level] * n)))
    d.send(req)
    return d.recv(bytes([CFG]), timeout=4.0)


def f3_frames(buf):
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
            if not raw:
                continue
            if raw[0] in QSR_TERSE:
                yield ("terse", None)
                continue
            if raw[0] != DIAG_EXT_MSG_F:
                continue
            if b"\x7d" in raw:
                out = bytearray()
                esc = False
                for b in raw:
                    if esc:
                        out.append(b ^ 0x20)
                        esc = False
                    elif b == 0x7D:
                        esc = True
                    else:
                        out.append(b)
                raw = bytes(out)
            yield ("f3", raw[:-2])
        off += ln


def decode(pkt):
    if len(pkt) < 20:
        return None
    _, ts_type, num_args, drop = struct.unpack_from("<BBBB", pkt, 0)
    line, ss_id, ss_mask = struct.unpack_from("<HHI", pkt, 12)
    off = 20 + 4 * num_args
    if off > len(pkt):
        return None
    args = struct.unpack_from("<%dI" % num_args, pkt, 20) if num_args else ()
    parts = pkt[off:].split(b"\x00")
    fmt = parts[0].decode("ascii", "replace") if parts else ""
    src = parts[1].decode("ascii", "replace") if len(parts) > 1 else ""
    try:
        text = fmt % args
    except Exception:
        text = fmt + (" %s" % (args,) if args else "")
    return ss_id, src.rsplit("/", 1)[-1], line, text


def main():
    level = int(sys.argv[1], 0)
    seconds = float(sys.argv[2])
    ranges = []
    for a in sys.argv[3:]:
        lo, _, hi = a.partition("-")
        ranges.append((int(lo), int(hi or lo)))

    d = Diag()
    if not ranges:
        for lo, hi in get_ranges(d):
            print("  ssid %5d - %5d" % (lo, hi))
        d.close()
        return

    for lo, hi in ranges:
        before = get_mask(d, lo, hi)
        r = set_mask(d, lo, hi, level)
        after = get_mask(d, lo, hi)
        print("set %d-%d level 0x%02x" % (lo, hi, level))
        print("    reply  %s" % (r.hex() if r else "<none>"))
        print("    before %s" % (before.hex()[:80] if before else "<none>"))
        print("    after  %s" % (after.hex()[:80] if after else "<none>"))

    seen = {}
    terse = 0
    deadline = time.time() + seconds
    with interruptible():
        while time.time() < deadline:
            try:
                buf = os.read(d.fd, 1 << 20)
            except (DiagInterrupt, BlockingIOError, OSError):
                continue
            for kind, pkt in f3_frames(buf):
                if kind == "terse":
                    terse += 1
                    continue
                got = decode(pkt)
                if not got:
                    continue
                ss_id, src, line, text = got
                entry = seen.setdefault((ss_id, src), [0, []])
                entry[0] += 1
                if len(entry[1]) < 3:
                    entry[1].append("%d: %s" % (line, text[:160]))

    print("\n%d distinct sources, %d QSR-hashed" % (len(seen), terse))
    for (ss_id, src), (n, samples) in sorted(seen.items(),
                                             key=lambda kv: -kv[1][0]):
        print("  ssid %-6d %-28s x%d" % (ss_id, src, n))
        for s in samples:
            print("        %s" % s)

    for lo, hi in ranges:
        set_mask(d, lo, hi, 0)
    d.close()


if __name__ == "__main__":
    main()
