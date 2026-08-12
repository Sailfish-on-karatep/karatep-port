#!/usr/bin/python3
# Recursive walk of the modem's live EFS over /dev/diag, with timestamps.
#
# Entry layout, confirmed against real responses from this modem:
#   0 dirp, 4 seq, 8 errno, 12 entry_type, 16 mode, 20 size,
#   24 atime, 28 mtime, 32 ctime, 36 name (NUL-terminated)
# seq 0 is a null pseudo-entry; the directory ends with an all-zero record.

import struct
import sys
import time

sys.path.insert(0, "/home/defaultuser")
from diagefs import (Diag, EFS2_READDIR, EFS2_CLOSEDIR, opendir, closedir,
                     read_file)

MAXREAD = 96
READ_CONTENT = "--names" not in sys.argv


def ts(v):
    if v in (0, 0x12D53D80):        # 0 or the 1980 GPS-epoch default
        return "-        "
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(v))
    except Exception:
        return "?%08x" % v


def entries(d, path):
    dirp, err = opendir(d, path)
    if dirp is None or err != 0:
        return None
    out = []
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
        out.append((mode, size, mtime, ctime, name))
        seq += 1
    closedir(d, dirp)
    return out


def walk(d, path, depth, out):
    kids = entries(d, path)
    if kids is None:
        out.append("%s%s  <unreadable>" % ("  " * depth, path))
        return
    for mode, size, mtime, ctime, name in kids:
        full = (path + "/" + name).replace("//", "/")
        pad = "  " * depth
        if mode & 0xF000 == 0x4000:
            out.append("%s%s/" % (pad, name))
            walk(d, full, depth + 1, out)
            continue
        line = "%s%-40s %6d  %s %s" % (pad, name, size, ts(mtime), ts(ctime))
        if READ_CONTENT and size <= MAXREAD:
            body, e = read_file(d, full, MAXREAD)
            line += "  " + (body.hex() if body is not None else "<%s>" % e)
        out.append(line)


d = Diag()
out = []
for r in [a for a in sys.argv[1:] if not a.startswith("--")] or ["/"]:
    out.append("=== %s ===" % r)
    walk(d, r, 1, out)
d.close()
open("/home/defaultuser/efswalk.txt", "w").write("\n".join(out) + "\n")
print("\n".join(out))
