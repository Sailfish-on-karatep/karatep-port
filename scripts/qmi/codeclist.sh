#!/bin/sh
# Does an empty qipcall_audio_codec_list cause "num_formats is 0"?
#
# A full sweep of every /nv/item_files/ims/* value against all eight carrier
# configs in the modem image found three items whose live value matches no
# config at all. Two are Jio leftovers of no consequence to voice
# (qp_ims_ut_config still reads "jionet"; qp_ims_vt_4G_media_capability is
# video). The third is qipcall_audio_codec_list, which is 128 bytes of zeros
# where rjil.mbn ships the ASCII list "AMR_WB_OA;AMR_WB_BE;AMR_OA;AMR_BE".
#
# That is worth testing because the modem complains about having no media
# formats. In both the registration control and the incoming-call capture,
# every IMS bring-up emits eight of:
#
#   qipcallsdp.c:8432  qipcallsdp_reallocate_med_arr:num_formats is 0!
#   qipcallsdp.c:8700  Failed to reallocate med_arr!! Status = 1
#
# and "488 Not Acceptable Here" -- how BSNL's incoming offers are refused -- is
# precisely the response for having no codec in common.
#
# The counter-evidence is real and is why this is a measurement rather than a
# fix: the modem does build a four-codec offer with the list empty
# (ReSizing med_arr by num_formats = 4), so something else supplies codecs on
# the offer-building path. This asks whether the *other* path, the one that
# reports zero, is the one reading this item.
#
# No calls needed: the signal appears at every IMS bring-up. Write the list,
# force a re-registration, count. The trap restores the exact 128 bytes of
# zeros that were there before.
#
# Both values are generated rather than typed. An earlier run of this
# investigation hardcoded a "128-byte" zero constant that was actually 130
# bytes, efswrite.py refused it -- correctly, it rejects any length change --
# and the device was left on a narrowed codec list until the restore output was
# read. Generating removes that whole class of mistake.
ITEM=/nv/item_files/ims/qipcall_audio_codec_list
CAP=/data/codeclist.bin
DUR=150
CPID=""

NEW=$(/usr/bin/python3 -c '
v = b"AMR_WB_OA;AMR_WB_BE;AMR_OA;AMR_BE"
print((v + b"\x00" * (128 - len(v))).hex())')
OLD=$(/usr/bin/python3 -c 'print(("00" * 128))')

regstate() {
  dbus-send --system --print-reply --dest=org.ofono /ril_0 \
    org.ofono.NetworkRegistration.GetProperties 2>/dev/null |
    grep -A1 '"Status"' | tail -1 | sed 's/.*string "//;s/".*//'
}

reregister() {
  dbus-send --system --print-reply --dest=org.ofono /ril_0 \
    org.ofono.Modem.SetProperty string:Online variant:boolean:false >/dev/null 2>&1
  sleep 12
  dbus-send --system --print-reply --dest=org.ofono /ril_0 \
    org.ofono.Modem.SetProperty string:Online variant:boolean:true >/dev/null 2>&1
  i=0
  while [ $i -lt 40 ]; do
    [ "$(regstate)" = "registered" ] && break
    sleep 3
    i=$((i + 1))
  done
}

restore() {
  echo
  echo "== RESTORING =="
  [ -n "$CPID" ] && kill -9 $CPID 2>/dev/null
  pkill -9 -f sdpraw.py 2>/dev/null
  /usr/bin/python3 /home/defaultuser/efswrite.py "$ITEM" "$OLD" 2>&1 | tail -1
  echo "  verify: $(/usr/bin/python3 - <<'PY'
import sys
sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag, read_file
d = Diag(); data, err = read_file(d, "/nv/item_files/ims/qipcall_audio_codec_list"); d.close()
print("%d bytes, %s" % (len(data), "all zero" if not data.strip(b"\x00") else data.rstrip(b"\x00")))
PY
)"
  reregister
  echo "  registration: $(regstate)"
  echo DONE-CODECLIST
}
trap restore EXIT INT TERM

echo "== writing codec list =="
/usr/bin/python3 /home/defaultuser/efswrite.py "$ITEM" "$NEW" 2>&1 | tail -2

echo "== capture across a re-registration =="
rm -f $CAP
/usr/bin/python3 -u /home/defaultuser/sdpraw.py $DUR $CAP 6 1 \
  > /data/codeclist-scan.log 2>&1 &
CPID=$!
sleep 14
reregister
echo "  registration: $(regstate)"
wait $CPID
CPID=""
cat /data/codeclist-scan.log
ls -la $CAP | tr -s ' ' | sed 's/^/    /'
