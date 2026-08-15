#!/bin/sh
# Restore qipcall_config_items, then try qp_ims_sip_extended_0_config as the
# lever that builds the INVITE's Supported list.
#
# qipcall_config_items is settled and the answer is no. On the ytl/smtf
# consensus value IMS still registers -- so the long-standing assumption that
# it is the registration gate was wrong -- but the INVITE still advertises
# timer,100rel,replaces,precondition,histinfo,tdialog unchanged, the call still
# dies INVITE -> 100 -> 183 -> PRACK -> CANCEL with cause 373, and registration
# is measurably *worse* (three REGISTERs went unanswered in five minutes).
# Jio's value goes back.
#
# The next candidate is qp_ims_sip_extended_0_config, 1024 bytes, whose first
# byte looks like a count and varies by operator: 0x0a for 3uk, 0x0b live and
# for gcf/ntel/smtf/ytl, 0x11 for rjil. Ten, eleven, seventeen. If that counts
# enabled SIP extensions then 3uk's is the shortest list on the handset and the
# most likely to be missing 100rel.
#
# This matters because 100rel is what lets BSNL answer in a reliable 183.
# Every SDP answer this device has ever received arrived in a provisional
# response, and this modem parses request bodies every time and response bodies
# never. Move the answer into the 200 OK and it arrives by the working path.
#
# Fully unattended: the device dials itself, on a number the user supplied for
# this purpose, and hangs up after six seconds so the CS fallback does not
# place a real ringing call.
NUM="+919487323890"
CFG=/nv/item_files/ims/qipcall_config_items
BAK=/home/defaultuser/qipcall_config_items.bak
ITEM=/nv/item_files/ims/qp_ims_sip_extended_0_config
GOOD=/home/defaultuser/extqti-KNOWNGOOD.rpm
TEST=/home/defaultuser/ofono-binder-plugin-ext-qti-0.0.2-1.aarch64.rpm
CAP=/data/sipext.bin
LOG=/data/sipext-radio.log
DUR=150
LPID=""
CPID=""

echo "== restoring Jio qipcall_config_items =="
/usr/bin/python3 /home/defaultuser/efswrite.py "$CFG" "$(cat $BAK)" 2>&1 | tail -1

OLD=$(/usr/bin/python3 - "$ITEM" <<'PY'
import sys
sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag, read_file
d = Diag(); data, err = read_file(d, sys.argv[1]); d.close()
print(data.hex() if data else "")
PY
)
[ -n "$OLD" ] || { echo "could not read $ITEM"; exit 1; }
echo "$OLD" > /home/defaultuser/sip_extended.bak
NEW=$(/usr/bin/python3 /home/defaultuser/mbnitems.py \
        /vendor/firmware_mnt/image/3uk.mbn 2>/dev/null |
      grep "/nv/item_files/ims/qp_ims_sip_extended_0_config " | sed 's/.*tag=07 //')
NEW=$(/usr/bin/python3 -c "
new='$NEW'; old='$OLD'
new = new[:len(old)] + '0'*max(0, len(old)-len(new))
print(new)")
echo "  old: $(echo $OLD | cut -c1-40)"
echo "  new: $(echo $NEW | cut -c1-40)"

regstate() {
  dbus-send --system --print-reply --dest=org.ofono /ril_0 \
    org.ofono.NetworkRegistration.GetProperties 2>/dev/null |
    grep -A1 '"Status"' | tail -1 | sed 's/.*string "//;s/".*//'
}
hangup() {
  dbus-send --system --print-reply --dest=org.ofono /ril_0 \
    org.ofono.VoiceCallManager.HangupAll >/dev/null 2>&1
}

restore() {
  echo
  echo "== RESTORING =="
  hangup
  [ -n "$CPID" ] && kill -9 $CPID 2>/dev/null
  [ -n "$LPID" ] && kill -9 $LPID 2>/dev/null
  pkill -9 -f sdpraw.py 2>/dev/null
  sleep 1
  /usr/bin/python3 /home/defaultuser/efswrite.py "$ITEM" "$OLD" 2>&1 | tail -1
  rpm -Uvh --force $GOOD 2>&1 | tail -1
  /usr/bin/python3 /home/defaultuser/efswrite.py \
    /nv/item_files/modem/mmode/sms_domain_pref 01 2>&1 | tail -1
  systemctl restart ofono
  setprop ctl.restart ril-daemon
  i=0
  while [ $i -lt 25 ]; do
    [ "$(regstate)" = "registered" ] && break
    sleep 3; i=$((i + 1))
  done
  echo "  network: $(regstate)"
  echo DONE-SIPEXT
}
trap restore EXIT INT TERM

echo
echo "== writing 3uk qp_ims_sip_extended_0_config =="
/usr/bin/python3 /home/defaultuser/efswrite.py "$ITEM" "$NEW" 2>&1 | tail -2

echo "== installing TEST plugin =="
rpm -Uvh --force $TEST 2>&1 | tail -1
/usr/bin/python3 /home/defaultuser/efswrite.py \
  /nv/item_files/modem/mmode/sms_domain_pref 03 2>&1 | tail -1
systemctl restart ofono
setprop ctl.restart ril-daemon

i=0
while [ $i -lt 45 ]; do
  last=$(/system/bin/logcat -d -b radio 2>/dev/null | grep "RIL\[0\]" |
         grep -oE "new irte [0-9]+ with confidence [0-9]+" | tail -1)
  case "$last" in "new irte 3"*) break ;; esac
  sleep 2; i=$((i + 1))
done
echo "  irte: ${last:-none}"

/system/bin/logcat -G 16M 2>/dev/null
/system/bin/logcat -b radio -v time -T 1 2>/dev/null > $LOG &
LPID=$!
rm -f $CAP
/usr/bin/python3 -u /home/defaultuser/sdpraw.py $DUR $CAP 6 1 \
  > /data/sipext-scan.log 2>&1 &
CPID=$!
sleep 12

for n in 1 2; do
  echo "== dialling attempt $n =="
  dbus-send --system --print-reply --dest=org.ofono /ril_0 \
    org.ofono.VoiceCallManager.Dial string:"$NUM" string:"default" 2>&1 | tail -1
  sleep 6
  hangup
  sleep 14
done

wait $CPID
CPID=""
echo
echo "== VERDICT =="
grep "RIL\[0\]" $LOG 2>/dev/null | grep -oE "end_reason_text \(UTF-8\): .*" | sort | uniq -c | sed 's/^/    /'
grep "RIL\[0\]" $LOG 2>/dev/null | grep -oE "call failure cause [0-9]+" | sort | uniq -c | sed 's/^/    /'
ls -la $CAP 2>/dev/null | tr -s ' ' | sed 's/^/    /'
