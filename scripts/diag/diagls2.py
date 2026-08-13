#!/usr/bin/python3
# Directory listing with mtime, over DIAG EFS2.
import struct, sys, time
sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag, EFS2_READDIR, opendir, closedir

d = Diag()
for p in sys.argv[1:] or ["/"]:
    print("=== %s ===" % p)
    dirp, err = opendir(d, p)
    if dirp is None or err != 0:
        print("  <unreadable err=%s>" % err); continue
    seq = 1
    while True:
        r = d.efs(EFS2_READDIR, struct.pack("<II", dirp, seq))
        if r is None or len(r) < 36:
            break
        _dp, _sq, e, etype, mode, size, atime, mtime, ctime = \
            struct.unpack_from("<IIiIIIIII", r, 0)
        name = r[36:].split(b"\x00")[0].decode("ascii", "replace")
        if e != 0 or not name:
            break
        kind = "d" if mode & 0xF000 == 0x4000 else "-"
        ts = time.strftime("%Y-%m-%d %H:%M", time.gmtime(mtime)) if mtime else "-"
        print("  %s %7d  %s  %s" % (kind, size, ts, name))
        seq += 1
    closedir(d, dirp)
d.close()
