#!/usr/bin/env python3
"""
Minimal Wayland registry dumper -- no bindings, just the wire protocol.

Exists because nothing on the device can answer the only question that matters
for the Waydroid touch bug: which globals does lipstick advertise, at what
versions, and does its wl_seat claim WL_SEAT_CAPABILITY_TOUCH?

Waydroid's Android-side Wayland client (hwcomposer/wayland-hwc.cpp) prefers
xdg_wm_base over wl_shell whenever both are present, and only creates a
wl_touch -- and therefore only ever writes /dev/input/wl_touch_events -- if the
seat advertises the touch capability. Both facts are checkable from here.

Usage:  wlinfo.py [/path/to/wayland-0]
"""
import os
import socket
import struct
import sys

DISPLAY_ID = 1
REGISTRY_ID = 2
SYNC_ID = 3
SEAT_ID = 4

WL_SEAT_CAPABILITY_POINTER = 1
WL_SEAT_CAPABILITY_KEYBOARD = 2
WL_SEAT_CAPABILITY_TOUCH = 4


def pad4(n):
    return (n + 3) & ~3


def enc_str(s):
    """Wayland string: uint32 length INCLUDING the NUL, then NUL-terminated
    bytes padded out to a 4-byte boundary."""
    raw = s.encode() + b"\0"
    return struct.pack("<I", len(raw)) + raw + b"\0" * (pad4(len(raw)) - len(raw))


def msg(obj, opcode, body=b""):
    size = 8 + len(body)
    return struct.pack("<II", obj, (size << 16) | opcode) + body


class Conn:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.buf = b""

    def send(self, data):
        self.sock.sendall(data)

    def recv_msg(self):
        while len(self.buf) < 8:
            if not self._fill():
                return None
        obj, word = struct.unpack("<II", self.buf[:8])
        size, opcode = word >> 16, word & 0xFFFF
        while len(self.buf) < size:
            if not self._fill():
                return None
        body = self.buf[8:size]
        self.buf = self.buf[size:]
        return obj, opcode, body

    def _fill(self):
        # Wayland passes fds as SCM_RIGHTS; we ignore them but must drain them
        # or the peer's fd table fills up. recvmsg lets us discard cleanly.
        try:
            data, _anc, _flags, _addr = self.sock.recvmsg(4096, socket.CMSG_SPACE(16 * 4))
        except OSError:
            return False
        if not data:
            return False
        self.buf += data
        return True


def read_str(body, off):
    (n,) = struct.unpack_from("<I", body, off)
    off += 4
    s = body[off:off + n - 1].decode(errors="replace")
    return s, off + pad4(n)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        rt = os.environ.get("XDG_RUNTIME_DIR", "")
        disp = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
        path = disp if disp.startswith("/") else os.path.join(rt, disp)
    print("connecting to %s" % path)

    c = Conn(path)
    c.send(msg(DISPLAY_ID, 1, struct.pack("<I", REGISTRY_ID)))   # get_registry
    c.send(msg(DISPLAY_ID, 0, struct.pack("<I", SYNC_ID)))       # sync

    globals_ = {}
    seat_name = None
    seat_version = 0

    while True:
        m = c.recv_msg()
        if m is None:
            print("!! connection closed")
            return 1
        obj, opcode, body = m

        if obj == DISPLAY_ID and opcode == 0:      # wl_display.error
            oid, code = struct.unpack_from("<II", body, 0)
            emsg, _ = read_str(body, 8)
            print("!! protocol error obj=%d code=%d: %s" % (oid, code, emsg))
            return 1

        if obj == REGISTRY_ID and opcode == 0:     # wl_registry.global
            (name,) = struct.unpack_from("<I", body, 0)
            iface, off = read_str(body, 4)
            (version,) = struct.unpack_from("<I", body, off)
            globals_[iface] = version
            if iface == "wl_seat":
                seat_name, seat_version = name, version

        if obj == SYNC_ID and opcode == 0:         # wl_callback.done
            break

    print("\n=== globals advertised by the compositor ===")
    for iface in sorted(globals_):
        print("  %-40s v%d" % (iface, globals_[iface]))

    print("\n=== what Waydroid's hwcomposer will do ===")
    if "xdg_wm_base" in globals_:
        print("  xdg_wm_base present -> client takes the XDG SHELL path")
    elif "wl_shell" in globals_:
        print("  no xdg_wm_base, wl_shell present -> client takes the WL_SHELL path")
    else:
        print("  neither xdg_wm_base nor wl_shell -> client abort()s")

    if seat_name is None:
        print("\n!! no wl_seat advertised at all -- no input of any kind")
        return 0

    # Bind the seat and wait for its capabilities event.
    bind_body = (struct.pack("<I", seat_name) + enc_str("wl_seat")
                 + struct.pack("<II", min(seat_version, 5), SEAT_ID))
    c.send(msg(REGISTRY_ID, 0, bind_body))
    c.send(msg(DISPLAY_ID, 0, struct.pack("<I", SYNC_ID + 1)))

    caps = None
    while True:
        m = c.recv_msg()
        if m is None:
            break
        obj, opcode, body = m
        if obj == SEAT_ID and opcode == 0:         # wl_seat.capabilities
            (caps,) = struct.unpack_from("<I", body, 0)
        if obj == SYNC_ID + 1 and opcode == 0:
            break

    print("\n=== wl_seat (v%d) capabilities ===" % seat_version)
    if caps is None:
        print("  !! seat sent no capabilities event")
    else:
        print("  raw = 0x%x" % caps)
        print("  pointer  : %s" % bool(caps & WL_SEAT_CAPABILITY_POINTER))
        print("  keyboard : %s" % bool(caps & WL_SEAT_CAPABILITY_KEYBOARD))
        print("  touch    : %s" % bool(caps & WL_SEAT_CAPABILITY_TOUCH))
        if not caps & WL_SEAT_CAPABILITY_TOUCH:
            print("  -> hwcomposer never creates a wl_touch, so it never writes")
            print("     /dev/input/wl_touch_events. This alone explains dead touch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
