#!/bin/sh
# What did each subscription actually get, and did IMS move?
echo "== every Selected config line =="
grep -E "pdc_select_config_ind_hdlr|pdc_activate_config_ind_hdlr|mbn_sw_activate_config_hndlr" /data/rjil-stage.log | \
  sed 's/.*RIL\[/RIL[/' | sort -u | head -30
echo
echo "== which .mbn per RIL instance =="
grep -oE "RIL\[[01]\].*mcfg_sw/[a-z_0-9]+\.mbn" /data/rjil-stage.log | \
  sed 's/.*RIL\[\([01]\)\].*mcfg_sw\//\1 /' | sort | uniq -c
echo
echo "== RIL[0] IMS registration lines =="
grep "RIL\[0\]" /data/rjil-stage.log | grep -iE "imsa_get_ims_registration_info|ims_reg_state|registration error" | tail -12
echo
echo "== live imsa registration state now =="
/system/bin/logcat -d -b radio 2>/dev/null | grep "RIL\[0\]" | \
  grep -iE "imsa_get_ims_registration_info|ims_registered|reg_state" | tail -10
echo
echo "== mcfg version NV items =="
for n in 0 1 8; do
  echo -n "  mcfg_sw_muxd_version_$n: "
  /usr/bin/python3 /home/defaultuser/diagcat.py /nv/item_files/mcfg/mcfg_sw_muxd_version_$n 2>&1 | grep hex | cut -c1-30
done
echo
echo "== items only rjil writes -- did they land? =="
for i in /nv/item_files/modem/mmode/sms_domain_pref \
         /nv/item_files/modem/mmode/supplement_service_domain_pref \
         /nv/item_files/ims/qp_ims_ut_config; do
  echo -n "  $i: "
  /usr/bin/python3 /home/defaultuser/diagcat.py "$i" 2>&1 | grep hex | cut -c1-40
done
echo DONE-CHK
