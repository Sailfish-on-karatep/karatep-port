#!/usr/bin/python3
# Full map of qcril's radio-config items to the QMI messages that carry them.
#
# Each entry is {get_msg u64, set_msg u64, const char *name, item_id u64}, and
# the library holds two of these tables: one using the message pair this 2016
# modem implements, one using the pair a later modem would.
import struct

path = "qcril/libril-qc-qmi-1.so"
d = open(path, "rb").read()
e_shoff, = struct.unpack_from("<Q", d, 0x28)
e_shentsize, e_shnum = struct.unpack_from("<HH", d, 0x3a)
secs = []
for i in range(e_shnum):
    o = e_shoff + i * e_shentsize
    addr, off, size = struct.unpack_from("<QQQ", d, o + 0x10)
    secs.append((addr, off, size))


def string_at(v):
    for addr, off, size in secs:
        if addr and addr <= v < addr + size:
            fo = off + (v - addr)
            end = d.find(b"\x00", fo, fo + 200)
            if end < 0:
                return None
            s = d[fo:end]
            return s.decode("ascii", "replace") if s.isascii() else None
    return None


rows = []
for pos in range(0, len(d) - 8, 8):
    v, = struct.unpack_from("<Q", d, pos)
    if v < 0x1000:
        continue
    s = string_at(v)
    if not s or not s.startswith("QCRIL_QMI_RADIO_CONFIG_"):
        continue
    get_, = struct.unpack_from("<Q", d, pos - 16)
    set_, = struct.unpack_from("<Q", d, pos - 8)
    item, = struct.unpack_from("<Q", d, pos + 8)
    if get_ > 0xffff or set_ > 0xffff or item > 0xffff:
        continue
    rows.append((pos, get_, set_, item, s[len("QCRIL_QMI_RADIO_CONFIG_"):]))

old = [r for r in rows if r[1] < 0x80]
new = [r for r in rows if r[1] >= 0x80]
for label, group in (("legacy pair (this modem)", old), ("modern pair", new)):
    print("\n=== %s: %d items ===" % (label, len(group)))
    print("  %-46s item  get   set" % "name")
    for _, g, s_, item, name in sorted(group, key=lambda r: r[3]):
        print("  %-46s %3d  0x%02x  0x%02x" % (name, item, g, s_))
