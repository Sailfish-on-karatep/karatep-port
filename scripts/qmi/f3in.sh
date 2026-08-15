#!/bin/sh
# Measurement 1 of docs/rca/volte-answer-sdp-is-never-parsed.md: F3 across an
# INCOMING call.
#
# The outgoing path is a poor place to watch the abort. Between BSNL's 183 and
# the CANCEL there is a complete PRACK transaction, a preconditions exchange, a
# dialog-id concatenation and ~35 ms of noise, and the decision itself is taken
# 1 ms after the PRACK goes out with no network response in between.
#
# The incoming path reaches the same verdict in 28 ms -- INVITE in, 488 Not
# Acceptable Here out -- with no PRACK, no preconditions, no CANCEL and no
# dialog-id concatenation. Whatever refuses the media has to log inside that
# window, with almost nothing around it. It has never been captured with F3
# enabled: the one incoming call recorded so far predates the message masks.
#
# What this is looking for specifically: any record from qipcallsdp.c. On the
# outgoing path that module speaks only when building our own offer and again in
# teardown, and is silent while BSNL's answer is on the table. If it is silent
# here too, then the modem refuses the offer without parsing it in both
# directions and the fault is upstream of media entirely. If it does speak here,
# the incoming path parses where the outgoing path does not, and the difference
# between the two is the next thing to chase.
#
# Purely observational: no plugin swap, no NV write, nothing persistent. Log
# masks are cut to equipment id 1 so 0x156e (whole SIP messages in plaintext)
# lands in the same capture as the F3, which is what lets a record be placed
# relative to the INVITE and the 488 rather than merely counted.

CAP=/data/f3in.bin
LOG=/data/f3in-radio.log
DUR=300
LPID=""
CPID=""

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
  echo "  registration: $(regstate)"
  echo DONE-F3IN
}
trap restore EXIT INT TERM

echo "== baseline: $(regstate) =="
/system/bin/logcat -G 16M 2>/dev/null
/system/bin/logcat -b radio -v time -T 1 2>/dev/null > $LOG &
LPID=$!

rm -f $CAP
/usr/bin/python3 -u /home/defaultuser/sdpraw.py $DUR $CAP 8 1 \
  > /data/f3in-scan.log 2>&1 &
CPID=$!

# Give the masks and the preflight time to settle before asking for a call, so
# the INVITE cannot land in the middle of mask setup.
i=0
while [ $i -lt 20 ]; do
  grep -q "preflight" /data/f3in-scan.log 2>/dev/null && break
  sleep 2
  i=$((i + 1))
done
cat /data/f3in-scan.log

echo
echo "READY-F3IN -- place TWO INCOMING calls to this device now"
echo "  (let each ring/fail on its own; do not answer on the other end)"

wait $CPID
CPID=""
cat /data/f3in-scan.log

echo
echo "== SIP seen =="
grep "RIL\[0\]" $LOG 2>/dev/null |
  grep -oE "end_reason_text \(UTF-8\): .*|call failure cause [0-9]+" |
  sort | uniq -c | sed 's/^/    /'
ls -la $CAP 2>/dev/null | tr -s ' ' | sed 's/^/    /'
