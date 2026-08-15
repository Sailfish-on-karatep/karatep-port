#!/bin/sh
# Compare qp_ims_media_config across every carrier config in the modem image,
# and against what is actually live in EFS right now.
#
# This item was flagged in the parent RCA -- Jio ships 06 78 05..., every other
# operator ships 01 78 05... -- and then deliberately left alone, because it is
# a large struct rather than a boolean and changing it wholesale would have
# confounded the media-boolean test that was running at the time. That test is
# long finished and the five booleans are at their consensus values, so the
# struct is now the last untouched item in the group.
#
# It matters more than it did then. The modem reads qp_ims_media_config while
# setting up every call, aborts outgoing calls without ever parsing the answer
# SDP, and refuses incoming offers immediately after tokenising them -- both
# decisions taken inside QSR-hashed records that cannot be read. NV values are
# the readable inputs to that unreadable code.
#
# Read-only.
for f in 3uk gcf mexico ntel rjil row smtf ytl; do
  v=$(/usr/bin/python3 /home/defaultuser/mbnitems.py \
        "/vendor/firmware_mnt/image/$f.mbn" 2>/dev/null |
      grep "/nv/item_files/ims/qp_ims_media_config " |
      sed 's/.*tag=07 //')
  printf '%-8s %s\n' "$f" "${v:-<absent>}"
done

/usr/bin/python3 - <<'PY'
import sys
sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag, read_file

d = Diag()
data, err = read_file(d, "/nv/item_files/ims/qp_ims_media_config")
d.close()
print("%-8s %s" % ("LIVE", data.hex() if data else "<%s>" % err))
PY
