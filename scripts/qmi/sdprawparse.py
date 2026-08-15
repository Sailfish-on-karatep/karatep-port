#!/usr/bin/python3
#
# Host-side decode of a sdpraw.py capture.
#
# sdpraw.py writes the DIAG driver's batches to disk untouched so the handset
# does no analysis at all. Everything expensive happens here: HDLC decode, log
# header parse, printable-run extraction, SIP reassembly.
#
#   sdprawparse.py <capture.bin> <outprefix>              rows + sip
#   sdprawparse.py <capture.bin> --window <t0> <t1>       hex dump of a window
#
# The window mode is the point of the exercise. The call dies ~30-50 ms after
# BSNL's 183 arrives, and the previous capture could only say "no code in that
# window is unique to the failure" -- true, but it was comparing code *identity*
# across one sixteenth of the log. Comparing frame *payloads* in the fatal window
# against the same codes elsewhere is the test that was never run.

import collections
import re
import struct
import sys

USER_SPACE_DATA_TYPE = 0x00000020

SIP_STARTS = (b"SIP/2.0 ", b"REGISTER ", b"INVITE ", b"SUBSCRIBE ", b"NOTIFY ",
              b"MESSAGE ", b"OPTIONS ", b"BYE ", b"CANCEL ", b"ACK ",
              b"UPDATE ", b"PRACK ", b"INFO ", b"REFER ")


def hdlc_decode(raw):
    """Undo the trailing-flag HDLC framing DIAG uses, minus the CRC check.

    The escape-free fast path matters: this runs over ~500 MB of capture, and
    the byte-at-a-time loop below is the single hottest thing in the decode.
    The large majority of frames contain no 0x7d at all, and for those the
    whole transform is "drop the flag and the CRC".
    """
    if raw.endswith(b"\x7e"):
        raw = raw[:-1]
    if b"\x7d" not in raw:
        return raw[:-2] if len(raw) > 2 else raw
    out = bytearray()
    esc = False
    for b in raw:
        if esc:
            out.append(b ^ 0x20)
            esc = False
        elif b == 0x7d:
            esc = True
        else:
            out.append(b)
    return bytes(out[:-2]) if len(out) > 2 else bytes(out)


def batches(path):
    """Yield (t, buf) for each recorded read() of /dev/diag."""
    with open(path, "rb") as f:
        while True:
            hdr = f.read(12)
            if len(hdr) < 12:
                return
            t, ln = struct.unpack("<dI", hdr)
            buf = f.read(ln)
            if len(buf) < ln:
                return
            yield t, buf


def frames(buf):
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


def log_packets(path):
    """Yield (t, code, pkt) for every DIAG log packet in the capture."""
    for t, buf in batches(path):
        for pkt in frames(buf):
            if pkt[:1] != b"\x10" or len(pkt) < 12:
                continue
            code, = struct.unpack_from("<H", pkt, 6)
            yield t, code, pkt


_RUNS = {}


def longest_text(body, minrun=12):
    """Longest printable-ASCII run in a frame, if it is long enough.

    Done with a regex rather than a Python byte loop purely for speed -- the
    interpreted version was the second hottest path over a 500 MB capture.
    """
    rx = _RUNS.get(minrun)
    if rx is None:
        rx = _RUNS[minrun] = re.compile(b"[ -~]{%d,}" % minrun)
    best = max(rx.findall(body), key=len, default=None)
    return best.decode("ascii", "replace") if best else None


def printable(msg):
    return "".join(chr(c) if 32 <= c < 127
                   else ("\n" if c in (10, 13) else ".") for c in msg).strip()


def do_rows(path, prefix):
    rows = open(prefix + "-rows.txt", "w")
    sip = open(prefix + "-sip.txt", "w")
    eq = collections.Counter()
    codes = collections.Counter()
    total = nsip = 0
    for t, code, pkt in log_packets(path):
        total += 1
        eq[code >> 12] += 1
        codes[code] += 1
        body = pkt[12:]
        txt = longest_text(body)
        rows.write("%9.3f 0x%04x %5d %s\n"
                   % (t, code, len(pkt), (txt or "")[:160]))
        if code == 0x156E:
            idx = [body.find(s) for s in SIP_STARTS]
            idx = [i for i in idx if i >= 0]
            if idx:
                nsip += 1
                sip.write("===== t=%.3f 0x%04x %d bytes =====\n%s\n\n"
                          % (t, code, len(pkt), printable(body[min(idx):])))
    rows.close()
    sip.close()
    print("%d log packets, %d distinct codes, %d SIP messages"
          % (total, len(codes), nsip))
    print("by equipment id:")
    for k in sorted(eq):
        print("  equip %2d : %8d frames  (%d codes)"
              % (k, eq[k], len({c for c in codes if c >> 12 == k})))


def do_window(path, t0, t1):
    seen = collections.OrderedDict()
    for t, code, pkt in log_packets(path):
        if t0 <= t <= t1:
            seen.setdefault(code, []).append((t, pkt))
    print("%d distinct codes in [%.3f, %.3f]" % (len(seen), t0, t1))
    for code in sorted(seen):
        hits = seen[code]
        print("\n=== 0x%04x  equip %d  %d frame(s) ==="
              % (code, code >> 12, len(hits)))
        for t, pkt in hits[:4]:
            body = pkt[12:]
            txt = longest_text(body, minrun=8)
            print("  t=%.3f len=%d %s" % (t, len(pkt),
                                          ("| " + txt) if txt else ""))
            print("    " + body[:96].hex())


def main():
    path = sys.argv[1]
    if len(sys.argv) > 2 and sys.argv[2] == "--window":
        do_window(path, float(sys.argv[3]), float(sys.argv[4]))
        return 0
    do_rows(path, sys.argv[2] if len(sys.argv) > 2 else path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
