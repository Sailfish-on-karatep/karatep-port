#!/usr/bin/python3
#
# The last two IMS config items that could plausibly shape the SDP.
#
# Twelve Jio-specific items have now been aligned to the other operators'
# consensus across three call windows, and the VoLTE call still ends with
# "SDP parse failed". These two are what is left that touches media:
#
#   qipcall_audio_codec_list  Jio is the ONLY config in this modem image that
#                             carries this item at all. Its value is
#                             AMR_WB_OA;AMR_WB_BE;AMR_OA;AMR_BE. Every other
#                             operator leaves it unset and lets the modem use
#                             its own default codec set, so blank it and do the
#                             same. BSNL expects AMR-WB on payload types 97/98
#                             and AMR-NB on 99/100, each in a bandwidth-efficient
#                             and an octet-aligned variant, which is the same
#                             four codecs -- so if this matters it is the
#                             encoding, not the content.
#
#   qp_ims_media_config       Aligned to ntel's. The leading byte is 06 in Jio's
#                             and in gcf and ntel, so that is not the difference;
#                             the differences are further in (02 vs 00 at offset
#                             18, 3c 00 3c 00 vs 3c 00 00 00 at 28, 03 vs 00 at
#                             39) and are timers whose meaning is not recoverable
#                             without the struct definition.
#
# Originals are appended to /data/ims-nv-backup.txt, as with the earlier batch.
import struct
import subprocess
import sys

sys.path.insert(0, "/home/defaultuser")
import diagefs  # noqa: E402

DONOR = "/vendor/firmware_mnt/image/ntel.mbn"
BACKUP = "/data/ims-nv-backup.txt"
CHUNK = 256


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
        d.efs(diagefs.EFS2_WRITE, struct.pack("<iI", fd, off) + part)
        off += len(part)
    d.efs(diagefs.EFS2_CLOSE, struct.pack("<i", fd))
    return None


def donor_value(name):
    out = subprocess.run(["/usr/bin/python3", "/home/defaultuser/mbnitems.py",
                          DONOR], capture_output=True, text=True).stdout
    for line in out.splitlines():
        f = line.split()
        if f and f[0].endswith("/" + name):
            return bytes.fromhex(f[-1])
    return None


def main():
    d = diagefs.Diag()
    backup = open(BACKUP, "a")

    name = "qipcall_audio_codec_list"
    path = "/nv/item_files/ims/" + name
    before, err = diagefs.read_file(d, path, maxlen=4096)
    if before is None:
        print("%s: cannot read (%s)" % (name, err))
    else:
        backup.write("%s %s\n" % (path, before.hex()))
        txt = before.split(b"\x00")[0].decode("ascii", "replace")
        print("%s" % name)
        print("      was %r (%d bytes)" % (txt, len(before)))
        e = write_file(d, path, b"\x00" * len(before))
        after, _ = diagefs.read_file(d, path, maxlen=4096)
        print("      now blank: %s" % (after == b"\x00" * len(before)))

    name = "qp_ims_media_config"
    path = "/nv/item_files/ims/" + name
    donor = donor_value(name)
    before, err = diagefs.read_file(d, path, maxlen=4096)
    if before is None or donor is None:
        print("%s: cannot read or no donor" % name)
    elif len(donor) > len(before):
        print("%s: donor larger than nv, skipped" % name)
    else:
        backup.write("%s %s\n" % (path, before.hex()))
        data = donor + b"\x00" * (len(before) - len(donor))
        print("%s" % name)
        print("      was %s" % before[:26].hex())
        write_file(d, path, data)
        after, _ = diagefs.read_file(d, path, maxlen=4096)
        print("      now %s" % after[:26].hex())
        print("      matches donor: %s" % (after == data))

    backup.close()
    d.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
