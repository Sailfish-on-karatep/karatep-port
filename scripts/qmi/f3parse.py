#!/usr/bin/python3
#
# Host-side decode of the modem's F3 debug messaging from an sdpraw.py capture.
#
# sdprawparse.py only understands log packets (0x10). Once the *message* masks
# are raised -- a separate mechanism with a separate command, which is why this
# port's old "the modem emits no F3 at all" finding was wrong -- the modem also
# emits F3 records, and on this build they arrive as EXT_MSG_F (0x92) with the
# format string and source filename in plaintext, so no QSR hash database is
# needed to read them.
#
#   u8 cmd | u8 ts_type | u8 num_args | u8 drop_cnt | u64 ts
#   u16 line | u16 ss_id | u32 ss_mask | u32 args[num_args]
#   char fmt[] '\0' char file[] '\0'
#
# MSG_F (0x79) is the legacy form and carries the same layout on this modem --
# discovered the hard way, after a parser that only looked at 0x92 failed to
# find a message that was plainly in the capture.
#
#   f3parse.py <capture.bin> <outprefix>                 full log + census
#   f3parse.py <capture.bin> --find <substring> [ctx]    hits with context
#
# The %-substitution is deliberately approximate: args are all u32 in the wire
# format, so %s cannot be recovered (the pointer is meaningless off-target) and
# is left as-is. Everything that matters here -- filename, line number, and the
# integer arguments -- is exact.

import collections
import re
import struct
import sys

USER_SPACE_DATA_TYPE = 0x00000020
MSG_TYPES = (0x79, 0x92)

KINDS = {
    0x10: "LOG_F",
    0x79: "MSG_F",
    0x92: "EXT_MSG_F",
    0x93: "QSR_TERSE",
    0x98: "QSherlock",
    0x99: "QSR4_TERSE",
}

SIP_STARTS = (b"SIP/2.0 ", b"REGISTER ", b"INVITE ", b"SUBSCRIBE ", b"NOTIFY ",
              b"MESSAGE ", b"OPTIONS ", b"BYE ", b"CANCEL ", b"ACK ",
              b"UPDATE ", b"PRACK ", b"INFO ", b"REFER ")

_FMT = re.compile(r"%[-+ #0]*[0-9]*(?:\.[0-9]+)?(?:hh|h|ll|l|z)?[diouxXc]")


def hdlc_decode(raw):
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
                yield hdlc_decode(raw + b"\x7e")
        off += ln


_FNAME = re.compile(rb"^[A-Za-z0-9_./+\-]{4,}$")


def parse_msg(pkt):
    """(ss_id, line, file, fmt, args) for an *inline* F3 record, or None.

    On this build the packet type is the discriminator, exactly: across a
    420-second capture, all 227,849 MSG_F (0x79) records carry inline strings
    and all 1,692,674 EXT_MSG_F (0x92) records are QSR -- 100% and 0%, no
    overlap. Do not infer the layout from the name: it is the *legacy* type
    that carries whole format strings here, and the extended one that carries
    a hash.

    The filename check below is belt and braces, and it was not always. Before
    the packet-type split was measured, a QSR record whose trailing argument
    bytes happened to contain a NUL followed by printable ASCII decoded as a
    perfectly plausible inline record: one such frame decoded as file "oh",
    format "5", and was briefly mistaken for a unique event five milliseconds
    before a call was cancelled. Its CRC was valid; it was f101:6482 with args
    (…, 2000, …), and the "oh" was two bytes of an integer. 901 records were
    misread that way. The name test alone would have caught them: real names
    are source paths, and where the firmware truncates them it does so at 18
    characters and they stay source-like (qpSipSessionServic, qpSipDispatcher.cp).
    """
    if pkt[:1] != b"\x79" or len(pkt) < 20:
        return None
    nargs = pkt[2]
    end = 20 + 4 * nargs
    if nargs > 16 or end > len(pkt):
        return None
    line, ss_id = struct.unpack_from("<HH", pkt, 12)
    args = struct.unpack_from("<%dI" % nargs, pkt, 20) if nargs else ()
    tail = pkt[end:]
    parts = tail.split(b"\x00")
    if len(parts) < 2 or not _FNAME.match(parts[1]):
        return None
    fmt = parts[0].decode("ascii", "replace")
    fname = parts[1].decode("ascii", "replace")
    return ss_id, line, fname.rsplit("/", 1)[-1], fmt, args


def parse_qsr(pkt):
    """(ss_id, line, msg_hash, args) for a QSR record, or None.

    EXT_MSG_F (0x92) on this build: the 32-bit hash sits immediately after
    ss_mask, where the strings would be, and the arguments follow it. The hash
    resolves against the table in modem.b14 (u16 file_index | u16 line |
    u32 hash); the strings themselves ship only in the vendor QXDM database.
    """
    if pkt[:1] != b"\x92" or len(pkt) < 24:
        return None
    nargs = pkt[2]
    if nargs > 16 or 24 + 4 * nargs > len(pkt):
        return None
    line, ss_id = struct.unpack_from("<HH", pkt, 12)
    msg_hash, = struct.unpack_from("<I", pkt, 20)
    args = struct.unpack_from("<%dI" % nargs, pkt, 24) if nargs else ()
    return ss_id, line, msg_hash, args


def render(fmt, args):
    """Substitute the integer conversions; leave %s alone (see header)."""
    it = iter(args)

    def sub(_m):
        try:
            return str(next(it))
        except StopIteration:
            return _m.group(0)
    return _FMT.sub(sub, fmt)


def records(path):
    """Yield (t, kind, payload) where payload is decoded per kind."""
    for t, buf in batches(path):
        for pkt in frames(buf):
            if not pkt:
                continue
            k = pkt[0]
            if k in MSG_TYPES:
                m = parse_msg(pkt)
                if m:
                    yield t, k, m
            elif k == 0x10 and len(pkt) >= 12:
                code, = struct.unpack_from("<H", pkt, 6)
                yield t, 0x10, (code, pkt[12:])
            else:
                yield t, k, None


def collect(path):
    kinds = collections.Counter()
    msgs = []      # (t, ss_id, "file:line", text)
    sip = []       # (t, first line of the SIP message)
    for t, k, p in records(path):
        kinds[k] += 1
        if k in MSG_TYPES:
            ss_id, line, fname, fmt, args = p
            msgs.append((t, ss_id, "%s:%d" % (fname, line), render(fmt, args)))
        elif k == 0x10 and p[0] == 0x156E:
            body = p[1]
            idx = [body.find(s) for s in SIP_STARTS]
            idx = [i for i in idx if i >= 0]
            if idx:
                start = min(idx)
                nl = body.find(b"\r\n", start)
                head = body[start:nl if nl > 0 else start + 80]
                sip.append((t, head.decode("ascii", "replace")))
    return kinds, msgs, sip


def main():
    path = sys.argv[1]
    kinds, msgs, sip = collect(path)

    if len(sys.argv) > 2 and sys.argv[2] == "--find":
        needle = sys.argv[3]
        ctx = int(sys.argv[4]) if len(sys.argv) > 4 else 4
        hits = [i for i, m in enumerate(msgs)
                if needle in m[2] or needle in m[3]]
        print("%d F3 messages, %d hit(s) for %r" % (len(msgs), len(hits),
                                                    needle))
        for i in hits:
            print("\n--- hit at t=%.3f ---" % msgs[i][0])
            for j in range(max(0, i - ctx), min(len(msgs), i + ctx + 1)):
                t, ss, loc, txt = msgs[j]
                mark = ">>>" if j == i else "   "
                print("%s %8.3f ss%-3d %-26s %s" % (mark, t, ss, loc, txt[:150]))
        return 0

    prefix = sys.argv[2] if len(sys.argv) > 2 else path
    with open(prefix + "-f3.txt", "w") as f:
        for t, ss, loc, txt in msgs:
            f.write("%9.3f ss%-3d %-28s %s\n" % (t, ss, loc, txt))
    with open(prefix + "-sipseq.txt", "w") as f:
        for t, head in sip:
            f.write("%9.3f %s\n" % (t, head))

    print("packet types:")
    for k, v in kinds.most_common():
        print("  0x%02x %-12s %9d" % (k, KINDS.get(k, ""), v))
    print("%d F3 messages decoded, %d SIP messages" % (len(msgs), len(sip)))
    files = collections.Counter(m[2].split(":")[0] for m in msgs)
    print("top source files:")
    for f_, n in files.most_common(15):
        print("  %8d  %s" % (n, f_))
    return 0


if __name__ == "__main__":
    sys.exit(main())
