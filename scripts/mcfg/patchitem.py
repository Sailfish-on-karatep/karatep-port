#!/usr/bin/python3
#
# Change a single NV item's value byte inside a software carrier config.
#
# Writing the NV directly does not stick: the modem re-applies its activated
# carrier config on every boot, so any item the config owns is restored. To
# change sms_domain_pref the config itself has to carry the new value.
#
# Same framing karatep-modem-config.py uses -- the item path, then the record
# framing, then a type tag and the value byte -- and the same version bump and
# re-hash as retarget.py, since qcril skips a config whose version is not newer
# than the one already active.
import hashlib
import struct
import sys

ITEM_PREFIX = b"\x00\x02\x00\x02\x00\x07"


def segments(data):
    e_phoff, = struct.unpack_from("<I", data, 0x1c)
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 0x2a)
    out = []
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        _, p_offset, _, _, p_filesz = struct.unpack_from("<5I", data, o)
        out.append((p_offset, p_filesz))
    return e_phoff + e_phnum * e_phentsize, out


def main():
    src, dst = sys.argv[1:3]
    path = sys.argv[3].encode()
    value = int(sys.argv[4])

    data = open(src, "rb").read()
    hdr_len, segs = segments(data)
    hash_off, _ = segs[1]
    mcfg_off, mcfg_len = segs[2]
    out = bytearray(data)
    payload = bytearray(out[mcfg_off:mcfg_off + mcfg_len])

    needle = path + ITEM_PREFIX
    at = payload.find(needle)
    if at < 0:
        raise SystemExit("item not found: %s" % path.decode())
    if payload.find(needle, at + 1) >= 0:
        raise SystemExit("item appears more than once: %s" % path.decode())
    vpos = at + len(needle)
    print("  %s: %d -> %d" % (path.decode(), payload[vpos], value))
    payload[vpos] = value

    version = bytes(payload[20:24])
    if version[0] == 0xFF:
        raise SystemExit("minor version is already 0xff")
    bumped = bytes([version[0] + 1]) + version[1:]
    n = payload.count(version)
    if n != 3:
        raise SystemExit("expected 3 copies of the version, found %d" % n)
    payload = bytearray(payload.replace(version, bumped))
    print("  MCFG version: %s -> %s" % (version.hex(), bumped.hex()))

    out[mcfg_off:mcfg_off + mcfg_len] = payload
    out[hash_off + 40:hash_off + 72] = hashlib.sha256(bytes(out[:hdr_len])).digest()
    out[hash_off + 104:hash_off + 136] = hashlib.sha256(bytes(payload)).digest()
    open(dst, "wb").write(bytes(out))
    print("  wrote %s (%d bytes)" % (dst, len(out)))


main()
