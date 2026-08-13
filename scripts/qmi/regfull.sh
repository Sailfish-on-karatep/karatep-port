#!/bin/sh
echo "== imsa 0x20 full =="
/usr/bin/python3 - <<'PY'
import sys
sys.path.insert(0, "/home/defaultuser")
from qmiims import Imss, as_int
imsa = Imss(port=0x39)
for t, v in imsa.call(0x20):
    txt = "".join(chr(c) if 32 <= c < 127 else "." for c in v)
    print("  tlv 0x%02x len %2d  %-24s |%s|" % (t, len(v), v.hex()[:48], txt))
imsa.close()
PY
echo
echo "== registration stability: reg_status indications over the whole buffer =="
/system/bin/logcat -d -b radio 2>/dev/null | grep -c "imsa_reg_status_ind_hdlr: ims_registered: 1"
/system/bin/logcat -d -b radio 2>/dev/null | grep -c "imsa_reg_status_ind_hdlr: ims_registered: 0"
echo
echo "== SMS: what does the modem think the domain preference is =="
echo -n "  sms_domain_pref: "
/usr/bin/python3 /home/defaultuser/diagcat.py /nv/item_files/modem/mmode/sms_domain_pref 2>&1 | grep hex
echo -n "  qp_ims_sms_config (SMSC, Jio's): "
/usr/bin/python3 /home/defaultuser/diagcat.py /nv/item_files/ims/qp_ims_sms_config 2>&1 | grep hex | cut -c1-30
echo
echo "== ofono voice / sms interfaces alive =="
dbus-send --system --print-reply --dest=org.ofono /ril_0 org.ofono.MessageManager.GetProperties 2>&1 | grep -E "string \"" | tr -s ' ' | head
echo DONE-REG
