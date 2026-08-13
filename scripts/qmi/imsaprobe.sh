#!/bin/sh
# Corroborate the registration claim from the modem's own imsa service, and
# from the full RIL[0] history rather than a 60-second window.
echo "== imsa sweep (port 0x39, 0x20..0x60) =="
/usr/bin/python3 - <<'PY'
import socket, struct, sys
sys.path.insert(0, "/home/defaultuser")
from qmiims import Imss, QMI_RESULT_TLV, as_int
imsa = Imss(port=0x39)
ok, err = [], {}
for mid in range(0x20, 0x61):
    try:
        tlvs = imsa.call(mid, timeout=1.0)
    except socket.timeout:
        continue
    res = None
    for t, v in tlvs:
        if t == QMI_RESULT_TLV and len(v) >= 4:
            res = struct.unpack("<HH", v[:4])
    if res is None:
        continue
    if res[0] == 0:
        ok.append((mid, [(t, v) for t, v in tlvs if t != QMI_RESULT_TLV]))
    else:
        err.setdefault(res[1], []).append(mid)
for mid, rest in ok:
    print("  0x%02x OK  %d tlv(s)" % (mid, len(rest)))
    for t, v in rest:
        n = as_int(v)
        print("      tlv 0x%02x len %d = %s%s" % (t, len(v), v.hex()[:64],
              "  (%d)" % n if n is not None else ""))
for e in sorted(err):
    print("  error %d: %s" % (e, " ".join("0x%02x" % m for m in err[e])))
imsa.close()
PY

echo
echo "== every RIL[0] imsa registration transition in the buffer =="
/system/bin/logcat -d -b radio 2>/dev/null | grep "RIL\[0\]" | \
  grep -iE "map_qmi_ims_reg_state|imsa_registration_status|ims_registered:|registration error code" | \
  sed 's/\(..-.. ..:..:..\).*RIL\[0\]/\1 /' | tail -30
echo
echo "== imsa indication handlers seen =="
/system/bin/logcat -d -b radio 2>/dev/null | grep "RIL\[0\]" | \
  grep -oE "qcril_qmi_imsa_[a-z_]+" | sort | uniq -c | sort -rn | head -20
echo
echo "== does the modem report VoPS / IMS voice available? =="
/system/bin/logcat -d -b radio 2>/dev/null | grep "RIL\[0\]" | \
  grep -iE "vops|volte|ims_voice|voice_over|srvcc|emergency_ims" | tail -15
echo DONE-IMSA
