#!/usr/bin/python3
#
# Is the IMS signalling readable, or is it inside IPsec?
#
# Six NV items have now been corrected one service window at a time, against a
# symptom -- "SDP parse failed" -- whose actual content has never been seen. If
# the SIP on the IMS bearer is plaintext then the INVITE and its answer can be
# captured directly and the parse failure read off, instead of inferred from
# which operator config disagrees with which.
#
# So classify rather than grep: count IP protocol numbers per interface, and
# dump any plaintext SIP found. 50 is ESP (IPsec, unreadable), 17 UDP, 6 TCP.
# 3GPP IMS uses ports 5060/5061, and with IPsec the SIP rides on negotiated
# high ports instead, so a UDP flow that is not on 5060 and is not parseable as
# SIP is itself evidence of protection.
#
# Unbound AF_PACKET on purpose: the IMS bearer goes down and comes back during
# re-registration, and a bound socket dies with ENETDOWN mid-capture.
import socket
import struct
import sys
import time

SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 90
ETH_P_ALL = 3

s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
s.settimeout(2.0)

PROTO = {1: "icmp", 6: "tcp", 17: "udp", 41: "6in4", 50: "ESP", 51: "AH",
         58: "icmp6", 132: "sctp"}

counts = {}
sip_seen = []
ports = {}
end = time.time() + SECONDS
print("capturing %d s" % SECONDS)

while time.time() < end:
    try:
        pkt, addr = s.recvfrom(65535)
    except socket.timeout:
        continue
    except OSError:
        continue
    iface = addr[0]
    if len(pkt) < 20:
        continue

    # Raw IP on rmnet (no ethernet header); sniff both shapes.
    ver = pkt[0] >> 4
    if ver == 4:
        proto = pkt[9]
        ihl = (pkt[0] & 0xF) * 4
        payload = pkt[ihl:]
    elif ver == 6:
        proto = pkt[6]
        payload = pkt[40:]
    else:
        # Probably an ethernet header in front.
        if len(pkt) > 14 and pkt[12:14] == b"\x08\x00":
            proto = pkt[23]
            ihl = (pkt[14] & 0xF) * 4
            payload = pkt[14 + ihl:]
        else:
            continue

    key = (iface, PROTO.get(proto, str(proto)))
    counts[key] = counts.get(key, 0) + 1

    if proto in (6, 17) and len(payload) >= 8:
        sport, dport = struct.unpack_from(">HH", payload, 0)
        pk = (iface, sport if sport < dport else dport)
        ports[pk] = ports.get(pk, 0) + 1
        # TCP's header length is variable -- it is the top nibble of byte 12,
        # in 32-bit words. Assuming 20 bytes silently shifted every TCP payload
        # and made real SIP look like noise, which is why an earlier run
        # reported "no plaintext SIP" while counting packets on port 5060.
        if proto == 17:
            body = payload[8:]
        else:
            off = (payload[12] >> 4) * 4 if len(payload) > 12 else 20
            body = payload[off:]
        # Keep anything on the SIP ports whatever it looks like, plus anything
        # that parses as SIP on any port.
        # rmnet_data2 is the modem's own IMS PDN -- up with a /27 and MTU 1300
        # even when ofono's context3 (rmnet_data1) is inactive. The SIP endpoint
        # lives inside the modem so there is no AP socket for it, but the
        # packets still cross the netdev, so keep everything with a payload on
        # that interface whatever port it is on.
        is_sip_port = (5060 in (sport, dport) or 5061 in (sport, dport)
                       or iface == "rmnet_data2")
        looks_sip = (body[:7] in (b"SIP/2.0", b"INVITE ", b"REGISTE") or
                     body[:4] in (b"ACK ", b"BYE ") or
                     body[:8] in (b"OPTIONS ", b"SUBSCRIB") or
                     body[:7] == b"MESSAGE")
        if (is_sip_port or looks_sip) and len(body) > 4:
            if len(sip_seen) < 12:
                sip_seen.append((iface, sport, dport, body[:1200]))

print()
print("== packets by interface and protocol ==")
for (iface, proto), n in sorted(counts.items(), key=lambda x: -x[1])[:14]:
    print("  %-14s %-6s %d" % (iface, proto, n))

print()
print("== busiest ports (lower of src/dst) ==")
for (iface, port), n in sorted(ports.items(), key=lambda x: -x[1])[:12]:
    print("  %-14s %-6d %d" % (iface, port, n))

print()
if sip_seen:
    print("== TRAFFIC ON THE SIP PORTS (raw, may or may not be SIP) ==")
    for iface, sp, dp, body in sip_seen:
        print("-- %s %d->%d --" % (iface, sp, dp))
        try:
            print(body.decode("utf-8", "replace"))
        except Exception:
            print(repr(body))
else:
    print("== no plaintext SIP seen ==")
    esp = sum(n for (i, p), n in counts.items() if p == "ESP")
    if esp:
        print("   %d ESP packets: the signalling is inside IPsec and cannot be" % esp)
        print("   read from here. The SDP would have to come from the modem.")
    else:
        print("   and no ESP either -- either nothing registered during the")
        print("   window, or the signalling does not traverse an AP netdev.")
