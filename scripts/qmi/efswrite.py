#!/usr/bin/python3
#
# Write an EFS2 item on the modem, over the same DIAG channel diagefs.py reads.
#
# diagefs.py defines EFS2_WRITE but only ever reads. The write side is the same
# shape as the read side: open, then (fd, offset, data) per chunk, then close.
# The response carries (fd, offset, bytes_written, errno).
#
# Used here to correct /nv/item_files/ims/qp_ims_sms_config, whose first 128-byte
# field is the SMSC used for SMS over IMS. The retargeted Jio carrier config left
# Reliance's '10138' there on a BSNL SIM. That matters beyond messaging: qcril
# only ever learns "registered on IMS" -- and so only ever sets the ims_rte that
# decides CS vs IMS for voice -- from the WMS SMS transport registration
# indication, which has never fired on this device.
#
# The item is rewritten in place at its existing length. Nothing is truncated and
# nothing is created; if the file is missing this does not invent one.
import struct
import sys

sys.path.insert(0, "/home/defaultuser")
import diagefs  # noqa: E402

CHUNK = 256


def write_file(d, path, data):
    r = d.efs(diagefs.EFS2_OPEN, struct.pack("<II", diagefs.O_RDWR, 0)
              + diagefs.cstr(path))
    if r is None or len(r) < 8:
        return "open: no response"
    fd, err = struct.unpack_from("<ii", r, 0)
    if err != 0 or fd < 0:
        return "open: errno %d" % err
    off = 0
    try:
        while off < len(data):
            part = data[off:off + CHUNK]
            r = d.efs(diagefs.EFS2_WRITE,
                      struct.pack("<II", fd, off) + part)
            if r is None or len(r) < 16:
                return "write at %d: no response" % off
            _fd, _off, nwritten, werr = struct.unpack_from("<IIii", r, 0)
            if werr != 0:
                return "write at %d: errno %d" % (off, werr)
            if nwritten <= 0:
                return "write at %d: wrote %d" % (off, nwritten)
            off += nwritten
    finally:
        d.efs(diagefs.EFS2_CLOSE, struct.pack("<i", fd))
    return None


def main():
    path = sys.argv[1]
    newhex = sys.argv[2]
    data = bytes.fromhex(newhex)

    d = diagefs.Diag()
    before, err = diagefs.read_file(d, path, maxlen=4096)
    if before is None:
        print("cannot read %s: %s" % (path, err))
        d.close()
        return 1
    print("current: %d bytes" % len(before))
    if len(data) != len(before):
        print("refusing: new value is %d bytes, item is %d" %
              (len(data), len(before)))
        d.close()
        return 1
    if data == before:
        print("already identical, nothing to do")
        d.close()
        return 0

    e = write_file(d, path, data)
    if e:
        print("write failed: %s" % e)
        d.close()
        return 1

    after, err = diagefs.read_file(d, path, maxlen=4096)
    d.close()
    if after is None:
        print("wrote, but read-back failed: %s" % err)
        return 1
    print("read back: %d bytes, matches: %s" % (len(after), after == data))
    if after != data:
        for i, (a, b) in enumerate(zip(after, data)):
            if a != b:
                print("  first difference at 0x%x: got %02x want %02x" % (i, a, b))
                break
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
