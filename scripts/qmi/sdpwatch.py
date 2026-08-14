#!/usr/bin/python3
#
# Find what the modem records for itself between the PRACK and the CANCEL.
#
# sdpdump.py answered "what does the SIP say"; this answers "what does the modem
# think happened". The call dies 40 ms after the PRACK with QMI end cause 373 and
# the text "SDP parse failed", which is a local decision taken on the spot -- so
# whatever the IMS stack decided is decided inside that 40 ms window, and if it
# leaves any trace at all it is in a log packet with a timestamp inside it.
#
# So this keeps everything under equipment id 1 rather than the two codes
# sdpdump.py filters to: every frame gets a timestamped row of code and length,
# any frame carrying a run of printable text gets that text kept as well, and
# 0x156e is written out whole as before. Post-processing then lines the codes up
# against the SIP and asks which ones only ever appear next to a failure.
#
# Equipment id 1 alone is deliberate: 0x156e, 0x11eb and 0x1544 all live there,
# and raising all sixteen masks drives this handset past a load average of 4 and
# starves the shell running the test.
#
# Usage: sdpwatch.py [seconds] [outfile] [equip_id ...]

import os
import signal
import struct
import sys
import time

sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag, USER_SPACE_DATA_TYPE, hdlc_decode  # noqa: E402

DIAG_LOG_CONFIG_F = 0x73
LOG_CONFIG_SET_MASK = 3

SIP_STARTS = (b"SIP/2.0 ", b"REGISTER ", b"INVITE ", b"SUBSCRIBE ", b"NOTIFY ",
              b"MESSAGE ", b"OPTIONS ", b"BYE ", b"CANCEL ", b"ACK ",
              b"UPDATE ", b"PRACK ", b"INFO ", b"REFER ")


class _Timeout(Exception):
    pass


def _alarm(_sig, _frm):
    raise _Timeout()


def read_deadline(fd, seconds):
    """One read() from /dev/diag that cannot sleep forever.

    diagchar_read() waits in wait_event_interruptible() and ignores O_NONBLOCK,
    and diagchar_poll() calls the device readable whenever the driver has been
    woken rather than when a batch is queued, so select() cannot bound this.
    The timer is armed around the read alone, so no signal lands mid-write.
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


def longest_text(body, minrun=12):
    """The longest printable-ASCII run in a frame, if it is long enough.

    Most log packets are packed binary. A frame that carries a readable string
    is carrying something a human wrote -- a URI, a state name, an error -- and
    those are the ones worth reading next to a failure.
    """
    best_start = best_len = run_start = run = 0
    for i, c in enumerate(body):
        if 32 <= c < 127:
            if run == 0:
                run_start = i
            run += 1
            if run > best_len:
                best_len, best_start = run, run_start
        else:
            run = 0
    if best_len < minrun:
        return None
    return body[best_start:best_start + best_len].decode("ascii", "replace")


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
    outpath = sys.argv[2] if len(sys.argv) > 2 else "/data/sdpwatch"
    equips = [int(x) for x in sys.argv[3:]] or [1]

    signal.signal(signal.SIGALRM, _alarm)
    d = Diag()
    ok = [e for e in equips if set_log_mask(d, e) == 0]
    print("log masks raised for equip ids: %s"
          % (" ".join(str(e) for e in ok) or "none"))
    sys.stdout.flush()

    rows = open(outpath + "-rows.txt", "w")     # every frame, one line each
    sip = open(outpath + "-sip.txt", "w")       # 0x156e, written out whole
    start = time.time()
    end = start + seconds
    total = 0
    while time.time() < end:
        buf = read_deadline(d.fd, min(1.0, end - time.time()))
        if not buf:
            continue
        for pkt in frames(buf):
            if pkt[:1] != b"\x10" or len(pkt) < 12:
                continue
            total += 1
            code, = struct.unpack_from("<H", pkt, 6)
            t = time.time() - start
            body = pkt[12:]
            txt = longest_text(body)
            rows.write("%8.3f 0x%04x %5d %s\n"
                       % (t, code, len(pkt), (txt or "")[:160]))
            if code == 0x156E:
                idx = [body.find(s) for s in SIP_STARTS]
                idx = [i for i in idx if i >= 0]
                if idx:
                    msg = body[min(idx):]
                    sip.write("===== t=%.3f 0x%04x %d bytes =====\n%s\n\n"
                              % (t, code, len(pkt),
                                 "".join(chr(c) if 32 <= c < 127
                                         else ("\n" if c in (10, 13) else ".")
                                         for c in msg).strip()))
        rows.flush()
        sip.flush()

    rows.close()
    sip.close()
    print("%d log frames in %.0fs -> %s-rows.txt, %s-sip.txt"
          % (total, seconds, outpath, outpath))
    d.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
