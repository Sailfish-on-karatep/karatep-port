#!/usr/bin/python3
#
# Turn on CLIENT_PROVISIONING_ENABLE_VOLTE, the switch nothing on this device
# ever wrote.
#
# Recovered from libril-qc-qmi-1.so: qcril keeps a table of radio-config items,
# {get_msg, set_msg, name, item_id}, and it appears twice -- once with the
# message pair this 2016 modem implements and once with the pair a later modem
# would. QIPCALL_VOLTE_ENABLED (item 46) is get 0x37 / set 0x36 in the legacy
# table and get 0x90 / set 0x8f in the modern one, which is exactly why 0x90
# answers INTERNAL while 0x37 answers fine. The port has been calling the wrong
# generation of the API all along.
#
# Reading both legacy getters says the qipcall layer is already on --
# 0x37 gives mobile_data=1 volte=1 vt=1 -- but 0x54, client provisioning, gives
# volte=0 vt=0 presence=0. Item 24 is CLIENT_PROVISIONING_ENABLE_VOLTE and its
# legacy setter is 0x53.
#
# The set request's TLV tag is not in the table, so try the plausible ones and
# let the read-back decide. Nothing here writes anything but that one flag, and
# setting it back to 0 undoes it.
import socket
import struct
import sys

sys.path.insert(0, "/home/defaultuser")
from qmiims import Imss, QMI_RESULT_TLV  # noqa: E402

GET, SET = 0x54, 0x53
ENABLE_VOLTE_TAG = 0x11          # tag of item 24 in the 0x54 response


def result(tlvs):
    for t, v in tlvs:
        if t == QMI_RESULT_TLV and len(v) >= 4:
            return struct.unpack("<HH", v[:4])
    return None


def read_prov(imss):
    try:
        tl = dict((t, v) for t, v in imss.call(GET, timeout=3.0))
    except socket.timeout:
        return None
    return dict((t, v[0]) for t, v in sorted(tl.items())
                if t != QMI_RESULT_TLV and len(v) in (1, 2, 4))


def show(imss, label):
    p = read_prov(imss)
    names = {0x11: "volte", 0x12: "vt", 0x13: "presence", 0x14: "wifi_call",
             0x15: "wifi_roam", 0x16: "wifi_pref"}
    print("  %-8s %s" % (label, " ".join(
        "%s=%d" % (names.get(t, "%02x" % t), v) for t, v in sorted(p.items())
        if t in names) if p else "(no answer)"))
    return p


def main():
    imss = Imss()
    before = show(imss, "before:")

    for tag in (ENABLE_VOLTE_TAG, 0x10, 0x01):
        for width in (1, 4):
            payload = (struct.pack("<BH", tag, width) +
                       (1).to_bytes(width, "little"))
            try:
                r = result(imss.call(SET, payload, timeout=3.0))
            except socket.timeout:
                print("  set tag 0x%02x/%dB -> timeout" % (tag, width))
                continue
            print("  set tag 0x%02x/%dB -> result=%s" % (tag, width, r))
            if r and r[0] == 0:
                after = show(imss, "after:")
                if after and after.get(ENABLE_VOLTE_TAG) == 1:
                    print("  ACCEPTED and read back as 1")
                    imss.close()
                    return 0
    print("  no tag/width combination set it; provisioning unchanged")
    show(imss, "final:")
    imss.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
