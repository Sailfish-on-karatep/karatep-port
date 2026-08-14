#!/bin/sh
# Find every IMS item where Jio's config disagrees with the other operators'.
#
# We are running Reliance Jio's commercial config retargeted at BSNL. Jio's IMS
# is unusual -- VoLTE-only, and it turned out to be the single config of six
# that ships qipcall_precondition_enable = 0 and qipcall_qos_enabled = 0, where
# 3uk, gcf, ntel, smtf and ytl all ship 1. SIP preconditions are the
# a=curr/des/conf:qos attributes in the SDP, so a modem told not to use them,
# receiving an answer full of them, is a plausible "SDP parse failed".
#
# That one was found by looking at five items by hand. This does the whole IMS
# namespace: for every /nv/item_files/ims/* item, print Jio's value beside the
# other configs' values, and flag the ones where Jio stands alone. The point is
# to fix all of them in one go rather than spend a service window per item.
SRC=/vendor/firmware_mnt/image
/usr/bin/python3 - "$SRC" <<'PY'
import os
import subprocess
import sys

src = sys.argv[1]
sys.path.insert(0, "/home/defaultuser")


def items(path):
    out = subprocess.run(
        ["/usr/bin/python3", "/home/defaultuser/mbnitems.py", path],
        capture_output=True, text=True).stdout
    d = {}
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 3 and f[0].startswith("/nv/item_files/ims/"):
            d[f[0]] = f[-1]
    return d


configs = {}
for n in sorted(os.listdir(src)):
    if not n.endswith(".mbn"):
        continue
    d = items(os.path.join(src, n))
    if d:
        configs[n] = d

jio = configs.get("rjil.mbn", {})
others = dict((k, v) for k, v in configs.items() if k != "rjil.mbn")
print("configs with IMS items: %s" % ", ".join(sorted(configs)))
print()

odd = []
for key, jv in sorted(jio.items()):
    vals = {}
    for n, d in others.items():
        if key in d:
            vals.setdefault(d[key], []).append(n)
    if not vals:
        continue
    # Jio stands alone if no other config shares its value.
    if jv not in vals:
        odd.append((key, jv, vals))

print("== items where Jio disagrees with every other config (%d) ==" % len(odd))
for key, jv, vals in odd:
    short = key.replace("/nv/item_files/ims/", "")
    print("  %s" % short)
    print("      jio  = %s" % jv[:60])
    for v, names in sorted(vals.items(), key=lambda x: -len(x[1])):
        print("      %-4s = %s   (%s)" % (len(names), v[:52], ",".join(names)))
PY
echo DONE-DIFF
