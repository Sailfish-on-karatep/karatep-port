#!/bin/sh
# Does the blanked codec list explain the incoming 488?
#
# qipcall_audio_codec_list is 128 bytes of zeros and is one of only three IMS
# items that match no carrier config in the modem image. It is empty because
# this investigation blanked it as an experiment and never put it back;
# rjil.mbn ships AMR_WB_OA;AMR_WB_BE;AMR_OA;AMR_BE.
#
# It was tested once, against registration, and the "num_formats is 0" message
# it was suspected of causing turned out to fire either way. But it was never
# tested against an *incoming* call, and that is the case where it should
# matter most: 488 Not Acceptable Here is literally "no codec in common", the
# modem does parse the incoming offer before refusing it, and BSNL's offer
# carries AMR, AMR-WB, G729, PCMA and PCMU. Writing the list demonstrably
# changes media construction -- the offer goes from num_formats 4 to 6 -- so
# the item is live.
#
# No dial path changes: incoming calls do not use it. Restores generated zeros.
ITEM=/nv/item_files/ims/qipcall_audio_codec_list
CAP=/data/incodec.bin
LOG=/data/incodec-radio.log
DUR=300
LPID=""
CPID=""

NEW=$(/usr/bin/python3 -c '
v=b"AMR_WB_OA;AMR_WB_BE;AMR_OA;AMR_BE"
print((v+b"\x00"*(128-len(v))).hex())')
OLD=$(/usr/bin/python3 -c 'print("00"*128)')

regstate() {
  dbus-send --system --print-reply --dest=org.ofono /ril_0 \
    org.ofono.NetworkRegistration.GetProperties 2>/dev/null |
    grep -A1 '"Status"' | tail -1 | sed 's/.*string "//;s/".*//'
}
restore() {
  echo
  echo "== RESTORING =="
  [ -n "$CPID" ] && kill -9 $CPID 2>/dev/null
  [ -n "$LPID" ] && kill -9 $LPID 2>/dev/null
  pkill -9 -f sdpraw.py 2>/dev/null
  sleep 1
  /usr/bin/python3 /home/defaultuser/efswrite.py "$ITEM" "$OLD" 2>&1 | tail -1
  echo "  network: $(regstate)"
  echo DONE-INCODEC
}
trap restore EXIT INT TERM

echo "== writing codec list =="
/usr/bin/python3 /home/defaultuser/efswrite.py "$ITEM" "$NEW" 2>&1 | tail -2

# Re-register so the modem picks the list up before any call arrives.
dbus-send --system --print-reply --dest=org.ofono /ril_0 \
  org.ofono.Modem.SetProperty string:Online variant:boolean:false >/dev/null 2>&1
sleep 10
dbus-send --system --print-reply --dest=org.ofono /ril_0 \
  org.ofono.Modem.SetProperty string:Online variant:boolean:true >/dev/null 2>&1
i=0
while [ $i -lt 40 ]; do
  [ "$(regstate)" = "registered" ] && break
  sleep 3; i=$((i+1))
done
echo "  network: $(regstate)"

/system/bin/logcat -G 16M 2>/dev/null
/system/bin/logcat -b radio -v time -T 1 2>/dev/null > $LOG &
LPID=$!
rm -f $CAP
/usr/bin/python3 -u /home/defaultuser/sdpraw.py $DUR $CAP 8 1 \
  > /data/incodec-scan.log 2>&1 &
CPID=$!
i=0
while [ $i -lt 20 ]; do
  grep -q preflight /data/incodec-scan.log 2>/dev/null && break
  sleep 2; i=$((i+1))
done
cat /data/incodec-scan.log
echo
echo "READY-INCODEC -- place TWO INCOMING calls now"
wait $CPID
CPID=""
echo
echo "== VERDICT =="
grep "RIL\[0\]" $LOG 2>/dev/null | grep -oE "call failure cause [0-9]+" | sort | uniq -c | sed 's/^/    /'
ls -la $CAP 2>/dev/null | tr -s ' ' | sed 's/^/    /'
