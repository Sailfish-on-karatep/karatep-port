#!/bin/sh
# Swap qipcall_config_items from Jio's value to the ytl/smtf consensus, and
# find out whether IMS still registers.
#
# Why this item, and why now. It is the last substantive carrier-config item
# still set to Jio's value -- byte-identical to rjil.mbn, differing from the
# ytl/smtf consensus on five bytes: offsets 0, 2, 301, 302 and 437. It was held
# back through the whole investigation because it was bisected as what makes
# this modem accept IMS at all, and then dismissed "by inspection" on the
# grounds that offset 0 is a count (26 for Jio, 25 for everyone else) and the
# extra entry is probably the IMS-enabling one.
#
# Inspection is not measurement, and the reason to revisit it is new. The modem
# parses SDP in requests and never in responses; BSNL delivers its answer in a
# reliable 183, which only happens because we advertise 100rel in the INVITE's
# Supported list (timer,100rel,replaces,precondition,histinfo,tdialog). Nothing
# else in NV has been found to build that list, and this item is a 512-byte
# blob of small fields that Jio ships differently. If the swap changes what the
# INVITE advertises, the answer moves out of the 183 and into a 200 OK -- the
# one dispatch path this modem has never been seen to refuse.
#
# That is a hypothesis about an item whose schema we do not have, so this
# script only answers the free half of it: does IMS survive the change. A call
# is needed to see the Supported list, and there is no point spending one if
# the modem deregisters.
#
# Reversible by construction. The old value is read back from EFS before the
# write and restored from that, not from a constant -- efswrite.py refuses any
# length change, and an earlier run of this investigation left the device on a
# narrowed codec list for exactly the reason that a "restore" constant was
# typed rather than generated.
ITEM=/nv/item_files/ims/qipcall_config_items
BACKUP=/home/defaultuser/qipcall_config_items.bak

echo "== reading current value =="
/usr/bin/python3 - "$ITEM" "$BACKUP" <<'PY'
import sys
sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag, read_file
d = Diag(); data, err = read_file(d, sys.argv[1]); d.close()
if data is None:
    raise SystemExit("read failed: %s" % err)
open(sys.argv[2], "w").write(data.hex())
print("  %d bytes saved to %s" % (len(data), sys.argv[2]))
print("  first 16: %s" % data[:16].hex())
PY
[ -s "$BACKUP" ] || { echo "no backup, aborting"; exit 1; }

NEW=$(/usr/bin/python3 /home/defaultuser/mbnitems.py \
        /vendor/firmware_mnt/image/ytl.mbn 2>/dev/null |
      grep "/nv/item_files/ims/qipcall_config_items " | sed 's/.*tag=07 //')
OLD=$(cat "$BACKUP")
# The config blob is stored without the trailing padding EFS holds, so pad the
# replacement to the live length rather than letting efswrite refuse it.
NEW=$(/usr/bin/python3 -c "
new='$NEW'; old='$OLD'
new = new[:len(old)] + '0' * max(0, len(old) - len(new))
print(new)")
echo "  replacement: ${NEW#????????????????}" >/dev/null
echo "  old first 16: $(echo $OLD | cut -c1-32)"
echo "  new first 16: $(echo $NEW | cut -c1-32)"

regstate() {
  dbus-send --system --print-reply --dest=org.ofono /ril_0 \
    org.ofono.NetworkRegistration.GetProperties 2>/dev/null |
    grep -A1 '"Status"' | tail -1 | sed 's/.*string "//;s/".*//'
}
imsstate() {
  /system/bin/logcat -d -b radio 2>/dev/null |
    grep -oE "ims_registered: [01]" | tail -1
}

reregister() {
  dbus-send --system --print-reply --dest=org.ofono /ril_0 \
    org.ofono.Modem.SetProperty string:Online variant:boolean:false >/dev/null 2>&1
  sleep 10
  dbus-send --system --print-reply --dest=org.ofono /ril_0 \
    org.ofono.Modem.SetProperty string:Online variant:boolean:true >/dev/null 2>&1
  i=0
  while [ $i -lt 40 ]; do
    [ "$(regstate)" = "registered" ] && break
    sleep 3; i=$((i + 1))
  done
}

restore() {
  echo
  echo "== RESTORING Jio value =="
  /usr/bin/python3 /home/defaultuser/efswrite.py "$ITEM" "$OLD" 2>&1 | tail -1
  reregister
  echo "  network: $(regstate)   ims: $(imsstate)"
  echo DONE-CFGSWAP
}

echo
echo "== writing consensus value =="
/usr/bin/python3 /home/defaultuser/efswrite.py "$ITEM" "$NEW" 2>&1 | tail -2

/system/bin/logcat -c -b radio 2>/dev/null
reregister
echo
echo "== after swap =="
echo "  network: $(regstate)"
sleep 20
echo "  ims: $(imsstate)"
/system/bin/logcat -d -b radio 2>/dev/null |
  grep -oE "imsa_reg_status_ind_hdlr[^,]*|ims_registered: [01]|new irte [0-9]+" |
  sort | uniq -c | tail -8 | sed 's/^/    /'

case "$(imsstate)" in
  *"ims_registered: 1"*)
      echo
      echo "IMS SURVIVED -- leaving the consensus value in place for a call test."
      echo "  backup of Jio's value: $BACKUP"
      echo KEEP-CFGSWAP
      ;;
  *)  echo
      echo "IMS did NOT come back -- rolling back."
      restore
      ;;
esac
