#!/bin/sh
# Measurement 1 of docs/rca/volte-sdp-exceeds-modem-string-buffer.md:
#
#   "Does qvp_app_oa_api.c:2747 fire outside calls? BSNL's REGISTER 200 OK
#    carries tag values of the same length. If the truncation happens during
#    registration too -- which succeeds -- it is bookkeeping noise and not the
#    fault at all. This is the cheapest test of the whole hypothesis and needs
#    no call, only F3 enabled across a re-registration."
#
# The published attribution of that truncation to BSNL's SDP was retracted in
# 74842c0: the F3 context bracketing each fire is qipcalldialog.c dialog
# bookkeeping, and the identifier it builds -- call-id(34) + local tag(10) +
# BSNL remote tag(48) = 92 bytes -- overflows a 50-byte destination on its own,
# with no SDP involved. Registration builds the same kind of identifier from the
# same peer's tags, and registration *works*. So:
#
#   fires during registration  -> the truncation is noise; the call-failure
#                                 hypothesis dies, and the real cause is still
#                                 in the QSR-hashed 1 ms before the CANCEL.
#   silent during registration -> the truncation really is specific to the
#                                 call path, and measurement 2 (F3 across an
#                                 incoming INVITE, 28 ms, no PRACK) is worth
#                                 the inbound call.
#
# Deliberately cheap and reversible: no plugin swap, no NV write, no call. The
# only state touched is ofono's Modem.Online, toggled to force a detach/attach
# and therefore a fresh IMS registration; the trap puts it back.
#
# Log masks are cut to equipment id 1 so 0x156e (whole SIP messages in
# plaintext) lands in the same capture as the F3 -- that is what lets a fire be
# placed relative to the REGISTER and its 200 OK rather than merely counted.

CAP=/data/f3reg.bin
LOG=/data/f3reg-radio.log
DUR=200
LPID=""
CPID=""

online() {
  dbus-send --system --print-reply --dest=org.ofono /ril_0 \
    org.ofono.Modem.SetProperty string:Online variant:boolean:$1 >/dev/null 2>&1
}

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
  online true
  i=0
  while [ $i -lt 25 ]; do
    [ "$(regstate)" = "registered" ] && break
    sleep 3
    i=$((i + 1))
  done
  echo "  registration: $(regstate) $(dbus-send --system --print-reply \
    --dest=org.ofono /ril_0 org.ofono.NetworkRegistration.GetProperties \
    2>/dev/null | grep -A1 '"Technology"' | tail -1 | sed 's/.*string "//;s/".*//')"
  echo DONE-F3REG
}
trap restore EXIT INT TERM

echo "== baseline: $(regstate) =="
/system/bin/logcat -G 16M 2>/dev/null
/system/bin/logcat -b radio -v time -T 1 2>/dev/null > $LOG &
LPID=$!

rm -f $CAP
/usr/bin/python3 -u /home/defaultuser/sdpraw.py $DUR $CAP 8 1 \
  > /data/f3reg-scan.log 2>&1 &
CPID=$!

# Window A -- registered and idle. Anything that fires here is background
# chatter and settles the question before the toggle even happens.
sleep 30
echo "== window A (idle, registered) done at t~30 =="
cat /data/f3reg-scan.log

# Window B -- forced deregistration and re-registration.
echo "== t~30: Online=false =="
online false
sleep 15
echo "== t~45: Online=true =="
online true

i=0
while [ $i -lt 40 ]; do
  [ "$(regstate)" = "registered" ] && break
  sleep 3
  i=$((i + 1))
done
echo "== reregistered after toggle: $(regstate) =="

wait $CPID
CPID=""
cat /data/f3reg-scan.log

echo
echo "== IMS in radio log =="
grep "RIL\[0\]" $LOG 2>/dev/null |
  grep -oiE "ims_registration_state[^,]*|imsa_[a-z_]*reg[a-z_]*|new irte [0-9]+" |
  sort | uniq -c | sort -rn | head -15 | sed 's/^/    /'
ls -la $CAP 2>/dev/null | tr -s ' ' | sed 's/^/    /'
