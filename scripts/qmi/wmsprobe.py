#!/usr/bin/python3
#
# Ask the WMS (SMS) service what transport it has.
#
# ims_rte -- the value that decides CS vs IMS for voice -- is written only by
# qcril_qmi_nas_set_registered_on_ims, whose only caller is
# qcril_sms_process_transport_nw_reg_info_ind. That indication comes from WMS and
# has never fired here. Two very different causes:
#
#   * the modem has no IMS SMS transport, so there is nothing to report; or
#   * it has one, and qcril simply never subscribed to the indication.
#
# Asking WMS directly separates them. Service 0x05 sits at node 0 port 0x27.
import socket
import struct
import sys

sys.path.insert(0, "/home/defaultuser")
from qmiims import Imss, QMI_RESULT_TLV, as_int  # noqa: E402

WMS_PORT = 0x27


def main():
    wms = Imss(port=WMS_PORT)
    lo, hi = 0x20, 0x60
    if len(sys.argv) > 2:
        lo, hi = int(sys.argv[1], 0), int(sys.argv[2], 0)
    ok, err = [], {}
    for mid in range(lo, hi + 1):
        try:
            tlvs = wms.call(mid, timeout=1.0)
        except socket.timeout:
            continue
        res = None
        for t, v in tlvs:
            if t == QMI_RESULT_TLV and len(v) >= 4:
                res = struct.unpack("<HH", v[:4])
        if res is None:
            continue
        if res[0] == 0:
            ok.append((mid, tlvs))
        else:
            err.setdefault(res[1], []).append(mid)

    print("answering:")
    for mid, tlvs in ok:
        print("  msg 0x%02x" % mid)
        for t, v in tlvs:
            if t == QMI_RESULT_TLV:
                continue
            n = as_int(v)
            txt = ""
            if len(v) > 2 and all(32 <= c < 127 or c == 0 for c in v):
                s = v.split(b"\x00")[0]
                if len(s) >= 2:
                    txt = "  %r" % s.decode("ascii", "replace")
            print("      tlv 0x%02x len %-3d %s%s%s" %
                  (t, len(v), v.hex()[:48],
                   "  (%d)" % n if n is not None else "", txt))
    for e in sorted(err):
        print("error %d: %s" % (e, " ".join("0x%02x" % m for m in err[e])))
    wms.close()


main()
