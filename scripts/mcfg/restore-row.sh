#!/bin/sh
# Undo a carrier-config retarget: put subscription 0 back on our patched
# ROW_Generic_3GPP config.
#
# Restaging the stock files is not by itself enough. qcril caches which config
# claims which SIM in /data/vendor/radio/qcril.db (qcril_sw_mbn_file_type_table,
# qcril_sw_mbn_iin_table, qcril_sw_mbn_mcc_mnc_table), and a row written while a
# retargeted config was staged survives a plain rild restart. Those three tables
# are rebuilt from /data/vendor/radio/mbn-master, which /vendor/bin/init.qcom.sh
# populates from the pristine vendor images -- and only at boot. So:
#
#     reboot the handset first, then run this script.
#
# What this script must NOT do is replace qcril.db from
# /vendor/radio/qcril_database/qcril.db. That template carries the emergency and
# operator schema but none of the qcril_sw_mbn_* tables, so copying it over the
# live database throws away exactly the selection tables qcril has just built,
# and the next selection finds nothing at all. Deleting the database is worse
# still: qcril does not create the emergency schema, so every lookup then logs
# "no such table: qcril_emergency_source_mcc_mnc_table" and the emergency-number
# tables are gone with it. Leave the database alone; boot maintains it.
SRC=/vendor/firmware_mnt/image
DST=/data/vendor/radio/modem_config/mcfg_sw
ROW=/home/defaultuser/row_v61.mbn
LOG=/data/restore-row.log

echo "== qcril.db selection tables before (want all three) =="
strings -a /data/vendor/radio/qcril.db 2>/dev/null | \
  grep -oE "CREATE TABLE qcril_sw[a-z_]*" | sort -u | tr '\n' ' '
echo

mkdir -p $DST
rm -f $DST/*.mbn
/usr/bin/python3 - "$SRC" "$DST" "$ROW" <<'PY'
import os, struct, sys
src, dst, row = sys.argv[1:4]


def is_sw(d):
    """A software carrier config, as opposed to mba.mbn or a hardware one."""
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
    if name == "row.mbn":
        blob = open(row, "rb").read()
    elif not is_sw(blob):
        continue
    p = os.path.join(dst, name)
    open(p, "wb").write(blob)
    os.chown(p, 1001, 0)
    os.chmod(p, 0o444)
    staged.append(name)
print("staged:", " ".join(staged))
PY

rm -f $LOG
/system/bin/logcat -c 2>/dev/null
/system/bin/logcat -b radio 2>/dev/null | \
  grep -iE "Selected config|activate_config_hndlr|no such table" > $LOG &
LPID=$!
setprop persist.vendor.radio.sw_mbn_volte 1
setprop persist.vendor.radio.sw_mbn_openmkt 1
setprop persist.vendor.radio.sw_mbn_update 1
setprop persist.vendor.radio.sw_mbn_loaded 0
setprop persist.vendor.radio.voice_on_lte 1
setprop persist.vendor.radio.vdp_on_ims_cap 1
setprop ctl.restart ril-daemon
i=0
while [ $i -lt 120 ]; do sleep 1; i=$((i + 1)); done
kill $LPID 2>/dev/null

echo
echo "== missing-table errors (want 0) =="
grep -c "no such table" $LOG
echo "== config selected per subscription (want row.mbn) =="
grep -oE "mcfg_sw/[a-z_0-9]+\.mbn[01]?" $LOG | sort | uniq -c
echo "== resulting qp_ims_config NV =="
/usr/bin/python3 /home/defaultuser/diagcat.py \
  /nv/item_files/ims/qp_ims_config 2>&1 | grep hex | cut -c1-44
