#!/usr/bin/python3
#
# Minimal Qualcomm DIAG client, speaking the EFS2 subsystem over /dev/diag.
#
# This is the same channel QPST/QXDM drive over USB. Talking to it locally
# avoids having to switch the USB composition (which would drop the RNDIS
# link we are connected over).
#
# The transport is defined by our own kernel, in
# kernel/lenovo/msm8937/drivers/char/diag:
#
#   write(2): int pkt_type (USER_SPACE_DATA_TYPE) followed by an HDLC-framed
#             DIAG packet                                 -- diagchar_write()
#   read(2):  int data_type, int num_entries, then per entry
#             int len followed by len bytes of HDLC-framed data
#                                    -- diagchar_read() / diag_md_copy_to_user()
#
# and responses only arrive on the char device once logging has been switched
# to MEMORY_DEVICE_MODE with a non-zero peripheral mask (diag_switch_logging()
# rejects an empty mask outright).

import fcntl
import os
import select
import struct
import sys

DIAG = "/dev/diag"

USER_SPACE_DATA_TYPE = 0x00000020
DIAG_IOCTL_SWITCH_LOGGING = 7
MEMORY_DEVICE_MODE = 2
DIAG_CON_ALL = 0x1F          # APSS|MPSS|LPASS|WCNSS|SENSORS

DIAG_SUBSYS_CMD_F = 0x4B
DIAG_SUBSYS_FS = 0x13        # EFS2

EFS2_HELLO = 0
EFS2_QUERY = 1
EFS2_OPEN = 2
EFS2_CLOSE = 3
EFS2_READ = 4
EFS2_WRITE = 5
EFS2_OPENDIR = 11
EFS2_READDIR = 12
EFS2_CLOSEDIR = 13
EFS2_STAT = 15

O_RDONLY = 0
O_WRONLY = 1
O_RDWR = 2
O_CREAT = 0o100
O_TRUNC = 0o1000


def crc16(data):
    """CRC-16/X-25, as used by DIAG's HDLC framing."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def hdlc_encode(payload):
    frame = payload + struct.pack("<H", crc16(payload))
    out = bytearray()
    for b in frame:
        if b in (0x7E, 0x7D):
            out += bytes([0x7D, b ^ 0x20])
        else:
            out.append(b)
    out.append(0x7E)
    return bytes(out)


def hdlc_decode(frame):
    if frame.endswith(b"\x7e"):
        frame = frame[:-1]
    out = bytearray()
    esc = False
    for b in frame:
        if esc:
            out.append(b ^ 0x20)
            esc = False
        elif b == 0x7D:
            esc = True
        else:
            out.append(b)
    return bytes(out[:-2])       # drop the CRC


class Diag:
    def __init__(self):
        self.fd = os.open(DIAG, os.O_RDWR)
        param = bytearray(struct.pack("<IIB", MEMORY_DEVICE_MODE,
                                      DIAG_CON_ALL, 0))
        fcntl.ioctl(self.fd, DIAG_IOCTL_SWITCH_LOGGING, param, True)

    def close(self):
        os.close(self.fd)

    def send(self, payload):
        os.write(self.fd, struct.pack("<i", USER_SPACE_DATA_TYPE)
                 + hdlc_encode(payload))

    def recv(self, want_prefix, timeout=4.0):
        """Read frames until one starts with want_prefix."""
        deadline = select.select
        import time
        end = time.time() + timeout
        while time.time() < end:
            r, _, _ = deadline([self.fd], [], [], max(0.05, end - time.time()))
            if not r:
                continue
            buf = os.read(self.fd, 65536)
            if len(buf) < 8:
                continue
            data_type, num = struct.unpack_from("<ii", buf, 0)
            if data_type != USER_SPACE_DATA_TYPE:
                continue
            off = 8
            for _ in range(num):
                if off + 4 > len(buf):
                    break
                ln, = struct.unpack_from("<i", buf, off)
                off += 4
                if ln <= 0 or off + ln > len(buf):
                    break
                for frame in buf[off:off + ln].split(b"\x7e"):
                    if not frame:
                        continue
                    pkt = hdlc_decode(frame + b"\x7e")
                    if pkt.startswith(want_prefix):
                        return pkt
                off += ln
        return None

    def efs(self, cmd, payload=b""):
        req = struct.pack("<BBH", DIAG_SUBSYS_CMD_F, DIAG_SUBSYS_FS, cmd)
        self.send(req + payload)
        rsp = self.recv(req)
        return rsp[4:] if rsp else None


def cstr(path):
    return path.encode() + b"\x00"


def opendir(d, path):
    r = d.efs(EFS2_OPENDIR, cstr(path))
    if r is None or len(r) < 8:
        return None, -1
    dirp, err = struct.unpack_from("<Ii", r, 0)
    return dirp, err


def readdir(d, dirp, seq):
    r = d.efs(EFS2_READDIR, struct.pack("<II", dirp, seq))
    if r is None or len(r) < 36:
        return None
    _dirp, _seq, err, etype, mode, size = struct.unpack_from("<IIiIII", r, 0)
    name = r[36:].split(b"\x00")[0].decode("ascii", "replace")
    return err, etype, mode, size, name


def closedir(d, dirp):
    d.efs(EFS2_CLOSEDIR, struct.pack("<I", dirp))


def read_file(d, path, maxlen=4096):
    r = d.efs(EFS2_OPEN, struct.pack("<II", O_RDONLY, 0) + cstr(path))
    if r is None or len(r) < 8:
        return None, "open: no response"
    fd, err = struct.unpack_from("<ii", r, 0)
    if err != 0 or fd < 0:
        return None, "open: errno %d" % err
    out = b""
    offset = 0
    while offset < maxlen:
        r = d.efs(EFS2_READ, struct.pack("<III", fd, 512, offset))
        if r is None or len(r) < 16:
            break
        _fd, _off, nread, err = struct.unpack_from("<IIii", r, 0)
        if err != 0 or nread <= 0:
            break
        out += r[16:16 + nread]
        offset += nread
        if nread < 512:
            break
    d.efs(EFS2_CLOSE, struct.pack("<i", fd))
    return out, None


def main():
    d = Diag()

    hello = d.efs(EFS2_HELLO, struct.pack("<10I", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))
    if hello is None:
        print("EFS2 HELLO: no response -- transport not working")
        d.close()
        return 1
    print("EFS2 HELLO ok (%d bytes): %s" % (len(hello), hello[:40].hex()))

    targets = sys.argv[1:] or ["/nv/item_files/ims"]
    for path in targets:
        print("\n=== %s ===" % path)
        dirp, err = opendir(d, path)
        if dirp is None or err != 0:
            print("  opendir failed (errno %s)" % err)
            continue
        # seq 0 is a null pseudo-entry; real entries start at 1 and the
        # directory ends with an all-zero record.
        seq = 1
        while True:
            ent = readdir(d, dirp, seq)
            if ent is None:
                print("  readdir: no response at seq %d" % seq)
                break
            err, etype, mode, size, name = ent
            if err != 0 or not name:
                break
            if mode & 0xF000 == 0x4000:
                print("  %-44s <dir>" % name)
                seq += 1
                continue
            body, e = read_file(d, path + "/" + name)
            if e:
                print("  %-44s %5d  <%s>" % (name, size, e))
            else:
                print("  %-44s %5d  %s" % (name, size, body.hex()))
            seq += 1
        closedir(d, dirp)

    d.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
