#!/bin/sh
# Capture what the container's camera provider actually looks for.
#
# Runs ON THE DEVICE, from the host side (strace is /usr/bin/strace on Sailfish;
# the container has none). The provider is respawned by init every 5 seconds and
# lives about 40 ms, so attaching to it directly always loses the race -- by the
# time strace attaches, hw_get_module has already probed its paths. Instead we
# follow the container's init with -f and let strace pick the child up at fork.
#
# hw_get_module_by_class probes, in order, camera.$(ro.hardware.camera).so,
# camera.$(ro.hardware).so, camera.$(ro.product.board).so,
# camera.$(ro.board.platform).so, camera.$(ro.arch).so, camera.default.so --
# under /odm/lib/hw, /vendor/lib/hw and /system/lib/hw. Since Android 8 each
# candidate must also survive path_in_path(), which realpath()s it and rejects
# anything resolving outside the directory it was found in, so a symlink out to
# /vendor_extra is refused even though it reads fine.
#
# The trace shows exactly which names and directories are tried and what each
# call returns, which settles it either way.
set -e

OUT=/tmp/camera.strace
SECS="${1:-14}"

PID=$(lxc-info -P /var/lib/waydroid/lxc -n waydroid 2>/dev/null | awk '/^PID:/{print $2}')
[ -n "$PID" ] || { echo "container not running"; exit 1; }
echo "following container init pid $PID for ${SECS}s"

strace -f -p "$PID" -e trace=execve,openat,access,readlinkat,faccessat \
       -s 200 -o "$OUT" 2>/dev/null &
SPID=$!
sleep "$SECS"
kill "$SPID" 2>/dev/null || true
sleep 1

echo "=== camera-related probes ==="
grep -iE "camera" "$OUT" | grep -viE "cameraserver|cameraservice" | tail -40
echo
echo "=== every path probed under lib/hw ==="
grep -oE '"[^"]*lib(64)?/hw/[^"]*"' "$OUT" | sort -u | head -30
echo
echo "(full trace in $OUT)"
