#!/bin/sh
# Did IMS actually register, or is this another all-zero struct read as success?
echo "== ofono IpMultimediaSystem =="
dbus-send --system --print-reply --dest=org.ofono /ril_0 \
  org.ofono.IpMultimediaSystem.GetProperties 2>&1 | tail -20
echo
echo "== imsa (0x21, port 0x39) registration + service status =="
/usr/bin/python3 /home/defaultuser/qmiims.py port:0x39 0x22 0x23 0x24 0x25 2>&1
echo
echo "== network interfaces / IMS bearer =="
ip -4 addr show 2>/dev/null | grep -E "^[0-9]+:|inet " | sed 's/^/  /'
echo
echo "== ofono contexts =="
for c in 1 2 3; do
  echo -n "  context$c: "
  dbus-send --system --print-reply --dest=org.ofono /ril_0/context$c \
    org.ofono.ConnectionContext.GetProperties 2>&1 | \
    grep -A1 -E "\"Active\"|\"Type\"|\"Interface\"" | grep -E "boolean|string \"" | tr -s ' \n' ' '
  echo
done
echo
echo "== last 60 s of RIL[0] IMS registration state =="
/system/bin/logcat -d -b radio 2>/dev/null | grep "RIL\[0\]" | \
  grep -iE "imsa_registration|ims_reg_state|registration_status|pdp_error|reg_failure|sip" | tail -20
echo
echo "== anything mentioning SIP anywhere in the radio log =="
/system/bin/logcat -d -b radio 2>/dev/null | grep -icE "sip"
echo DONE-VER
