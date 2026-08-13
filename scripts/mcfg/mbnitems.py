#!/usr/bin/python3
#
# Print the EFS item values a software carrier config would write.
#
# Records in the MCFG payload are [len u32, counting itself][body], and an EFS
# item body is
#
#   [type u16][pad u16][pad u16][path_len u16][path incl NUL][u16][len u16][blob]
#
# where the blob's first byte is a value-type tag (0x07) and the rest is the
# value. Used to check that an activated config really is the one whose values
# turned up in NV.

import struct
import sys


def payload(path):
    data = open(path, "rb").read()
    e_phoff, = struct.unpack_from("<I", data, 0x1c)
    e_phentsize, _ = struct.unpack_from("<HH", data, 0x2a)
    _, off, _, _, ln = struct.unpack_from("<5I", data, e_phoff + 2 * e_phentsize)
    return data[off:off + ln]


def items(buf):
    out = {}
    off = 0x18
    while off + 6 <= len(buf):
        ln, = struct.unpack_from("<I", buf, off)
        if ln < 6 or off + ln > len(buf):
            break
        body = buf[off + 4:off + ln]
        off += ln
        if len(body) < 8 or body[0:2] != b"\x02\x19":
            continue
        path_len, = struct.unpack_from("<H", body, 6)
        path = body[8:8 + path_len]
        if b"\x00" not in path:
            continue
        rest = body[8 + path_len:]
        if len(rest) < 5:
            continue
        _a, blob_len = struct.unpack_from("<HH", rest, 0)
        out[path.split(b"\x00")[0].decode()] = rest[4:4 + blob_len]
    return out


def main():
    src = sys.argv[1]
    want = sys.argv[2:]
    got = items(payload(src))
    print("%s: %d item(s)" % (src, len(got)))
    for name in (want or sorted(got)):
        v = got.get(name)
        if v is None:
            print("  %-52s <absent>" % name)
        else:
            print("  %-52s tag=%02x %s" % (name, v[0], v[1:].hex()))


if __name__ == "__main__":
    main()
