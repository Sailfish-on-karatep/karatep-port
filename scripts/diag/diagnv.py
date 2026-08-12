#!/usr/bin/python3
# Read legacy NV items over /dev/diag (DIAG_NV_READ_F), plus a decode of the
# two IMEIs. NV 550 is NV_UE_IMEI_I, one per SIM slot on a dual-SIM target.
#
# Request/response layout for NV_READ_F is fixed width:
#   [cmd u8][item u16][data 128][status u16]

import struct
import sys

sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag

DIAG_NV_READ_F = 0x26

NV_ITEMS = {
    550: "NV_UE_IMEI_I",
    453: "NV_LTE_BC_CONFIG",
    6873: "NV_IMS_ENABLE",
    0: "NV_ESN_I",
}


def nv_read(d, item, index=0):
    req = struct.pack("<BH", DIAG_NV_READ_F, item) + b"\x00" * 128 \
        + struct.pack("<H", 0)
    d.send(req)
    rsp = d.recv(struct.pack("<BH", DIAG_NV_READ_F, item))
    if rsp is None or len(rsp) < 133:
        return None, None
    data = rsp[3:131]
    status, = struct.unpack_from("<H", rsp, 131)
    return data, status


def decode_imei(raw):
    """NV 550 is 9 bytes: length, then the digits packed two per byte with
    the first digit in the high nibble of byte 1 shifted left by 4."""
    if len(raw) < 9 or raw[0] != 0x08:
        return None
    digits = "%d" % (raw[1] >> 4)
    for b in raw[2:9]:
        digits += "%d%d" % (b & 0x0F, b >> 4)
    return digits


def luhn_ok(imei):
    if not imei or len(imei) != 15 or not imei.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(imei)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


d = Diag()
for item in (550,):
    for index in (0, 1):
        data, status = nv_read(d, item)
        if data is None:
            print("NV %d: no response" % item)
            break
        print("NV %d (%s) status=%d raw=%s"
              % (item, NV_ITEMS.get(item, "?"), status, data[:16].hex()))
        imei = decode_imei(data)
        if imei:
            print("   IMEI: %s   TAC=%s   luhn=%s"
                  % (imei, imei[:8], "OK" if luhn_ok(imei) else "BAD"))
        break
d.close()
