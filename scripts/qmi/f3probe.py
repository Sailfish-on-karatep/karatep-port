#!/usr/bin/python3
#
# Turn on the modem's F3 debug messaging and find out what it actually emits.
#
# The port's standing finding is that this modem "emits no F3 debug messaging at
# all". That was measured without ever setting the message masks -- a census of
# the 739,497-frame full-spectrum capture finds only log packets (0x10) and nine
# QSherlock frames (0x98), and no 0x79/0x92/0x93 at all. Log masks and message
# masks are separate mechanisms with separate commands, and only the log masks
# were ever raised. So the negative may be the same shape as the equipment-id-1
# mistake: a real measurement of the wrong thing.
#
# This matters now because modem.b24 -- the QSR string table -- contains
# qipcallsdp.c, and its strings say the SDP module validates the b=AS bandwidth
# line and carries a hardcoded bypass "ignore AS validation for RJIL". If F3 can
# be switched on, "Bandwidth : AS %d" and "Max Bandwidth : AS %d" become
# readable at the moment BSNL's SDP is rejected, and the hypothesis becomes an
# observation.
#
# Request format is from the device's own kernel, drivers/char/diag:
#
#   DIAG_CMD_MSG_CONFIG        0x7D        (diagchar.h:95)
#   DIAG_CMD_OP_SET_ALL_MSG_MASK  5        (diagchar.h:137)
#
#   struct diag_msg_config_rsp_t {         (diag_masks.h:85)
#       uint8 cmd_code; uint8 sub_cmd; uint8 status; uint8 padding;
#       uint32 rt_mask;
#   } __packed;
#
# diag_cmd_set_all_msg_mask() does memset(mask->ptr, req->rt_mask, ...), so it
# is the low byte of rt_mask that lands in every mask byte.
#
# Nothing here is persistent: runtime masks reset when the modem restarts, and
# no NV item is touched.
#
# Usage: f3probe.py [seconds] [outfile]

import collections
import os
import re
import signal
import struct
import sys
import time

sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag  # noqa: E402

DIAG_CMD_MSG_CONFIG = 0x7D
DIAG_CMD_OP_SET_ALL_MSG_MASK = 5

# The message packet types worth telling apart in the census.
KINDS = {
    0x10: "LOG_F (log packets)",
    0x79: "MSG_F (F3, legacy)",
    0x92: "EXT_MSG_F (F3, full string)",
    0x93: "QSR_EXT_MSG_TERSE_F (F3, hashed)",
    0x98: "QSherlock",
    0x99: "QSR4_EXT_MSG_TERSE_F (F3, hashed)",
}


class _Timeout(Exception):
    pass


def _alarm(_sig, _frm):
    raise _Timeout()


def read_deadline(fd, seconds):
    signal.setitimer(signal.ITIMER_REAL, max(0.05, seconds))
    try:
        return os.read(fd, 1 << 20)
    except (_Timeout, OSError):
        return b""
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def set_all_msg_mask(d, rt_mask=0xFFFFFFFF):
    req = struct.pack("<BBBBI", DIAG_CMD_MSG_CONFIG,
                      DIAG_CMD_OP_SET_ALL_MSG_MASK, 0, 0, rt_mask)
    d.send(req)
    rsp = d.recv(struct.pack("<BB", DIAG_CMD_MSG_CONFIG,
                             DIAG_CMD_OP_SET_ALL_MSG_MASK), timeout=5.0)
    if rsp is None:
        return None
    _cc, _sc, status, _pad, got = struct.unpack_from("<BBBBI", rsp, 0)
    return status, got


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    outpath = sys.argv[2] if len(sys.argv) > 2 else None

    signal.signal(signal.SIGALRM, _alarm)
    d = Diag()

    r = set_all_msg_mask(d)
    print("set_all_msg_mask -> %s" % ("no response" if r is None
                                      else "status=%d rt_mask=0x%08x" % r))
    sys.stdout.flush()

    f = open(outpath, "wb", buffering=1 << 20) if outpath else None
    kinds = collections.Counter()
    texts = collections.Counter()
    start = time.time()
    nbytes = 0
    while time.time() - start < seconds:
        buf = read_deadline(d.fd, min(1.0, seconds - (time.time() - start)))
        if not buf:
            continue
        nbytes += len(buf)
        if f:
            f.write(struct.pack("<dI", time.time() - start, len(buf)))
            f.write(buf)
        # Cheap census only; real decoding happens on the host.
        for pkt in _frames(buf):
            if not pkt:
                continue
            kinds[pkt[0]] += 1
            if pkt[0] in (0x79, 0x92, 0x93, 0x99):
                for t in re.findall(b"[ -~]{8,}", pkt):
                    texts[t[:70]] += 1
    if f:
        f.close()
    d.close()

    print("%.1f KB/s over %.0fs" % (nbytes / seconds / 1024, seconds))
    print("packet types:")
    for k, v in kinds.most_common():
        print("  0x%02x %-38s %8d" % (k, KINDS.get(k, ""), v))
    if texts:
        print("sample F3 text:")
        for t, n in texts.most_common(25):
            print("  %5d  %s" % (n, t.decode("ascii", "replace")))
    return 0


def _frames(buf):
    if len(buf) < 8:
        return
    data_type, num = struct.unpack_from("<ii", buf, 0)
    if data_type != 0x20:
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
                yield _hdlc(raw)
        off += ln


def _hdlc(raw):
    if b"\x7d" not in raw:
        return raw[:-2] if len(raw) > 2 else raw
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
    return bytes(out[:-2]) if len(out) > 2 else bytes(out)


if __name__ == "__main__":
    sys.exit(main())
