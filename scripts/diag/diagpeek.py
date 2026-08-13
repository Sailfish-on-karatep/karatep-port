#!/usr/bin/python3
# Histogram every DIAG frame the modem sends us, by command code.
#
# Answers "is the channel delivering anything at all, and of what kind" before
# any guessing about which mask enables what. 0x10 is a log packet, 0x79 an F3
# debug message, 0x92/0x93 a QSR-hashed one, 0x60 an event report.
import collections
import os
import struct
import sys
import time

sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag, USER_SPACE_DATA_TYPE, DiagInterrupt, interruptible
from f3probe import set_mask, get_ranges
from diagsip import set_log_mask

NAMES = {0x10: "log", 0x60: "event", 0x79: "F3 ext msg",
         0x92: "F3 QSR terse", 0x93: "F3 QSR2 terse", 0x1d: "?"}


def raw_frames(buf):
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
                yield raw
        off += ln


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0
    raise_masks = "--raise" in sys.argv
    raise_logs = "--logs" in sys.argv

    d = Diag()
    if raise_masks:
        for lo, hi in get_ranges(d):
            for start in range(lo, hi + 1, 200):
                stop = min(start + 199, hi)
                set_mask(d, start, stop, 0x1F)
        print("raised every F3 runtime mask")

    if raise_logs:
        # Control: the log mask is known to work on this build, so if log
        # packets arrive and F3 messages do not, the difference is the
        # firmware, not the capture path.
        for equip in range(0, 16):
            set_log_mask(d, equip)
        print("raised every log mask")

    counts = collections.Counter()
    sizes = collections.Counter()
    end = time.time() + seconds
    reads = 0
    with interruptible():
        while time.time() < end:
            try:
                buf = os.read(d.fd, 1 << 20)
            except (DiagInterrupt, BlockingIOError, OSError):
                continue
            reads += 1
            for raw in raw_frames(buf):
                counts[raw[0]] += 1
                sizes[raw[0]] += len(raw)

    print("%d reads, %d frames" % (reads, sum(counts.values())))
    for code, n in counts.most_common(20):
        print("  0x%02x %-14s x%-6d %d bytes" %
              (code, NAMES.get(code, ""), n, sizes[code]))
    d.close()


if __name__ == "__main__":
    main()
