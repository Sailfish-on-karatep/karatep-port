#!/usr/bin/python3
#
# Read qcril's IMS message-size table out of the running rild.
#
# qcril_qmi_ims_get_msg_size scans a table of 0x85 entries, stride 0x28,
# matching entry[+0] against the message id and entry[+4] against the message
# type, and returns the 64-bit size at +0x10. If the pair is not in the table it
# falls through to "return 0" -- and the only caller feeds that straight into
# qcril_malloc_adv, whose NULL result is the sole way
# qcril_qmi_ims_flow_control_event_queue can fail. So a missing table row is
# indistinguishable from the request being silently dropped, which is exactly
# what setServiceStatus does.
#
# The table pointer lives in a GOT slot at +0xfe98d0. The library uses Android's
# packed relocations, so that slot is zero in the file on disk and readelf shows
# no relocation for it -- it can only be read from a live process.
import re
import struct
import sys

LIB = "libril-qc-qmi-1.so"
GOT_SLOT = 0xfe98d0
STRIDE = 0x28
COUNT = 0x85


# The first PT_LOAD of this library has p_vaddr 0xb9000, not 0, so the load
# bias is the mapping start minus that -- adding a vaddr to the mapping start
# directly lands 0xb9000 too high and reads string data.
FIRST_LOAD_VADDR = 0xb9000


def load_base(pid):
    lo = None
    for line in open("/proc/%d/maps" % pid):
        if LIB in line:
            start = int(line.split("-")[0], 16)
            off = int(line.split()[2], 16)
            if off == 0 and (lo is None or start < lo):
                lo = start
    return None if lo is None else lo - FIRST_LOAD_VADDR


def read(pid, addr, n):
    with open("/proc/%d/mem" % pid, "rb", 0) as f:
        f.seek(addr)
        return f.read(n)


def main():
    pid = int(sys.argv[1])
    base = load_base(pid)
    if base is None:
        raise SystemExit("%s not mapped in pid %d" % (LIB, pid))
    print("lib base 0x%x" % base)
    ptr, = struct.unpack("<Q", read(pid, base + GOT_SLOT, 8))
    print("table pointer 0x%x" % ptr)
    if not ptr:
        raise SystemExit("table pointer is NULL")
    blob = read(pid, ptr, COUNT * STRIDE)
    rows = []
    for k in range(COUNT):
        msg_id, msg_type = struct.unpack_from("<II", blob, k * STRIDE)
        size, = struct.unpack_from("<Q", blob, k * STRIDE + 0x10)
        rows.append((k, msg_id, msg_type, size))
    print("\nfirst 12 rows (idx: id type size):")
    for r in rows[:12]:
        print("  %3d: id=%-4d type=%-3d size=%d" % r)
    print("\nrows for message id 30 (SET_SERVICE_STATUS):")
    hits = [r for r in rows if r[1] == 30]
    for r in hits:
        print("  %3d: id=%-4d type=%-3d size=%d" % r)
    if not hits:
        print("  *** NONE -- get_msg_size returns 0 for this message ***")
    ids = sorted(set(r[1] for r in rows))
    print("\ndistinct message ids in the table (%d): %s" % (len(ids), ids))
    print("is 30 present: %s" % (30 in ids))
    zero = [r for r in rows if r[3] == 0]
    print("rows with size 0: %d" % len(zero))


main()
