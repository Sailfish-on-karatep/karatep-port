#!/usr/bin/python3
#
# Compare the modem's per-subscription EFS state, subscription 0 against
# subscription 1.
#
# On this modem a per-subscription item is stored as two files: the bare name
# for subscription 0 and the same name with a "_Subscription01" suffix for
# subscription 1. The BSNL SIM sits in subscription 0 and the Jio SIM that
# once had working VoLTE sat in subscription 1, so anything the 2019 carrier
# provisioning wrote for Jio and never wrote for BSNL shows up here as a pair
# that disagrees, or as a suffixed file with no unsuffixed twin.

import struct
import sys

sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag, EFS2_READDIR, opendir, closedir, read_file

SUFFIX = "_Subscription01"
SKIP = ("/CGPS_PE", "/CGPS_ME", "/CGPS_SM", "/SUPL", "/gnss")


def entries(d, path):
    dirp, err = opendir(d, path)
    if dirp is None or err != 0:
        return []
    out, seq = [], 1
    while True:
        r = d.efs(EFS2_READDIR, struct.pack("<II", dirp, seq))
        if r is None or len(r) < 36:
            break
        _dp, _sq, e, _etype, mode, size = struct.unpack_from("<IIiIII", r, 0)
        name = r[36:].split(b"\x00")[0].decode("ascii", "replace")
        if e != 0 or not name:
            break
        out.append((mode, size, name))
        seq += 1
    closedir(d, dirp)
    return out


def walk(d, path, found, depth=0):
    if depth > 6 or path in SKIP:
        return
    for mode, size, name in entries(d, path):
        full = (path + "/" + name).replace("//", "/")
        if mode & 0xF000 == 0x4000:
            walk(d, full, found, depth + 1)
        elif name.endswith(SUFFIX):
            found.append(full)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "/"
    d = Diag()
    found = []
    walk(d, root, found)
    print("%d per-subscription file(s) under %s\n" % (len(found), root))

    same = 0
    for sub1 in found:
        sub0 = sub1[:-len(SUFFIX)]
        a, ea = read_file(d, sub0, 512)
        b, eb = read_file(d, sub1, 512)
        if a is not None and b is not None and a == b:
            same += 1
            continue
        print("%s" % sub0)
        print("   sub0: %s" % (a.hex() if a is not None else "<%s>" % ea))
        print("   sub1: %s" % (b.hex() if b is not None else "<%s>" % eb))
    print("\n%d identical pair(s) not shown" % same)
    d.close()


if __name__ == "__main__":
    main()
