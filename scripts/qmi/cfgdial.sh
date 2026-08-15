#!/bin/sh
# The decisive test: an outgoing VoLTE call on the consensus
# qipcall_config_items, dialled by the device itself.
#
# The incoming run on this value changed nothing of substance -- still 488, the
# SDP parser still runs once per call -- which is what was expected, because an
# incoming offer arrives in an INVITE and request bodies are the path this
# modem parses correctly every time. Outgoing is where it matters: BSNL's
# answer arrives in a reliable 183, a *response*, and the modem has never once
# been observed to hand a response body to the parser.
#
# The question this answers is whether the item builds the INVITE's Supported
# list. Today that reads timer,100rel,replaces,precondition,histinfo,tdialog.
# If 100rel goes, BSNL cannot use a reliable provisional, the answer moves into
# the 200 OK, and it arrives by the working path.
#
# Dialling is done here rather than by hand, with the number supplied by the
# user for this purpose. Each attempt is hung up after six seconds: the VoLTE
# INVITE fails at about 1.2 s and the modem then falls back to CS, which would
# otherwise place a real ringing call.
NUM="+919487323890"
GOOD=/home/defaultuser/extqti-KNOWNGOOD.rpm
TEST=/home/defaultuser/ofono-binder-plugin-ext-qti-0.0.2-1.aarch64.rpm
CAP=/data/cfgdial.bin
LOG=/data/cfgdial-radio.log
DUR=150
LPID=""
CPID=""

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
  echo "== RESTORING dial path =="
  hangup
  [ -n "$CPID" ] && kill -9 $CPID 2>/dev/null
  [ -n "$LPID" ] && kill -9 $LPID 2>/dev/null
  pkill -9 -f sdpraw.py 2>/dev/null
  sleep 1
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
  echo DONE-CFGDIAL
}
trap restore EXIT INT TERM

echo "== installing TEST plugin =="
rpm -Uvh --force $TEST 2>&1 | tail -1
/usr/bin/python3 /home/defaultuser/efswrite.py \
  /nv/item_files/modem/mmode/sms_domain_pref 03 2>&1 | tail -1
systemctl restart ofono
setprop ctl.restart ril-daemon

echo "== waiting for irte 3 =="
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
  > /data/cfgdial-scan.log 2>&1 &
CPID=$!
sleep 12
cat /data/cfgdial-scan.log

for n in 1 2; do
  echo "== dialling attempt $n =="
  dbus-send --system --print-reply --dest=org.ofono /ril_0 \
    org.ofono.VoiceCallManager.Dial string:"$NUM" string:"default" 2>&1 | tail -2
  sleep 6
  hangup
  sleep 14
done

wait $CPID
CPID=""
cat /data/cfgdial-scan.log

echo
echo "== VERDICT =="
grep "RIL\[0\]" $LOG 2>/dev/null | grep -oE "end_reason_text \(UTF-8\): .*" | sort | uniq -c | sed 's/^/    /'
grep "RIL\[0\]" $LOG 2>/dev/null | grep -oE "call failure cause [0-9]+" | sort | uniq -c | sed 's/^/    /'
ls -la $CAP 2>/dev/null | tr -s ' ' | sed 's/^/    /'
