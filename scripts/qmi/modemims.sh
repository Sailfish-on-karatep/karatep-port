#!/bin/sh
# Does this modem firmware contain an IMS session layer at all?
#
# 34 imss handlers exist, decode their requests correctly, and then fail
# internally. Either the session layer is present and something stops it
# starting, or the image was built without it. The image itself can say.
IMG=/vendor/firmware_mnt/image
echo "== modem image segments =="
ls -l $IMG/modem.b* 2>/dev/null | awk '{printf "  %-28s %s\n", $NF, $5}' | head -30
echo
echo "== SIP method and header strings across the whole image =="
for s in "SIP/2.0" "REGISTER" "INVITE" "P-Access-Network-Info" "Contact" \
         "3gppnetwork.org" "sip:" "ims_mgr" "qipcall" "IMS_SIP" "imsstack" \
         "rtp" "PRACK"; do
  n=$(cat $IMG/modem.b* 2>/dev/null | strings -a 2>/dev/null | grep -c -- "$s")
  printf "  %-24s %s\n" "$s" "$n"
done
echo
echo "== IMS-looking source file names compiled into the image =="
cat $IMG/modem.b* 2>/dev/null | strings -a 2>/dev/null | \
  grep -oE "[a-z_0-9]*ims[a-z_0-9]*\.(c|cpp|h)" | sort -u | head -30
echo
echo "== and the SIP stack's own files =="
cat $IMG/modem.b* 2>/dev/null | strings -a 2>/dev/null | \
  grep -oE "[a-z_0-9]*sip[a-z_0-9]*\.(c|cpp|h)" | sort -u | head -20
