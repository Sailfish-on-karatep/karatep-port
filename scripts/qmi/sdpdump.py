#!/usr/bin/python3
#
# Read the modem's own SIP off the DIAG log stream.
#
# This was tried once before and came back empty, and it was written up as "the
# modem emits no SIP at all". That was correct at the time and is not evidence
# about now: it was captured when IMS never registered, so the modem was not
# sending any SIP for it to log. It registers now, and re-running the scan finds
# whole SIP messages in plaintext under log code 0x156e -- REGISTER, SUBSCRIBE
# and their responses, complete with the AKAv1-MD5 Authorization and BSNL's
# Service-Route.
#
# That matters because it is the only way to read the SDP. The VoLTE call dies
# inside the modem with "SDP parse failed"; this firmware has F3 messaging
# compiled out, so there is no debug text; and the SIP never appears usefully on
# an AP netdev because the registration negotiates IPsec (Security-Client /
# Security-Verify, ports 8973/8108, not 5060). DIAG logs it before encryption.
#
# Log codes, all under equipment id 1:
#
#   0x156e  a whole SIP message, one frame, plain text after a short header
#   0x11eb  raw IP packets in 280-byte fragments -- the same data, chopped up,
#           and useless for reading a message
#   0x1544  IMS internal state, carries the odd config-derived URI
#
# so only equipment id 1 is enabled. Raising all sixteen works but costs a
# sustained load average of 4+ on this handset, which starves the shell driving
# the test.
#
#   DIAG_LOG_CONFIG_F = 0x73
#     request  [0x73][3 pad][op u32 = 3][equip_id u32][last_item u32][mask]
#     response [0x73][3 pad][op u32][status u32][equip_id u32][last_item u32]
#
# Usage: sdpdump.py [seconds] [outfile] [equip_id ...]

import os
import signal
import struct
import sys
import time

sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag, USER_SPACE_DATA_TYPE, hdlc_decode  # noqa: E402

DIAG_LOG_CONFIG_F = 0x73
LOG_CONFIG_SET_MASK = 3

# 0x156e is the whole-message log and is what we are here for. 0x1544 is cheap
# and has been seen carrying IMS URIs. 0x11eb is excluded on purpose: it is the
# same bytes as 0x156e split across 280-byte frames, so it only adds noise.
WANT_CODES = (0x156E, 0x1544)


class _Timeout(Exception):
    pass


def _alarm(_sig, _frm):
    raise _Timeout()


def read_deadline(fd, seconds):
    """One read() from /dev/diag that cannot sleep forever.

    diagchar_read() waits in wait_event_interruptible() and ignores O_NONBLOCK,
    and diagchar_poll() reports the device readable whenever the driver has been
    woken rather than only when a batch is queued -- so select() promises data
    that read() then blocks on. A one-shot SIGALRM is the only deadline that
    works. The timer is armed around the read alone so that no signal can land
    part-way through writing the output file.
    """
    signal.setitimer(signal.ITIMER_REAL, max(0.05, seconds))
    try:
        return os.read(fd, 1 << 20)
    except (_Timeout, OSError):
        return b""
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


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


def sip_text(pkt):
    """The SIP message inside a 0x156e frame, as text.

    The frame is [0x10][len][len][code][ts u64] then a vendor header that ends
    with the Call-ID as a NUL-terminated string, then the message itself. Rather
    than model the header, take everything from the first SIP start-line or
    status-line onwards -- there is exactly one per frame.
    """
    body = pkt[12:]
    starts = []
    for tok in (b"SIP/2.0 ", b"REGISTER ", b"INVITE ", b"SUBSCRIBE ",
                b"NOTIFY ", b"MESSAGE ", b"OPTIONS ", b"BYE ", b"CANCEL ",
                b"ACK ", b"UPDATE ", b"PRACK ", b"INFO ", b"REFER "):
        i = body.find(tok)
        if i >= 0:
            starts.append(i)
    if not starts:
        return None
    out = body[min(starts):]
    return "".join(chr(c) if 32 <= c < 127 else ("\n" if c in (10, 13) else ".")
                   for c in out)


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
    outpath = sys.argv[2] if len(sys.argv) > 2 else "/data/sdpdump.txt"
    equips = [int(x) for x in sys.argv[3:]] or [1]

    signal.signal(signal.SIGALRM, _alarm)

    d = Diag()
    ok = [e for e in equips if set_log_mask(d, e) == 0]
    print("log masks raised for equip ids: %s"
          % (" ".join(str(e) for e in ok) or "none"))
    sys.stdout.flush()

    out = open(outpath, "w")
    start = time.time()
    end = start + seconds
    total = kept = 0
    while time.time() < end:
        buf = read_deadline(d.fd, min(1.0, end - time.time()))
        if not buf:
            continue
        for pkt in frames(buf):
            total += 1
            if pkt[:1] != b"\x10" or len(pkt) < 12:
                continue
            code, = struct.unpack_from("<H", pkt, 6)
            if code not in WANT_CODES:
                continue
            txt = sip_text(pkt)
            if txt is None:
                continue
            kept += 1
            out.write("===== 0x%04x  t=%.1fs  %d bytes =====\n%s\n\n"
                      % (code, time.time() - start, len(pkt), txt.strip()))
            out.flush()

    out.close()
    print("%d frames seen, %d SIP messages written to %s"
          % (total, kept, outpath))
    d.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
