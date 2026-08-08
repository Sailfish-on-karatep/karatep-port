#!/usr/bin/env python3
"""Run a command non-interactively on the karatep debug shell.

  devshell.py 'command' [timeout] [port]

Ports: 23   = Mer Boat Loader shell, pre-switch_root (recovery / installer)
       2323 = post-switch_root shell (a booted or bootlooping rootfs)
"""
import socket
import sys
import time
import uuid

HOST = "192.168.2.15"


def run(cmd, timeout=30, port=2323):
    beg = "__B%s__" % uuid.uuid4().hex[:8]
    end = "__E%s__" % uuid.uuid4().hex[:8]
    s = socket.create_connection((HOST, port), timeout=10)
    s.settimeout(1.5)
    t0 = time.time()
    while time.time() - t0 < 3:                       # drain banner
        try:
            if not s.recv(65536):
                break
        except socket.timeout:
            break
    s.sendall(("echo %s\n%s\necho %s\n" % (beg, cmd, end)).encode())
    out, t0 = b"", time.time()
    while time.time() - t0 < timeout:
        try:
            chunk = s.recv(65536)
            if not chunk:
                break
            out += chunk
            if end.encode() in out.split(b"echo " + end.encode())[-1]:
                break
        except socket.timeout:
            if end.encode() in out:
                break
    s.close()
    txt = out.decode("utf-8", "replace")
    if beg in txt:
        txt = txt.split(beg)[-1].split("\n", 1)[-1]
    if end in txt:
        txt = txt.rsplit(end, 1)[0]
    return "\n".join(l for l in txt.splitlines()
                     if end not in l and beg not in l).strip("\n")


if __name__ == "__main__":
    print(run(sys.argv[1],
              int(sys.argv[2]) if len(sys.argv) > 2 else 30,
              int(sys.argv[3]) if len(sys.argv) > 3 else 2323))
