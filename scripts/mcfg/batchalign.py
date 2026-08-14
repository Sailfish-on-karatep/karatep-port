#!/usr/bin/python3
#
# Align the remaining Jio-specific IMS items to the other operators' consensus.
#
# We run Reliance's commercial config retargeted at BSNL, and Jio disagrees with
# every other config in this modem image on 21 IMS items. Six have been
# corrected one at a time -- preconditions, QoS, session bandwidth, AMR SCR both
# widths, and VT media capability -- and the VoLTE call still dies with "SDP
# parse failed". Since the SDP itself cannot be read on this platform (no F3
# messaging, and the SIP payload never crosses an AP netdev), the remaining
# approach is to stop differing from the configs that are known to work and then
# spend a single call window, rather than one window per item.
#
# ntel.mbn is the donor: a plain commercial config, and in the majority group
# for every item changed here.
#
# qipcall_config_items is deliberately NOT touched. That item was bisected
# earlier as the one that makes this modem accept IMS at all; changing it risks
# losing registration entirely and putting the port back to the start.
#
# Each item is written as the donor's value zero-padded to the existing NV
# length, because EFS writes must not change an item's size. The originals are
# printed and saved first so this is reversible.
import struct
import subprocess
import sys

sys.path.insert(0, "/home/defaultuser")
import diagefs  # noqa: E402

DONOR = "/vendor/firmware_mnt/image/ntel.mbn"
BACKUP = "/data/ims-nv-backup.txt"
CHUNK = 256

ITEMS = [
    # (name, why)
    ("qp_ims_config",
     "jio 0400 02 ... 01 01 0c 01 30 vs consensus 0000 02 ... 00"),
    ("qp_ims_sip_extended_0_config",
     "jio sets two 600000 ms timers the others leave at zero"),
    ("qp_ims_reg_config",
     "every other config carries the APN string \"ims\"; jio does not"),
    ("qp_ims_reg_extended_0_config",
     "jio 05 3c ... vs consensus 05 00 ..."),
    ("qp_ims_dpl_config",
     "jio ...0000 0004 vs consensus ...0001 0004"),
    ("qp_ims_voip_config",
     "differs past the shared urn: prefix"),
]


def donor_values():
    out = subprocess.run(["/usr/bin/python3", "/home/defaultuser/mbnitems.py",
                          DONOR], capture_output=True, text=True).stdout
    vals = {}
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 3 and f[0].startswith("/nv/item_files/ims/"):
            name = f[0].rsplit("/", 1)[1]
            vals[name] = bytes.fromhex(f[-1])
    return vals


def write_file(d, path, data):
    r = d.efs(diagefs.EFS2_OPEN,
              struct.pack("<II", diagefs.O_RDWR, 0) + diagefs.cstr(path))
    if r is None or len(r) < 8:
        return "open failed"
    fd, err = struct.unpack_from("<iI", r, 0)
    if err:
        return "open errno %d" % err
    off = 0
    while off < len(data):
        part = data[off:off + CHUNK]
        rr = d.efs(diagefs.EFS2_WRITE, struct.pack("<iI", fd, off) + part)
        if rr is None:
            d.efs(diagefs.EFS2_CLOSE, struct.pack("<i", fd))
            return "write failed at %d" % off
        off += len(part)
    d.efs(diagefs.EFS2_CLOSE, struct.pack("<i", fd))
    return None


def main():
    vals = donor_values()
    d = diagefs.Diag()
    backup = open(BACKUP, "a")
    ok = 0
    for name, why in ITEMS:
        path = "/nv/item_files/ims/" + name
        donor = vals.get(name)
        if donor is None:
            print("%-32s donor has no value, skipped" % name)
            continue
        before, err = diagefs.read_file(d, path, maxlen=4096)
        if before is None:
            print("%-32s cannot read: %s" % (name, err))
            continue
        if len(donor) > len(before):
            print("%-32s donor %d > nv %d, skipped" %
                  (name, len(donor), len(before)))
            continue
        backup.write("%s %s\n" % (path, before.hex()))
        data = donor + b"\x00" * (len(before) - len(donor))
        if data == before:
            print("%-32s already matches" % name)
            continue
        e = write_file(d, path, data)
        if e:
            print("%-32s WRITE FAILED: %s" % (name, e))
            continue
        after, _ = diagefs.read_file(d, path, maxlen=4096)
        good = after == data
        ok += 1 if good else 0
        print("%-32s %s  (%s)" % (name, "ok" if good else "MISMATCH", why))
        print("      was %s" % before[:22].hex())
        print("      now %s" % (after[:22].hex() if after else "?"))
    backup.close()
    d.close()
    print()
    print("%d item(s) changed; originals appended to %s" % (ok, BACKUP))
    return 0


if __name__ == "__main__":
    sys.exit(main())
