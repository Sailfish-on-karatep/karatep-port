#!/usr/bin/env python3
"""
Does lipstick deliver wl_touch to an xdg_shell surface, or only to a wl_shell one?

Waydroid's Android-side Wayland client (hwcomposer/wayland-hwc.cpp) prefers
xdg_wm_base over wl_shell whenever the compositor advertises both, and lipstick
gained xdg_shell in commit 4b9745ef ("[compositor] Add XDG shell support",
2026-03-30), shipping in Sailfish OS 5.1 -- the exact release where Waydroid
touch stopped working. This maps a real surface by each route and reports which
one receives touch, with Waydroid removed from the experiment entirely.

Usage:  wltouch.py {xdg|wl} [seconds]     then tap the screen.

Pure wire protocol -- no Wayland bindings exist on the device. Note that
libwayland's object map requires client-allocated ids to be dense, so every id
comes from next_id() in creation order; a gap earns
"invalid arguments for wl_registry#2.bind".
"""
import os
import socket
import struct
import sys
import time

W, H = 720, 1280
STRIDE = W * 4
SIZE = STRIDE * H

DISPLAY, REGISTRY = 1, 2
_next = [REGISTRY]


def next_id():
    _next[0] += 1
    return _next[0]


def pad4(n):
    return (n + 3) & ~3


def enc_str(s):
    raw = s.encode() + b"\0"
    return struct.pack("<I", len(raw)) + raw + b"\0" * (pad4(len(raw)) - len(raw))


def read_str(body, off):
    (n,) = struct.unpack_from("<I", body, off)
    off += 4
    return body[off:off + n - 1].decode(errors="replace"), off + pad4(n)


def fx(v):
    return v / 256.0


class Conn:
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.buf = b""

    def send(self, obj, opcode, body=b"", fds=None):
        size = 8 + len(body)
        data = struct.pack("<II", obj, (size << 16) | opcode) + body
        if fds:
            anc = [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                    struct.pack("<%dI" % len(fds), *fds))]
            self.s.sendmsg([data], anc)
        else:
            self.s.sendall(data)

    def recv(self, timeout=None):
        self.s.settimeout(timeout)
        while True:
            if len(self.buf) >= 8:
                obj, word = struct.unpack("<II", self.buf[:8])
                size = word >> 16
                if len(self.buf) >= size:
                    body = self.buf[8:size]
                    self.buf = self.buf[size:]
                    return obj, word & 0xFFFF, body
            try:
                data, _a, _f, _ad = self.s.recvmsg(4096, socket.CMSG_SPACE(16 * 4))
            except (socket.timeout, OSError):
                return None
            if not data:
                return None
            self.buf += data


def make_shm_fd():
    if hasattr(os, "memfd_create"):
        fd = os.memfd_create("wltouch", 0)
    else:
        rt = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
        p = os.path.join(rt, ".wltouch.%d" % os.getpid())
        fd = os.open(p, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        os.unlink(p)
    os.ftruncate(fd, SIZE)
    os.lseek(fd, 0, 0)
    row = struct.pack("<I", 0xFF404060) * W
    for _ in range(H):
        os.write(fd, row)
    return fd


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "xdg"
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 25.0
    if mode not in ("xdg", "wl"):
        print("usage: wltouch.py {xdg|wl} [seconds]")
        return 2

    path = os.environ.get("WAYLAND_SOCKET_PATH", "/run/display/wayland-0")
    c = Conn(path)

    c.send(DISPLAY, 1, struct.pack("<I", REGISTRY))
    sid = next_id()
    c.send(DISPLAY, 0, struct.pack("<I", sid))

    names = {}
    while True:
        m = c.recv(5)
        if m is None:
            print("!! no reply from compositor")
            return 1
        obj, op, body = m
        if obj == DISPLAY and op == 0:
            oid, code = struct.unpack_from("<II", body, 0)
            print("!! error obj=%d code=%d: %s" % (oid, code, read_str(body, 8)[0]))
            return 1
        if obj == REGISTRY and op == 0:
            (name,) = struct.unpack_from("<I", body, 0)
            iface, off = read_str(body, 4)
            (ver,) = struct.unpack_from("<I", body, off)
            names[iface] = (name, ver)
        if obj == sid and op == 0:
            break

    shell_iface = "xdg_wm_base" if mode == "xdg" else "wl_shell"
    for n in ["wl_compositor", "wl_shm", "wl_seat", shell_iface]:
        if n not in names:
            print("!! compositor does not advertise %s" % n)
            return 1

    # Events that arrive during a checkpoint round trip are kept, not dropped:
    # lipstick sends xdg_surface.configure / xdg_toplevel.configure immediately
    # after get_toplevel, which lands inside a checkpoint window. Discarding
    # them made it look like the compositor never configured the surface.
    queue = []

    def checkpoint(tag):
        s = next_id()
        c.send(DISPLAY, 0, struct.pack("<I", s))
        while True:
            m = c.recv(4)
            if m is None:
                print("!! %s: compositor went away" % tag)
                sys.exit(1)
            o, op, b = m
            if o == DISPLAY and op == 0:
                oid, code = struct.unpack_from("<II", b, 0)
                print("!! %s: protocol error obj=%d code=%d: %s"
                      % (tag, oid, code, read_str(b, 8)[0]))
                sys.exit(1)
            if o == s and op == 0:
                return
            queue.append(m)

    def bind(iface, ver):
        name, adv = names[iface]
        v = min(ver, adv)
        oid = next_id()
        c.send(REGISTRY, 0, struct.pack("<I", name) + enc_str(iface)
               + struct.pack("<II", v, oid))
        checkpoint("bind " + iface)
        print("   bound %-16s v%-2d (advertised v%d) as id %d" % (iface, v, adv, oid))
        sys.stdout.flush()
        return oid, v

    compositor, _ = bind("wl_compositor", 3)
    shm, _ = bind("wl_shm", 1)
    seat, seat_v = bind("wl_seat", 3)
    shell, _ = bind(shell_iface, 1)

    touch = next_id()
    c.send(seat, 2, struct.pack("<I", touch))              # wl_seat.get_touch
    checkpoint("get_touch")

    surface = next_id()
    c.send(compositor, 0, struct.pack("<I", surface))      # create_surface
    checkpoint("create_surface")

    fd = make_shm_fd()
    pool = next_id()
    c.send(shm, 0, struct.pack("<Ii", pool, SIZE), fds=[fd])
    os.close(fd)
    checkpoint("create_pool")

    buf = next_id()
    c.send(pool, 0, struct.pack("<Iiiiii", buf, 0, W, H, STRIDE, 1))
    checkpoint("create_buffer")

    xdg_surface = xdg_toplevel = shell_surface = None
    if mode == "xdg":
        xdg_surface = next_id()
        c.send(shell, 2, struct.pack("<II", xdg_surface, surface))
        checkpoint("get_xdg_surface")
        xdg_toplevel = next_id()
        c.send(xdg_surface, 1, struct.pack("<I", xdg_toplevel))
        c.send(xdg_toplevel, 2, enc_str("wltouch-probe"))   # set_title
        c.send(xdg_toplevel, 3, enc_str("wltouch-probe"))   # set_app_id
        c.send(surface, 6)                                  # commit; await configure
        checkpoint("xdg toplevel committed")
    else:
        shell_surface = next_id()
        c.send(shell, 0, struct.pack("<II", shell_surface, surface))
        checkpoint("get_shell_surface")
        c.send(shell_surface, 3)                            # set_toplevel
        c.send(shell_surface, 8, enc_str("wltouch-probe"))  # set_title
        c.send(surface, 1, struct.pack("<Iii", buf, 0, 0))
        c.send(surface, 2, struct.pack("<iiii", 0, 0, W, H))
        c.send(surface, 6)
        checkpoint("wl_shell surface committed")

    print("\nmode=%s  seat=v%d  -- SURFACE MAPPED, TAP THE SCREEN NOW (%ds)\n"
          % (mode, seat_v, int(secs)))
    sys.stdout.flush()

    touches = 0
    configured = False
    end = time.time() + secs
    while time.time() < end:
        if queue:
            m = queue.pop(0)
        else:
            m = c.recv(1.0)
        if m is None:
            continue
        obj, op, body = m

        if obj == DISPLAY and op == 0:
            oid, code = struct.unpack_from("<II", body, 0)
            print("!! protocol error obj=%d code=%d: %s"
                  % (oid, code, read_str(body, 8)[0]))
            return 1

        if obj == shell and mode == "xdg" and op == 0:          # xdg_wm_base.ping
            (serial,) = struct.unpack_from("<I", body, 0)
            c.send(shell, 3, struct.pack("<I", serial))

        if obj == shell_surface and op == 0:                    # wl_shell_surface.ping
            (serial,) = struct.unpack_from("<I", body, 0)
            c.send(shell_surface, 0, struct.pack("<I", serial))

        if obj == xdg_surface and op == 0:                      # xdg_surface.configure
            (serial,) = struct.unpack_from("<I", body, 0)
            c.send(xdg_surface, 4, struct.pack("<I", serial))   # ack_configure
            if not configured:
                configured = True
                c.send(surface, 1, struct.pack("<Iii", buf, 0, 0))
                c.send(surface, 2, struct.pack("<iiii", 0, 0, W, H))
                c.send(surface, 6)
                print("   xdg_surface.configure acked, buffer attached")
                sys.stdout.flush()

        if obj == xdg_toplevel and op == 0:                     # xdg_toplevel.configure
            w, h = struct.unpack_from("<ii", body, 0)
            (n,) = struct.unpack_from("<I", body, 8)
            st = list(struct.unpack_from("<%dI" % (n // 4), body, 12)) if n else []
            print("   xdg_toplevel.configure %dx%d states=%s%s"
                  % (w, h, st, "  <-- ACTIVATED" if 4 in st else ""))
            sys.stdout.flush()

        if obj == touch:
            if op == 0:
                _s, _t, _srf, tid, x, y = struct.unpack_from("<IIIiii", body, 0)
                touches += 1
                print("   *** wl_touch.DOWN id=%d at %.0f,%.0f" % (tid, fx(x), fx(y)))
            elif op == 1:
                print("   *** wl_touch.UP")
            elif op == 2:
                touches += 1
            sys.stdout.flush()

    print("\nRESULT mode=%s: %d touch events -> %s"
          % (mode, touches, "TOUCH DELIVERED" if touches else "NO TOUCH DELIVERED"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
