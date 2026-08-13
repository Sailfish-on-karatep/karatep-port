#!/bin/sh
# Stage the software carrier configs, with our patched row.mbn and the
# UK3G_GBR config retargeted at PLMN 404/80, then reload rild.
SRC=/vendor/firmware_mnt/image
DST=/data/vendor/radio/modem_config/mcfg_sw
LOG=/data/3uk-stage.log

echo "== active config version in NV before =="
/usr/bin/python3 /home/defaultuser/diagcat.py \
  /nv/item_files/mcfg/mcfg_sw_muxd_version_8 2>&1 | tail -2

mkdir -p $DST
rm -f $DST/*.mbn
/usr/bin/python3 - "$SRC" "$DST" <<'PY'
import os, struct, sys
src, dst = sys.argv[1:3]
REPLACE = {"row.mbn": "/home/defaultuser/row_v61.mbn",
           "3uk.mbn": "/home/defaultuser/3uk_bsnl.mbn"}


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
/system/bin/logcat -b radio 2>/dev/null | grep -iE "mbn|pdc|mcfg|config_id|imss|ims_" > $LOG &
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
while [ $i -lt 130 ]; do sleep 1; i=$((i + 1)); done
kill $LPID 2>/dev/null

echo
echo "== selection / load / activate =="
grep -iE "MCFG_VERSION|active config|load_config|select|activat|config_id|UK3G|ROW_Gen|Commercial|W-One" $LOG | tail -45
echo
echo "== sw_mbn_loaded =="
getprop persist.vendor.radio.sw_mbn_loaded
echo
echo "== active config version in NV after =="
/usr/bin/python3 /home/defaultuser/diagcat.py \
  /nv/item_files/mcfg/mcfg_sw_muxd_version_8 2>&1 | tail -2
echo
echo "== IMS QMI services on the modem (node 0) =="
for s in 0x00000012 0x0000001f 0x00000020 0x00000021 0x00000022; do
  if grep -q "^$s|" /sys/kernel/debug/msm_ipc_router/dump_servers.tr 2>/dev/null; then
    echo "  $s PRESENT"
  else
    tr -d ' ' < /sys/kernel/debug/msm_ipc_router/dump_servers | grep -q "^$s|" \
      && echo "  $s PRESENT" || echo "  $s absent"
  fi
done
