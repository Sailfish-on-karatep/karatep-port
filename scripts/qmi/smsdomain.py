#!/usr/bin/python3
#
# WMS message 0x40 is GET_DOMAIN_PREF and it answers 2.
#
# In QMI WMS the domain preference enum is
#   0 CS_PREFERRED, 1 PS_PREFERRED, 2 CS_ONLY, 3 PS_ONLY
#
# so SMS on this modem is configured CS_ONLY. A UE told SMS is CS-only never
# registers an SMS transport over IMS -- which is why WMS 0x47
# (GET_TRANSPORT_NW_REG_INFO) answers success with no data, why
# qcril_sms_process_transport_nw_reg_info_ind has never once fired, why
# qcril_qmi_nas_set_registered_on_ims is never called, and therefore why
# nas_cached_info.ims_rte is stuck at 0 and every call is placed on CS.
#
# Set PS_PREFERRED rather than PS_ONLY: preferred keeps CS as the fallback, so a
# failure to carry SMS over IMS costs nothing.
import socket
import struct
import sys

sys.path.insert(0, "/home/defaultuser")
from qmiims import Imss, QMI_RESULT_TLV, as_int  # noqa: E402

WMS_PORT = 0x27
GET_DOMAIN_PREF = 0x40
SET_DOMAIN_PREF = 0x3F

NAMES = {0: "CS_PREFERRED", 1: "PS_PREFERRED", 2: "CS_ONLY", 3: "PS_ONLY"}


def result(tlvs):
    for t, v in tlvs:
        if t == QMI_RESULT_TLV and len(v) >= 4:
            return struct.unpack("<HH", v[:4])
    return None


def show(wms, label):
    try:
        tl = wms.call(GET_DOMAIN_PREF, timeout=3.0)
    except socket.timeout:
        print("  %-8s (timeout)" % label)
        return None
    for t, v in tl:
        if t == QMI_RESULT_TLV:
            continue
        n = as_int(v)
        print("  %-8s domain_pref = %s (%s)" % (label, n, NAMES.get(n, "?")))
        return n
    return None


def main():
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    wms = Imss(port=WMS_PORT)
    before = show(wms, "before:")
    if before == want:
        print("  already %s" % NAMES.get(want))
        wms.close()
        return 0
    for tag in (0x01, 0x10):
        payload = struct.pack("<BH", tag, 1) + bytes([want])
        try:
            r = result(wms.call(SET_DOMAIN_PREF, payload, timeout=3.0))
        except socket.timeout:
            print("  set tag 0x%02x -> timeout" % tag)
            continue
        print("  set tag 0x%02x = %d -> result=%s" % (tag, want, r))
        if r and r[0] == 0:
            after = show(wms, "after:")
            if after == want:
                print("  ACCEPTED")
                wms.close()
                return 0
    show(wms, "final:")
    wms.close()
    return 1


sys.exit(main())
