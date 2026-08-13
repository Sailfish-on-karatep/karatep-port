#!/bin/sh
# Stage the Jio commercial config, retargeted at PLMN 404/80 and IIN 8991805,
# in place of rjil.mbn, and let qcril select it for the BSNL SIM.
#
# Why this donor. Commercial-Reliance is the only config on this handset known
# to have produced working VoLTE on this exact modem and this exact firmware,
# under LineageOS. Its 80 EFS items are a strict superset of the 50 hand-built
# into row_v61.mbn: everything we wrote, it also writes. Two things it has that
# we never gave the modem --
#
#   * one value difference in an item we did copy:
#     /nv/item_files/ims/qp_ims_reg_config byte 0 is 01 in Jio's and 00 in ours;
#   * thirty items we never copied at all, among them /Data_Profiles/Profile1..3
#     (Profile2 is the "ims" APN), /pdp_profiles/consl_profiles/*_call_prof_num,
#     and the mmode domain preferences (sms_domain_pref, supplement_service_
#     domain_pref, wifi_config).
#
# row.mbn is staged STOCK here, not row_v61.mbn, so that anything that changes
# is attributable to this config alone. If the retarget is not selected the
# fallback has IMS_enable=0 and IMS goes away entirely -- that is a clean
# negative, and restore-row.sh puts it back.
SRC=/vendor/firmware_mnt/image
DST=/data/vendor/radio/modem_config/mcfg_sw
LOG=/data/rjil-stage.log

echo "== NV before =="
for i in /nv/item_files/mcfg/mcfg_sw_muxd_version_8 \
         /nv/item_files/ims/qp_ims_reg_config; do
  echo "  $i"
  /usr/bin/python3 /home/defaultuser/diagcat.py "$i" 2>&1 | grep hex | cut -c1-44
done

mkdir -p $DST
rm -f $DST/*.mbn
/usr/bin/python3 - "$SRC" "$DST" <<'PY'
import os, struct, sys
src, dst = sys.argv[1:3]
REPLACE = {"rjil.mbn": "/home/defaultuser/rjil_bsnl.mbn"}


def is_sw(d):
    if len(d) < 0x34 or d[:4] != b"\x7fELF" or d[4] != 1:
        return False
    off, = struct.unpack_from("<I", d, 0x1c)
    esz, n = struct.unpack_from("<HH", d, 0x2a)
    if n < 3:
        return False
    _, po, _, _, pl = struct.unpack_from("<5I", d, off + 2 * esz)
    p = d[po:po + pl]
    return p[:4] == b"MCFG" and struct.unpack_from("<H", p, 6)[0] == 1


staged = []
for name in sorted(os.listdir(src)):
    if not name.endswith(".mbn"):
        continue
    blob = open(os.path.join(src, name), "rb").read()
    if name in REPLACE:
        blob = open(REPLACE[name], "rb").read()
    elif not is_sw(blob):
        continue
    p = os.path.join(dst, name)
    open(p, "wb").write(blob)
    os.chown(p, 1001, 0)
    os.chmod(p, 0o444)
    staged.append("%s(%d)" % (name, len(blob)))
print("staged:", " ".join(staged))
PY

rm -f $LOG
/system/bin/logcat -c 2>/dev/null
/system/bin/logcat -b radio 2>/dev/null | \
  grep -iE "mbn|mcfg|Selected config|activate_config|no such table|imss|imsa|ims_" > $LOG &
LPID=$!
setprop persist.vendor.radio.sw_mbn_volte 1
setprop persist.vendor.radio.sw_mbn_openmkt 1
setprop persist.vendor.radio.sw_mbn_update 1
setprop persist.vendor.radio.sw_mbn_loaded 0
setprop persist.vendor.radio.voice_on_lte 1
setprop persist.vendor.radio.vdp_on_ims_cap 1
echo "restarting rild"
setprop ctl.restart ril-daemon
i=0
while [ $i -lt 150 ]; do sleep 1; i=$((i + 1)); done
kill $LPID 2>/dev/null

echo
echo "== missing-table errors (want 0) =="
grep -c "no such table" $LOG
echo "== config selected per subscription (want rjil.mbn) =="
grep -oE "mcfg_sw/[a-z_0-9]+\.mbn[01]?" $LOG | sort | uniq -c
echo "== selection / activation lines =="
grep -iE "Selected config|activate_config|MCFG_VERSION|Commercial|ROW_Gen" $LOG | \
  sed 's/.*RIL\[0\]//' | tail -25
echo "== sw_mbn_loaded =="
getprop persist.vendor.radio.sw_mbn_loaded

echo
echo "== NV after =="
for i in /nv/item_files/mcfg/mcfg_sw_muxd_version_8 \
         /nv/item_files/ims/IMS_enable \
         /nv/item_files/ims/qp_ims_reg_config \
         /nv/item_files/ims/qp_ims_config; do
  echo "  $i"
  /usr/bin/python3 /home/defaultuser/diagcat.py "$i" 2>&1 | grep hex | cut -c1-44
done

echo
echo "== IMS QMI services on the modem (node 0) =="
D=/sys/kernel/debug/msm_ipc_router/dump_servers
[ -r $D ] || D=/d/msm_ipc_router/dump_servers
for s in 0x00000012 0x0000001f 0x00000020 0x00000021 0x00000022; do
  if awk 'NR>2 {print $1}' $D | grep -q "$s"; then
    echo "  $s PRESENT"
  else
    echo "  $s absent"
  fi
done

echo
echo "== imss: enable config, domain, service flags =="
/usr/bin/python3 /home/defaultuser/qmiims.py get 0x90 0x28 0x37 0x54 2>&1

echo
echo "== ofono =="
dbus-send --system --print-reply --dest=org.ofono /ril_0 \
  org.ofono.NetworkRegistration.GetProperties 2>&1 | grep -E "string \"" | tr -s ' ' | head -12
dbus-send --system --print-reply --dest=org.ofono /ril_0 \
  org.ofono.IpMultimediaSystem.GetProperties 2>&1 | grep -E "string \"|boolean" | tr -s ' '

echo
echo "== IMS traffic in the radio log =="
grep -iE "ims_reg|registration_status|set_ims_service|imsa_" $LOG | \
  sed 's/.*RIL\[0\]//' | tail -25
echo DONE-RJIL
