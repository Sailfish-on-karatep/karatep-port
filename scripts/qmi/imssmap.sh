#!/bin/sh
# Which of the modem's IMS Settings messages work and which fail internally?
#
# 0x90 is a getter with no request TLVs, so its QMI_ERR_INTERNAL cannot be
# blamed on a malformed request. Mapping the whole message space shows whether
# the service is wholly broken or only its IMS-state-dependent half.
echo "== imss (0x12, port 0x37) message sweep =="
/usr/bin/python3 /home/defaultuser/qmiims.py sweep 0x20 0xa0 2>&1 | tail -30

echo
echo "== IMS-related NV that gates the modem's IMS task =="
/usr/bin/python3 /home/defaultuser/diagcat.py \
  /nv/item_files/ims/IMS_enable \
  /nv/item_files/ims/ims_operation_mode \
  /nv/item_files/ims/qp_ims_test_mode \
  /nv/item_files/ims/ims_hybrid_enable \
  /nv/item_files/ims/qipcall_config_items \
  /nv/item_files/ims/qp_ims_reg_config 2>&1 | grep -E "===|hex" | cut -c1-100
