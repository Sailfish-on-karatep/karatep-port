#!/usr/bin/python3
# Disassemble one exported function out of an aarch64 .so.
#
# The system objdump on this host is built without aarch64 support ("can't
# disassemble for architecture UNKNOWN!") even though readelf reads the header
# fine, so capstone does the work instead. Section headers give the vaddr ->
# file offset mapping.
import struct
import subprocess
import sys

sys.path.insert(0, "/opencloud/work/telephony/pylib")
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN


def sections(path):
    d = open(path, "rb").read()
    e_shoff, = struct.unpack_from("<Q", d, 0x28)
    e_shentsize, e_shnum = struct.unpack_from("<HH", d, 0x3a)
    out = []
    for i in range(e_shnum):
        o = e_shoff + i * e_shentsize
        addr, off, size = struct.unpack_from("<QQQ", d, o + 0x10)
        out.append((addr, off, size))
    return d, out


def read(path, vaddr, size):
    d, secs = sections(path)
    for addr, off, sz in secs:
        if addr and addr <= vaddr < addr + sz:
            delta = vaddr - addr
            return d[off + delta:off + delta + size]
    raise SystemExit("vaddr 0x%x not in any section" % vaddr)


def symbols(path):
    out = {}
    for line in subprocess.run(["objdump", "-T", path], capture_output=True,
                               text=True).stdout.splitlines():
        f = line.split()
        if len(f) >= 7 and f[3] == ".text":
            try:
                out[f[-1]] = (int(f[0], 16), int(f[4], 16))
            except ValueError:
                pass
    return out


def main():
    path, name = sys.argv[1:3]
    limit = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0
    syms = symbols(path)
    if name not in syms:
        raise SystemExit("no symbol %s" % name)
    vaddr, size = syms[name]
    if limit:
        size = min(size, limit)
    code = read(path, vaddr, size)
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    byaddr = dict((a, n) for n, (a, _) in syms.items())
    for ins in md.disasm(code, vaddr):
        note = ""
        if ins.mnemonic == "bl":
            try:
                t = int(ins.op_str.strip("#"), 0)
                note = "   ; %s" % byaddr.get(t, "sub_%x" % t)
            except ValueError:
                pass
        print("%08x  %-8s %s%s" % (ins.address, ins.mnemonic, ins.op_str, note))


main()
