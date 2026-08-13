#!/bin/sh
# Confirm the absence before believing it: check the whole firmware directory,
# not just modem.b*, and read the modem's own build banner.
IMG=/vendor/firmware_mnt/image
echo "== everything in the firmware image dir =="
ls $IMG | tr '\n' ' '
echo
echo
echo "== SIP literals anywhere in the whole directory =="
for s in "SIP/2.0" "sip:" "P-Access-Network-Info" "Max-Forwards" "CSeq"; do
  n=$(cat $IMG/* 2>/dev/null | strings -a 2>/dev/null | grep -c -- "$s")
  printf "  %-24s %s\n" "$s" "$n"
done
echo
echo "== modem build banner =="
cat $IMG/modem.b* 2>/dev/null | strings -a 2>/dev/null | \
  grep -E "MPSS\.|TA\.2\.3|Rel[0-9]|_IMS|NOIMS|NO_IMS" | sort -u | head -12
echo
echo "== what the 'REGISTER'/'INVITE' hits actually are =="
cat $IMG/modem.b* 2>/dev/null | strings -a 2>/dev/null | \
  grep -E "REGISTER|INVITE|PRACK" | sort -u | head -12
