#!/usr/bin/python3
#
# List the records in a Qualcomm software carrier config (mcfg_sw .mbn).
#
# The payload sits in the third ELF program header and starts "MCFG", followed
# by a header and then a flat sequence of records. Each record is
#
#   [len u32][type u16][... type-specific body ...]
#
# The two that matter here are the EFS item records, which carry a
# NUL-terminated path and a value, and the selection records at the top of the
# file, which say which SIMs the config applies to.

import struct
import sys


def payload(path):
    data = open(path, "rb").read()
    e_phoff, = struct.unpack_from("<I", data, 0x1c)
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 0x2a)
    o = e_phoff + 2 * e_phentsize
    _, p_offset, _, _, p_filesz = struct.unpack_from("<5I", data, o)
    return data[p_offset:p_offset + p_filesz]


def records(buf):
    off = 8 + 16          # "MCFG" + type/num + version/carrier block
    # find the first record by scanning for a plausible length prefix
    off = 0
    magic = buf.index(b"MCFG")
    off = magic + 4
    fmt, num_items = struct.unpack_from("<HH", buf, off)
    off += 4
    carrier, = struct.unpack_from("<H", buf, off)
    off += 2 + 2
    version = buf[off - 4 + 2:off]
    off = 24              # records start after the fixed header
    out = []
    while off + 6 <= len(buf):
        ln, = struct.unpack_from("<I", buf, off)
        if ln < 2 or off + 4 + ln > len(buf):
            break
        typ, = struct.unpack_from("<H", buf, off + 4)
        body = buf[off + 6:off + 4 + ln]
        out.append((typ, body))
        off += 4 + ln
    return num_items, out


def show(path):
    buf = payload(path)
    print("=== %s (%d bytes payload) ===" % (path, len(buf)))
    print("header: %s" % buf[:32].hex())
    num, recs = records(buf)
    print("declared items: %d, parsed records: %d" % (num, len(recs)))
    kinds = {}
    for typ, body in recs:
        kinds[typ] = kinds.get(typ, 0) + 1
    print("record types: %s" % sorted(kinds.items()))
    for typ, body in recs:
        if typ == 1:                       # NV item, by legacy item number
            print("  nv   %s" % body[:24].hex())
        elif typ in (2, 4):                # EFS file item
            name = body[2:].split(b"\x00")[0]
            rest = body[2 + len(name) + 1:]
            print("  efs%d %-58s %s" % (typ, name.decode("ascii", "replace"),
                                        rest[:24].hex()))
        else:
            txt = "".join(chr(c) if 32 <= c < 127 else "." for c in body[:48])
            print("  t%-3d %s  |%s" % (typ, body[:32].hex(), txt))


for p in sys.argv[1:]:
    show(p)
