#!/usr/bin/env python3
"""Recover HIDL interface facts from a device's own Android APK/DEX.

Porting a Sailfish adaptation onto a vendor HIDL HAL means writing a client for
an interface whose .hal file we do not have. The device does ship an
authoritative copy of it, though: the Java classes that hidl-gen generated for
the Android service that drives the same HAL. For VoLTE that is
/system/system_ext/priv-app/ims/ims.apk, which contains the whole of
vendor.qti.hardware.radio.ims.

This reads those classes straight out of classes.dex -- no apktool, no baksmali,
no network -- and answers the three questions a libgbinder client actually needs:

    enums      what integer does this constant have on THIS device
    codes      what transaction code does this method have
    fields     what fields does this struct have
    protos     what does this method's signature look like

Every answer comes from the device's own build, which is the point: guessing
these from an upstream fork that targets a different interface generation is how
you end up mapping REGISTERED onto the wrong value.

Usage:
    hidl-from-apk.py enums  ims.apk 'ims/V1_0/RegState'
    hidl-from-apk.py codes  ims.apk 'ims/V1_0/IImsRadio$Proxy'
    hidl-from-apk.py fields ims.apk 'ims/V1_0/ServiceStatusInfo'
    hidl-from-apk.py protos ims.apk 'ims/V1_0/IImsRadio;' [method ...]
    hidl-from-apk.py list   ims.apk 'radio/ims'

Sanity-check `codes` output before trusting it: it should reproduce whatever
codes your existing client already hardcodes.
"""
import sys
import struct
import zipfile


# ---------------------------------------------------------------- dex basics

def uleb128(b, o):
    r = s = 0
    while True:
        x = b[o]
        o += 1
        r |= (x & 0x7F) << s
        if not x & 0x80:
            return r, o
        s += 7


def load_dex(path):
    if path.endswith(".dex"):
        return [open(path, "rb").read()]
    z = zipfile.ZipFile(path)
    return [z.read(n) for n in z.namelist() if n.endswith(".dex")]


class Dex:
    def __init__(self, d):
        self.d = d
        (self.string_ids_size, self.string_ids_off,
         self.type_ids_size, self.type_ids_off,
         self.proto_ids_size, self.proto_ids_off,
         self.field_ids_size, self.field_ids_off,
         self.method_ids_size, self.method_ids_off,
         self.class_defs_size, self.class_defs_off) = struct.unpack_from("<12I", d, 56)

    def string(self, i):
        off = struct.unpack_from("<I", self.d, self.string_ids_off + 4 * i)[0]
        _, off = uleb128(self.d, off)          # skip the utf16 length
        return self.d[off:self.d.index(b"\x00", off)].decode("utf-8", "replace")

    def type(self, i):
        return self.string(struct.unpack_from("<I", self.d, self.type_ids_off + 4 * i)[0])

    def field(self, i):
        cls, typ = struct.unpack_from("<HH", self.d, self.field_ids_off + 8 * i)
        name = struct.unpack_from("<I", self.d, self.field_ids_off + 8 * i + 4)[0]
        return self.type(cls), self.type(typ), self.string(name)

    def method(self, i):
        cls, proto = struct.unpack_from("<HH", self.d, self.method_ids_off + 8 * i)
        name = struct.unpack_from("<I", self.d, self.method_ids_off + 8 * i + 4)[0]
        return self.type(cls), proto, self.string(name)

    def proto(self, i):
        _shorty, ret, params = struct.unpack_from("<3I", self.d, self.proto_ids_off + 12 * i)
        args = []
        if params:
            n = struct.unpack_from("<I", self.d, params)[0]
            args = [self.type(struct.unpack_from("<H", self.d, params + 4 + 2 * k)[0])
                    for k in range(n)]
        return self.type(ret), args

    def classes(self, want):
        """Yield (descriptor, class_data_off, static_values_off) for matches."""
        for i in range(self.class_defs_size):
            base = self.class_defs_off + 32 * i
            (class_idx, _af, _sc, _io, _sf, _ao,
             class_data_off, static_values_off) = struct.unpack_from("<8I", self.d, base)
            desc = self.type(class_idx)
            if want(desc) and class_data_off:
                yield desc, class_data_off, static_values_off

    def class_data(self, off):
        """Return (static_fields, instance_fields, direct, virtual) index lists."""
        d, o = self.d, off
        n_static, o = uleb128(d, o)
        n_inst, o = uleb128(d, o)
        n_direct, o = uleb128(d, o)
        n_virtual, o = uleb128(d, o)

        def fields(n):
            out, idx = [], 0
            nonlocal o
            for _ in range(n):
                diff, o = uleb128(d, o)
                _acc, o = uleb128(d, o)
                idx += diff
                out.append(idx)
            return out

        def methods(n):
            out, idx = [], 0
            nonlocal o
            for _ in range(n):
                diff, o = uleb128(d, o)
                _acc, o = uleb128(d, o)
                code, o = uleb128(d, o)
                idx += diff
                out.append((idx, code))
            return out

        return fields(n_static), fields(n_inst), methods(n_direct), methods(n_virtual)


def encoded_array(d, off):
    """Decode a static_values encoded_array into a list of Python values."""
    if not off:
        return []
    values = []
    cnt, o = uleb128(d, off)
    for _ in range(cnt):
        arg = d[o]
        o += 1
        vtype, varg = arg & 0x1F, (arg >> 5) & 0x7
        if vtype == 0x1E:                      # NULL
            values.append(None)
            continue
        if vtype == 0x1F:                      # BOOLEAN
            values.append(bool(varg))
            continue
        size = varg + 1
        v = int.from_bytes(d[o:o + size], "little")
        o += size
        if vtype in (0x00, 0x02, 0x03, 0x04, 0x06):   # signed
            bits = size * 8
            if v >= 1 << (bits - 1):
                v -= 1 << bits
        values.append(v)
    return values


# ------------------------------------------------------- dalvik instructions

def _sizes():
    """Instruction length in 16-bit code units, indexed by opcode."""
    t = [1] * 256
    for op, n in {
        0x02: 2, 0x03: 3, 0x05: 2, 0x06: 3, 0x08: 2, 0x09: 3,
        0x13: 2, 0x14: 3, 0x15: 2, 0x16: 2, 0x17: 3, 0x18: 5, 0x19: 2,
        0x1A: 2, 0x1B: 3, 0x1C: 2, 0x1F: 2, 0x20: 2, 0x22: 2, 0x23: 2,
        0x24: 3, 0x25: 3, 0x26: 3, 0x29: 2, 0x2A: 3, 0x2B: 3, 0x2C: 3,
        0xFA: 4, 0xFB: 4, 0xFC: 3, 0xFD: 3, 0xFE: 2, 0xFF: 2,
    }.items():
        t[op] = n
    for lo, hi, n in ((0x2D, 0x32, 2), (0x32, 0x3E, 2), (0x44, 0x52, 2),
                      (0x52, 0x60, 2), (0x60, 0x6E, 2), (0x6E, 0x73, 3),
                      (0x74, 0x79, 3), (0x90, 0xB0, 2), (0xD0, 0xE3, 2)):
        for op in range(lo, hi):
            t[op] = n
    return t


SIZES = _sizes()


def walk(insns):
    """Yield (offset, opcode) over an insns array, skipping switch/array payloads."""
    i, n = 0, len(insns)
    while i < n:
        unit = insns[i]
        if unit == 0x0100:                                  # packed-switch payload
            i += 4 + insns[i + 1] * 2
            continue
        if unit == 0x0200:                                  # sparse-switch payload
            i += 2 + insns[i + 1] * 4
            continue
        if unit == 0x0300:                                  # fill-array-data payload
            width = insns[i + 1]
            count = insns[i + 2] | (insns[i + 3] << 16)
            i += 4 + (width * count + 1) // 2
            continue
        yield i, unit & 0xFF
        i += SIZES[unit & 0xFF]


# ------------------------------------------------------------------ commands

def cmd_list(dex, pattern):
    seen = {dex.type(i) for i in range(dex.type_ids_size)}
    for t in sorted(x for x in seen if pattern in x):
        print(t)


def cmd_enums(dex, pattern):
    for desc, cdo, svo in dex.classes(lambda d: pattern in d):
        statics, _i, _d, _v = dex.class_data(cdo)
        values = encoded_array(dex.d, svo)
        print(f"\n=== {desc} ===")
        rows = []
        for n, fidx in enumerate(statics):
            _cls, typ, name = dex.field(fidx)
            rows.append((name, values[n] if n < len(values) else None, typ))
        for name, val, typ in sorted(rows, key=lambda r: (r[1] is None, r[1])):
            print(f"    {name} = {val}   ({typ})")


def cmd_fields(dex, pattern):
    # DEX sorts fields by name, so this is the field *set*, not the .hal
    # declaration order -- cross-check the order against a known struct before
    # laying out C.
    for desc, cdo, _svo in dex.classes(lambda d: pattern in d):
        _s, inst, _d, _v = dex.class_data(cdo)
        print(f"\n=== {desc} ===   ({len(inst)} fields, sorted by name)")
        for fidx in inst:
            _cls, typ, name = dex.field(fidx)
            print(f"    {typ:60s} {name}")


def cmd_protos(dex, pattern, only):
    for i in range(dex.method_ids_size):
        cls, pidx, name = dex.method(i)
        if pattern not in cls or (only and name not in only):
            continue
        ret, args = dex.proto(pidx)
        print(f"{name}({', '.join(args)}) -> {ret}")


def cmd_codes(dex, pattern):
    """Read the transact() code out of each HIDL proxy method body.

    hidl-gen numbers methods by declaration order, which the DEX layout does not
    preserve. But every generated proxy body ends in
        mRemote.transact(<code>, _hidl_request, _hidl_reply, <flags>)
    so tracking constants per register up to the transact call recovers it.
    """
    transact = {i for i in range(dex.method_ids_size)
                if dex.method(i)[2] == "transact"}
    rows = []
    for desc, cdo, _svo in dex.classes(lambda d: pattern in d):
        _s, _i, direct, virtual = dex.class_data(cdo)
        for midx, code_off in direct + virtual:
            if not code_off:
                continue
            name = dex.method(midx)[2]
            insns_size = struct.unpack_from("<I", dex.d, code_off + 12)[0]
            insns = struct.unpack_from(f"<{insns_size}H", dex.d, code_off + 16)

            const, found = {}, None
            for off, op in walk(insns):
                if op == 0x12:                                  # const/4 vA, #+B
                    a, b = (insns[off] >> 8) & 0xF, (insns[off] >> 12) & 0xF
                    const[a] = b - 16 if b > 7 else b
                elif op == 0x13:                                # const/16 vAA, #+BBBB
                    a, v = (insns[off] >> 8) & 0xFF, insns[off + 1]
                    const[a] = v - 65536 if v > 32767 else v
                elif op == 0x14:                                # const vAA, #+BBBBBBBB
                    a = (insns[off] >> 8) & 0xFF
                    const[a] = insns[off + 1] | (insns[off + 2] << 16)
                elif 0x6E <= op <= 0x72:                        # invoke-kind
                    if insns[off + 1] in transact:
                        dd = insns[off + 2]
                        args = [(dd >> (4 * k)) & 0xF for k in range(4)]
                        if ((insns[off] >> 12) & 0xF) >= 2:
                            found = const.get(args[1])
                        break
                elif 0x74 <= op <= 0x78:                        # invoke-kind/range
                    if insns[off + 1] in transact:
                        found = const.get(insns[off + 2] + 1)
                        break
            if found is not None:
                rows.append((found, name))
    for code, name in sorted(rows):
        # HIDL reserves 0x0F000000+ for interfaceChain/ping/debug and friends
        mark = "" if code < 0x0F000000 else "   (hidl base)"
        print(f"{code:4d}  {name}{mark}" if not mark else f"{code:10d}  {name}{mark}")


def cmd_layout(dex, pattern):
    """Recover a HIDL struct's byte offsets from its generated parcel reader.

    `fields` gives the field *set* but not the order, because DEX sorts fields
    by name -- and the order is what a C struct has to reproduce. The generated
    readEmbeddedFromParcel() does one
        _hidl_blob.getInt32(_hidl_offset + N)
        iput <field>
    per primitive, so pairing each wide constant with the iput that follows it
    recovers the offsets directly. Fields read through a nested helper (strings,
    vecs, embedded structs) show up at their own offset too, since the offset
    constant is still materialised before the call.

    Cross-check the result against a struct whose layout you already know
    before trusting it.
    """
    for desc, cdo, _svo in dex.classes(lambda d: pattern in d):
        _s, _i, direct, virtual = dex.class_data(cdo)
        for midx, code_off in direct + virtual:
            if not code_off or dex.method(midx)[2] != "readEmbeddedFromParcel":
                continue
            insns_size = struct.unpack_from("<I", dex.d, code_off + 12)[0]
            insns = struct.unpack_from(f"<{insns_size}H", dex.d, code_off + 16)

            print(f"\n=== {desc} ===")
            last_wide, seen = None, set()
            for off, op in walk(insns):
                if op == 0x16:                                  # const-wide/16
                    v = insns[off + 1]
                    last_wide = v - 65536 if v > 32767 else v
                elif op == 0x17:                                # const-wide/32
                    last_wide = insns[off + 1] | (insns[off + 2] << 16)
                elif 0x59 <= op <= 0x5F:                        # iput family
                    fidx = insns[off + 1]
                    _cls, typ, name = dex.field(fidx)
                    if last_wide is not None and name not in seen:
                        seen.add(name)
                        print(f"    offset {last_wide:3d}   {name:24s} {typ}")
                        last_wide = None


COMMANDS = {
    "list": cmd_list, "enums": cmd_enums, "fields": cmd_fields,
    "codes": cmd_codes, "layout": cmd_layout,
}

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(2)
    cmd, path, pattern = sys.argv[1], sys.argv[2], sys.argv[3]
    for blob in load_dex(path):
        dex = Dex(blob)
        if cmd == "protos":
            cmd_protos(dex, pattern, set(sys.argv[4:]) or None)
        elif cmd in COMMANDS:
            COMMANDS[cmd](dex, pattern)
        else:
            print(f"unknown command: {cmd}", file=sys.stderr)
            sys.exit(2)
