#!/usr/bin/python3
#
# Full-spectrum DIAG capture: every equipment id, minimum work on the handset.
#
# Everything we know about the failed VoLTE call was learned from equipment id 1
# -- all 72,323 frames of the last capture, 93 distinct codes, every one of them
# 0x1xxx. That was a deliberate restriction: sdpwatch.py scanned each frame for
# printable runs in Python, and raising all sixteen masks on top of that drove
# this handset past a load average of 4 and starved the shell running the test.
#
# So the conclusion "no failure-specific code exists" is scoped to one sixteenth
# of the modem's logging. LTE (equip 0xb), audio and thirteen other classes have
# never been looked at, and the call dies in media -- QMI cause 373 is the media
# class -- which is exactly where equip 1 is least likely to be the right place.
#
# The fix for the load problem is to stop doing analysis on the device. This
# raises all sixteen masks and writes the driver's batches to disk verbatim:
#
#     [t double][len u32][raw batch]
#
# No HDLC decode, no struct parsing, no text scan. sdprawparse.py does all of
# that on the host, where CPU is free. /data has ~14 GB, so size is not a
# constraint; keeping up with the driver is, which is why the loop does nothing
# but read and write.
#
# Usage: sdpraw.py <seconds> <outfile> [preflight_seconds]

import os
import signal
import struct
import sys
import time

sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag  # noqa: E402

DIAG_LOG_CONFIG_F = 0x73
LOG_CONFIG_SET_MASK = 3

# F3 debug messaging is a *separate* mechanism from log packets, with its own
# command and its own masks -- which is why the port's old "this modem emits no
# F3 at all" finding was wrong: only the log masks had ever been raised. With
# these set the modem emits EXT_MSG_F (0x92), i.e. whole format strings, so no
# QSR hash database is needed to read them.
#   DIAG_CMD_MSG_CONFIG 0x7D, DIAG_CMD_OP_SET_ALL_MSG_MASK 5  (kernel diagchar.h)
#   struct diag_msg_config_rsp_t { u8 cmd, u8 sub, u8 status, u8 pad, u32 rt }
DIAG_CMD_MSG_CONFIG = 0x7D
DIAG_CMD_OP_SET_ALL_MSG_MASK = 5


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


def set_all_msg_mask(d, rt_mask=0xFFFFFFFF):
    req = struct.pack("<BBBBI", DIAG_CMD_MSG_CONFIG,
                      DIAG_CMD_OP_SET_ALL_MSG_MASK, 0, 0, rt_mask)
    d.send(req)
    rsp = d.recv(struct.pack("<BB", DIAG_CMD_MSG_CONFIG,
                             DIAG_CMD_OP_SET_ALL_MSG_MASK), timeout=5.0)
    return None if rsp is None else struct.unpack_from("<BBBBI", rsp, 0)[2]


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
    outpath = sys.argv[2] if len(sys.argv) > 2 else "/data/sdpraw.bin"
    preflight = float(sys.argv[3]) if len(sys.argv) > 3 else 15.0
    equips = [int(x) for x in sys.argv[4:]] or list(range(16))

    signal.signal(signal.SIGALRM, _alarm)
    d = Diag()

    ok = []
    for e in equips:
        if set_log_mask(d, e) == 0:
            ok.append(e)
    print("log masks raised for equip ids: %s"
          % (" ".join(str(e) for e in ok) or "none"))
    st = set_all_msg_mask(d)
    print("F3 message masks: %s"
          % ("no response" if st is None else "status=%d" % st))
    sys.stdout.flush()

    # Pre-flight: find out what full-spectrum logging actually costs before the
    # user is asked to place a call, so an untenable rate is discovered now
    # rather than after a wasted window.
    if preflight > 0:
        t0 = time.time()
        nb = nr = 0
        while time.time() - t0 < preflight:
            buf = read_deadline(d.fd, 0.5)
            if buf:
                nb += len(buf)
                nr += 1
        dt = time.time() - t0
        print("preflight: %.1f KB/s over %.0fs (%d reads) -- ~%.0f MB for %.0fs"
              % (nb / dt / 1024, dt, nr, nb / dt * seconds / 1e6, seconds))
        sys.stdout.flush()

    f = open(outpath, "wb", buffering=1 << 20)
    start = time.time()
    end = start + seconds
    total = nbytes = 0
    while time.time() < end:
        buf = read_deadline(d.fd, min(1.0, end - time.time()))
        if not buf:
            continue
        f.write(struct.pack("<dI", time.time() - start, len(buf)))
        f.write(buf)
        total += 1
        nbytes += len(buf)
    f.close()
    d.close()
    print("%d batches, %.1f MB in %.0fs -> %s"
          % (total, nbytes / 1e6, seconds, outpath))
    return 0


if __name__ == "__main__":
    sys.exit(main())
