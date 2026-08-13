#!/usr/bin/python3
#
# Point an existing software carrier config at a different operator.
#
# A config says which SIMs it applies to in the MCFG_TRL trailer at the end of
# its payload, as TLVs of [tag u8][len u16][value]:
#
#   tag 1, 5  the MCFG version, [minor, carrier, oem, family]
#   tag 3     the config's name
#   tag 4     [flag u8][count u8] then count * u32, each an ICCID/IIN prefix
#             read as a decimal number (Jio's are 8991840 .. 8991874)
#   tag 6     [flag u8][count u8] then count * (MCC u16, MNC u16)
#
# ROW_Generic_3GPP declares an empty tag 6, which is what makes it the config
# every unmatched SIM falls through to.
#
# Entries are overwritten in place rather than appended: that keeps every
# length in the file -- the TLV, the enclosing record, the ELF segment --
# exactly as it was, so only the three SHA-256 hashes need redoing. These
# files carry no signature and this modem checks only the hashes.

import hashlib
import struct
import sys


def segments(data):
    e_phoff, = struct.unpack_from("<I", data, 0x1c)
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 0x2a)
    out = []
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        _, p_offset, _, _, p_filesz = struct.unpack_from("<5I", data, o)
        out.append((p_offset, p_filesz))
    return e_phoff + e_phnum * e_phentsize, out


def trailer_tlvs(payload):
    """(offset, tag, length) of each TLV in MCFG_TRL, offsets into payload."""
    k = payload.find(b"MCFG_TRL")
    if k < 0:
        raise ValueError("no MCFG_TRL")
    off = k + 13                      # name, its NUL, and four bytes of header
    out = []
    while off + 3 <= len(payload):
        tag = payload[off]
        ln, = struct.unpack_from("<H", payload, off + 1)
        if off + 3 + ln > len(payload):
            break
        out.append((off + 3, tag, ln))
        off += 3 + ln
    return out


def retarget(data, mcc, mnc, iin):
    hdr_len, segs = segments(data)
    hash_off, hash_len = segs[1]
    mcfg_off, mcfg_len = segs[2]
    out = bytearray(data)
    payload = bytearray(out[mcfg_off:mcfg_off + mcfg_len])

    for voff, tag, ln in trailer_tlvs(payload):
        if tag == 3:
            print("  config name: %s" % payload[voff:voff + ln].decode())
        elif tag == 4 and ln >= 6:
            old, = struct.unpack_from("<I", payload, voff + 2)
            struct.pack_into("<I", payload, voff + 2, iin)
            print("  iin  [0]: %d -> %d" % (old, iin))
        elif tag == 6 and ln >= 6:
            oa, ob = struct.unpack_from("<HH", payload, voff + 2)
            struct.pack_into("<HH", payload, voff + 2, mcc, mnc)
            print("  plmn [0]: %d/%d -> %d/%d" % (oa, ob, mcc, mnc))

    # qcril skips a config whose version is not newer than the one already
    # active, so the minor has to move for the load to be attempted at all.
    version = bytes(payload[20:24])
    if version[0] == 0xFF:
        raise ValueError("minor version is already 0xff")
    bumped = bytes([version[0] + 1]) + version[1:]
    count = payload.count(version)
    if count != 3:
        raise ValueError("expected 3 copies of the version, found %d" % count)
    payload = bytearray(payload.replace(version, bumped))
    print("  MCFG version: %s -> %s" % (version.hex(), bumped.hex()))

    out[mcfg_off:mcfg_off + mcfg_len] = payload
    out[hash_off + 40:hash_off + 72] = hashlib.sha256(bytes(out[:hdr_len])).digest()
    out[hash_off + 104:hash_off + 136] = hashlib.sha256(bytes(payload)).digest()
    return bytes(out)


def main():
    src, dst, mcc, mnc, iin = sys.argv[1:6]
    data = open(src, "rb").read()
    print("%s -> %s" % (src, dst))
    open(dst, "wb").write(retarget(data, int(mcc), int(mnc), int(iin)))
    print("  wrote %d bytes" % len(data))


if __name__ == "__main__":
    main()
