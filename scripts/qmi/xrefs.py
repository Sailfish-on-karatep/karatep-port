#!/usr/bin/python3
# Find every BL that targets a given address in an aarch64 .so.
#
# Needed because the interesting qcril functions are static: nothing in the
# symbol table points at them, so "who calls this" cannot be answered by
# objdump. A BL is 100101 followed by a signed 26-bit word displacement, so the
# whole of .text can just be scanned four bytes at a time.
#
#   xrefs.py <lib.so> <target-vaddr> [<target-vaddr> ...]
import struct
import sys


def sections(path):
    d = open(path, "rb").read()
    e_shoff, = struct.unpack_from("<Q", d, 0x28)
    e_shentsize, e_shnum = struct.unpack_from("<HH", d, 0x3a)
    out = []
    for i in range(e_shnum):
        o = e_shoff + i * e_shentsize
        addr, off, size = struct.unpack_from("<QQQ", d, o + 0x10)
        flags, = struct.unpack_from("<Q", d, o + 8)
        # SHF_EXECINSTR
        if addr and (flags & 4):
            out.append((addr, off, size))
    return d, out


def main():
    path = sys.argv[1]
    targets = set(int(a, 0) for a in sys.argv[2:])
    d, secs = sections(path)
    hits = dict((t, []) for t in targets)
    for addr, off, size in secs:
        for i in range(0, size & ~3, 4):
            w, = struct.unpack_from("<I", d, off + i)
            if (w >> 26) != 0b100101:
                continue
            imm = w & 0x3ffffff
            if imm & 0x2000000:
                imm -= 0x4000000
            src = addr + i
            dst = src + imm * 4
            if dst in hits:
                hits[dst].append(src)
    for t in sorted(hits):
        print("0x%x: %d caller(s)" % (t, len(hits[t])))
        for s in hits[t]:
            print("   bl from 0x%x" % s)


main()
