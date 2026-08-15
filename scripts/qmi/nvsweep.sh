#!/bin/sh
# Compare every live /nv/item_files/ims/* value against all eight carrier
# configs in the modem image, and report only the disagreements.
#
# Picking items by hand has now produced two false leads in a row: the media
# booleans (already at consensus values, changed nothing) and qp_ims_media_config
# (which turned out to be byte-identical to gcf and ntel, not Jio's, once the
# whole 535 bytes were compared instead of the first 120). The failure mode is
# the same both times -- a plausible-looking item inspected in isolation.
#
# So do the whole namespace at once and let the data pick. Three questions:
#
#   ORPHAN     live value matches no config at all -- either hand-written by
#              this investigation, or a default the modem invented
#   MINORITY   configs disagree and the live value follows the smaller camp
#   UNANIMOUS  every config agrees and live differs -- the strongest signal
#
# Read-only.
/usr/bin/python3 - <<'PY'
import subprocess
import sys

sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag, read_file

CONFIGS = ["3uk", "gcf", "mexico", "ntel", "rjil", "row", "smtf", "ytl"]
PREFIX = "/nv/item_files/ims/"


def config_items(name):
    """{item_path: hex_value} for one carrier config."""
    out = {}
    try:
        txt = subprocess.run(
            ["/usr/bin/python3", "/home/defaultuser/mbnitems.py",
             "/vendor/firmware_mnt/image/%s.mbn" % name],
            capture_output=True, text=True, timeout=120).stdout
    except Exception:
        return out
    for line in txt.splitlines():
        line = line.strip()
        if not line.startswith(PREFIX) or "tag=07" not in line:
            continue
        path, _, val = line.partition("tag=07")
        out[path.strip()] = val.strip()
    return out


cfg = {c: config_items(c) for c in CONFIGS}
paths = sorted({p for c in cfg.values() for p in c})
print("%d IMS items across %d configs" % (paths.__len__(), len(CONFIGS)))

d = Diag()
orphan, minority, unanimous, missing = [], [], [], []
for p in paths:
    data, err = read_file(d, p)
    live = data.hex() if data else None
    vals = {c: cfg[c][p] for c in CONFIGS if p in cfg[c]}
    if live is None:
        missing.append((p, err))
        continue
    # Config blobs are stored without the trailing padding the modem writes, so
    # compare on the significant prefix rather than requiring equal length.
    def same(v):
        n = min(len(v), len(live))
        return v[:n] == live[:n] and set(live[n:]) <= {"0"} and set(v[n:]) <= {"0"}

    agree = [c for c, v in vals.items() if same(v)]
    if not vals:
        continue
    if not agree:
        orphan.append((p, live, vals))
    elif len(set(vals.values())) > 1 and len(agree) * 2 < len(vals):
        minority.append((p, agree, vals))
    elif len(set(vals.values())) == 1 and len(agree) != len(vals):
        unanimous.append((p, live, vals))
d.close()


def short(v, n=64):
    return (v[:n] + "…") if len(v) > n else v


print("\n=== ORPHAN: live matches no config (%d) ===" % len(orphan))
for p, live, vals in orphan:
    print("  %s" % p[len(PREFIX):])
    print("      live  %s" % short(live))
    for c in sorted(set(vals.values())):
        who = ",".join(sorted(k for k, v in vals.items() if v == c))
        print("      %-5s %s" % (who[:5], short(c)))

print("\n=== MINORITY: live follows the smaller camp (%d) ===" % len(minority))
for p, agree, vals in minority:
    print("  %-46s live agrees with %s" % (p[len(PREFIX):], ",".join(agree)))
    for c in sorted(set(vals.values())):
        who = ",".join(sorted(k for k, v in vals.items() if v == c))
        print("      %-22s %s" % (who, short(c, 48)))

print("\n=== items the modem has no value for (%d) ===" % len(missing))
for p, err in missing:
    print("  %-46s %s" % (p[len(PREFIX):], err))
PY
