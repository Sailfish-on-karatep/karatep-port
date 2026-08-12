#!/usr/bin/python3
#
# Talk to the modem's IMS Settings service (QMI 0x12) directly, over the MSM
# IPC router, bypassing both the vendor HAL and qcril.
#
# This exists because the two calls that would enable VoLTE cannot get through
# any higher layer on this device. setServiceStatus is accepted by the vendor
# HAL and silently dropped -- the binder transaction completes and qcril logs
# nothing at all -- and no reachable HIDL ConfigItem maps to qcril's
# QIPCALL_VOLTE_ENABLED. The modem itself is fine: it just never gets told.
#
# Message ids and struct sizes were recovered from
# libril-qc-qmi-1.so by disassembling qcril's own senders:
#
#   qcril_qmi_imss_set_ims_service_enabled       ->  msg 0x8f, req 72, resp 16
#   qcril_qmi_imss_get_ims_service_enable_config ->  msg 0x90, req  0, resp 88
#
# The get takes no request TLVs, which makes it a safe first transaction and,
# from its response, names the tags the set needs.
#
# Wire format on the IPC router is the plain QMI service message:
#   [ctrl u8][txn u16][msg u16][len u16] then TLVs of [type u8][len u16][value]

import ctypes
import ctypes.util
import os
import select
import socket
import time
import struct
import sys

# CPython's socket layer refuses an address whose family it does not know
# ("sendto(): bad family"), and AF_MSM_IPC is not one of them, so the syscalls
# are made through libc with a raw sockaddr buffer instead.
_libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
_libc.socket.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
_libc.sendto.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t,
                         ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
_libc.sendto.restype = ctypes.c_ssize_t
_libc.recv.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t,
                       ctypes.c_int]
_libc.recv.restype = ctypes.c_ssize_t


def _check(rc, what):
    if rc < 0:
        e = ctypes.get_errno()
        raise OSError(e, "%s: %s" % (what, os.strerror(e)))
    return rc

AF_MSM_IPC = 27
MSM_IPC_ADDR_ID = 2

IMSS_SERVICE = 0x12
IMSS_INSTANCE = 0x01

QMI_IMSS_SET_SERVICE_ENABLE = 0x8F
QMI_IMSS_GET_SERVICE_ENABLE = 0x90

QMI_RESULT_TLV = 0x02


def sockaddr(node, port):
    """struct sockaddr_msm_ipc, addressed by node/port id.

    Layout follows uapi/linux/msm_ipc.h: the union inside struct msm_ipc_addr
    holds uint32s, so addrtype is padded out to the union's alignment.
    """
    return struct.pack("<H2xB3xIIB3x", AF_MSM_IPC, MSM_IPC_ADDR_ID,
                       node, port, 0)


def qmi_request(txn, msg_id, tlvs=b""):
    return struct.pack("<BHHH", 0, txn, msg_id, len(tlvs)) + tlvs


def parse_tlvs(payload):
    out = []
    off = 0
    while off + 3 <= len(payload):
        t, ln = struct.unpack_from("<BH", payload, off)
        off += 3
        if off + ln > len(payload):
            break
        out.append((t, payload[off:off + ln]))
        off += ln
    return out


def as_int(v):
    if len(v) == 1:
        return v[0]
    if len(v) == 2:
        return struct.unpack("<H", v)[0]
    if len(v) == 4:
        return struct.unpack("<I", v)[0]
    return None


class Imss:
    def __init__(self, node=0, port=0x37):
        self.fd = _check(_libc.socket(AF_MSM_IPC, socket.SOCK_DGRAM, 0),
                         "socket(AF_MSM_IPC)")
        self.addr = sockaddr(node, port)
        self.txn = 1

    def call(self, msg_id, tlvs=b"", timeout=5.0):
        txn = self.txn
        self.txn += 1
        req = qmi_request(txn, msg_id, tlvs)
        _check(_libc.sendto(self.fd, req, len(req), 0, self.addr,
                            len(self.addr)), "sendto")
        end = time.time() + timeout
        buf = ctypes.create_string_buffer(4096)
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [],
                                    max(0.05, end - time.time()))
            if not r:
                continue
            n = _libc.recv(self.fd, buf, 4096, 0)
            if n < 7:
                continue
            data = buf.raw[:n]
            ctrl, rtxn, rmsg, rlen = struct.unpack_from("<BHHH", data, 0)
            if rmsg != msg_id or rtxn != txn:
                continue
            return parse_tlvs(data[7:7 + rlen])
        raise socket.timeout("no response to msg 0x%02x" % msg_id)

    def close(self):
        os.close(self.fd)


def show(tlvs):
    for t, v in tlvs:
        if t == QMI_RESULT_TLV and len(v) >= 4:
            result, error = struct.unpack("<HH", v[:4])
            print("  result=%d error=%d %s" % (result, error,
                  "OK" if result == 0 else "FAILED"))
            continue
        n = as_int(v)
        print("  tlv 0x%02x len %d = %s%s" % (t, len(v), v.hex(),
              "  (%d)" % n if n is not None else ""))


def main():
    imss = Imss()

    print("== get_ims_service_enable_config (0x%02x) ==" % QMI_IMSS_GET_SERVICE_ENABLE)
    try:
        show(imss.call(QMI_IMSS_GET_SERVICE_ENABLE))
    except socket.timeout:
        print("  timed out -- no response from imss")
        imss.close()
        return 1

    if len(sys.argv) > 2 and sys.argv[1] == "get":
        for a in sys.argv[2:]:
            mid = int(a, 0)
            print("\n== msg 0x%02x ==" % mid)
            try:
                show(imss.call(mid))
            except socket.timeout:
                print("  timed out")
        imss.close()
        return 0

    if len(sys.argv) > 4 and sys.argv[1] == "set":
        msg = int(sys.argv[2], 0)
        tag = int(sys.argv[3], 0)
        val = int(sys.argv[4], 0)
        back = int(sys.argv[5], 0) if len(sys.argv) > 5 else msg + 1
        print("\n== set msg 0x%02x, tlv 0x%02x = %d ==" % (msg, tag, val))
        tlv = struct.pack("<BHB", tag, 1, val)
        try:
            show(imss.call(msg, tlv))
        except socket.timeout:
            print("  timed out")
        print("\n== read back with msg 0x%02x ==" % back)
        try:
            show(imss.call(back))
        except socket.timeout:
            print("  timed out")

    imss.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
