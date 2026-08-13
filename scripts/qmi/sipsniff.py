#!/usr/bin/python3
#
# Watch the IMS bearer for SIP.
#
# Everything so far has chased the control path -- ofono to the HAL to qcril to
# QMI -- and inferred failure from silence. Step back: the modem's answer is a
# SIP 408, Request Timeout, which means it believes it sent a REGISTER and got
# nothing. Whether that REGISTER ever reaches the air is directly observable,
# because ofono owns the IMS PDN and it is an AP-visible netdev.
#
# Two outcomes, both decisive:
#   * REGISTER appears here -> it is on the wire, the core is not answering, and
#     the problem is the network side or the request's contents;
#   * nothing appears -> the modem is sending it somewhere that does not exist,
#     which is what an AP-owned PDN would do to a modem-internal IMS stack, and
#     the 408 is self-inflicted.
import socket
import struct
import sys
import time

IFACE = sys.argv[1] if len(sys.argv) > 1 else "rmnet_data1"
SECONDS = int(sys.argv[2]) if len(sys.argv) > 2 else 90
ETH_P_ALL = 3

# Do not bind to one interface: the IMS bearer goes down and comes back during
# a re-registration, and a bound socket dies with ENETDOWN taking the capture
# with it. Unbound AF_PACKET sees every interface, which is what we want anyway
# -- if the REGISTER leaves on something other than the IMS bearer, that is
# itself the answer.
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
s.settimeout(2.0)

print("sniffing %s for %d s" % (IFACE, SECONDS))
end = time.time() + SECONDS
total = 0
udp = 0
sip = 0
seen = {}
while time.time() < end:
    try:
        pkt = s.recv(65535)
    except socket.timeout:
        continue
    except OSError:
        continue
    total += 1
    # rmnet is a raw-IP interface: no ethernet header, the IP header is first.
    if not pkt:
        continue
    # Unbound, we get frames from every link type. rmnet is raw IP; wlan0 and
    # rndis0 have a 14-byte ethernet header. Try raw IP first, then skip 14.
    if (pkt[0] >> 4) not in (4, 6) and len(pkt) > 14 and (pkt[14] >> 4) in (4, 6):
        pkt = pkt[14:]
    ver = pkt[0] >> 4
    if ver == 4:
        ihl = (pkt[0] & 0xf) * 4
        proto = pkt[9]
        src = ".".join(str(b) for b in pkt[12:16])
        dst = ".".join(str(b) for b in pkt[16:20])
        payload = pkt[ihl:]
    elif ver == 6:
        proto = pkt[6]
        src = ":".join("%02x%02x" % (pkt[8+i], pkt[9+i]) for i in range(0, 16, 2))
        dst = ":".join("%02x%02x" % (pkt[24+i], pkt[25+i]) for i in range(0, 16, 2))
        payload = pkt[40:]
    else:
        continue
    key = "%s proto=%d" % (dst, proto)
    seen[key] = seen.get(key, 0) + 1
    if proto == 17 and len(payload) >= 8:
        udp += 1
        sport, dport = struct.unpack_from("!HH", payload, 0)
        body = payload[8:]
        if 5060 in (sport, dport) or body[:7] in (b"REGISTE", b"SIP/2.0"):
            sip += 1
            print("\n--- SIP %s:%d -> %s:%d, %d bytes" %
                  (src, sport, dst, dport, len(body)))
            print(body[:400].decode("ascii", "replace"))

print("\npackets=%d udp=%d sip=%d" % (total, udp, sip))
print("destinations seen:")
for k, v in sorted(seen.items(), key=lambda x: -x[1])[:10]:
    print("  %-40s %d" % (k, v))
