#!/usr/bin/python3
import sys
sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag, read_file
d = Diag()
for p in sys.argv[1:]:
    body, e = read_file(d, p, 4096)
    print("=== %s ===" % p)
    if body is None:
        print("  <%s>" % e); continue
    print("  hex: %s" % body.hex())
    txt = "".join(chr(c) if 32 <= c < 127 else "." for c in body)
    print("  txt: %s" % txt)
d.close()
