#!/usr/bin/python3
# Disassemble a raw address range out of an aarch64 .so.
#
# disasm.py works from the dynamic symbol table, so it can only reach exported
# functions. The interesting qcril logic is mostly static -- update_ims_rte at
# 0x4ff2e4 has no symbol at all, it was located from the string reference -- so
# this variant takes an address and a byte count instead of a name.
#
#   disasmat.py <lib.so> <vaddr> <nbytes>
#
# Branch targets inside the range are annotated as L<addr> so the control flow
# can be followed by eye; calls out are resolved against the dynamic symbols
# where possible, and against the relocations for PLT entries where not.
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
                out[int(f[0], 16)] = f[-1]
            except ValueError:
                pass
    return out


def strings_at(path):
    """vaddr -> string, for annotating adrp/add pairs into .rodata."""
    d, secs = sections(path)
    out = {}
    for addr, off, sz in secs:
        if not addr:
            continue
        blob = d[off:off + sz]
        start = None
        for i, b in enumerate(blob):
            if 0x20 <= b < 0x7f:
                if start is None:
                    start = i
            else:
                if start is not None and b == 0 and i - start >= 4:
                    out[addr + start] = blob[start:i].decode("ascii")
                start = None
    return out


def main():
    path = sys.argv[1]
    vaddr = int(sys.argv[2], 0)
    size = int(sys.argv[3], 0)
    code = read(path, vaddr, size)
    syms = symbols(path)
    strs = strings_at(path)
    md = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
    md.detail = False
    ins_list = list(md.disasm(code, vaddr))

    # Collect intra-range branch targets so they can be labelled.
    targets = set()
    for ins in ins_list:
        if ins.mnemonic.startswith(("b", "cb", "tb")) and "#0x" in ins.op_str:
            try:
                t = int(ins.op_str.split("#")[-1], 0)
            except ValueError:
                continue
            if vaddr <= t < vaddr + size:
                targets.add(t)

    # Track adrp results so adrp/add pairs can be turned into string literals.
    pending = {}
    for ins in ins_list:
        if ins.address in targets:
            print("L%x:" % ins.address)
        note = ""
        if ins.mnemonic == "adrp":
            reg, imm = [x.strip() for x in ins.op_str.split(",")]
            pending[reg] = int(imm.lstrip("#"), 0)
        elif ins.mnemonic == "add" and "#" in ins.op_str:
            f = [x.strip() for x in ins.op_str.split(",")]
            if len(f) == 3 and f[1] in pending and f[2].startswith("#"):
                try:
                    a = pending[f[1]] + int(f[2].lstrip("#"), 0)
                    if a in strs:
                        note = '   ; "%s"' % strs[a][:70]
                except ValueError:
                    pass
        elif ins.mnemonic in ("bl", "b") or ins.mnemonic.startswith(("cb", "tb")):
            try:
                t = int(ins.op_str.split("#")[-1], 0)
                if t in syms:
                    note = "   ; %s" % syms[t]
                elif t in targets:
                    note = "   ; -> L%x" % t
                elif ins.mnemonic == "bl":
                    note = "   ; sub_%x" % t
            except ValueError:
                pass
        print("%08x  %-8s %s%s" % (ins.address, ins.mnemonic, ins.op_str, note))


main()
