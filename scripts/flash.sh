#!/usr/bin/env bash
#
# Semi-automated Sailfish OS installer for the Lenovo Vibe K6 Note / Plus (karatep).
#
# This automates docs/flashing.md. Read that first -- it explains why each step is
# what it is, and how to recover when something goes wrong.
#
# What this does NOT do, deliberately:
#   * It never flashes hybris-recovery.img. The recovery is live-booted with
#     `fastboot boot` so an existing TWRP install survives.
#   * It never runs `echo umount_stowaways > /init-ctl/stdin`. That belongs to the
#     USB mass-storage workflow and would unmount /data out from under us.
#   * It refuses to flash the boot partition unless the rootfs extracted correctly.
#
# Usage:
#   scripts/flash.sh [--release-dir DIR] [--recovery IMG] [--port N] [--skip-fastboot]
#
set -euo pipefail

DEVICE_IP="192.168.2.15"          # hybris-boot's fixed address on the USB RNDIS link
TELNET_PORT=23                    # Mer Boat Loader shell (pre-switch_root). 2323 is post-switch_root.
BOOT_PARTITION="/dev/mmcblk0p34"  # this recovery exposes no by-name symlinks
HTTP_PORT=8000

RELEASE_DIR="/opencloud/hadk/SailfishOScommunity-release-5.1.0.11-karatep"
RECOVERY_IMG="/opencloud/hadk/out/target/product/karatep/hybris-recovery.img"
SKIP_FASTBOOT=0

while [ $# -gt 0 ]; do
    case "$1" in
        --release-dir)   RELEASE_DIR=$2; shift 2 ;;
        --recovery)      RECOVERY_IMG=$2; shift 2 ;;
        --port)          HTTP_PORT=$2; shift 2 ;;
        --skip-fastboot) SKIP_FASTBOOT=1; shift ;;
        -h|--help)       sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m    %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- prerequisites

for tool in python3 fastboot; do
    command -v "$tool" >/dev/null || die "$tool is not installed"
done

ROOTFS=$(ls -1 "$RELEASE_DIR"/sailfishos-karatep-release-*.tar.bz2 2>/dev/null | head -1) \
    || die "no rootfs tarball in $RELEASE_DIR"
[ -n "$ROOTFS" ] || die "no sailfishos-karatep-release-*.tar.bz2 in $RELEASE_DIR"
BOOT_IMG="$RELEASE_DIR/hybris-boot.img"
[ -f "$BOOT_IMG" ] || die "missing $BOOT_IMG"
[ -f "$RECOVERY_IMG" ] || die "missing $RECOVERY_IMG"

say "Files"
printf '    rootfs   : %s (%s)\n' "$(basename "$ROOTFS")" "$(du -h "$ROOTFS" | cut -f1)"
printf '    boot     : %s\n' "$BOOT_IMG"
printf '    recovery : %s\n' "$RECOVERY_IMG"

# ------------------------------------------------------------- live-boot recovery

if [ "$SKIP_FASTBOOT" -eq 0 ]; then
    say "Live-booting hybris-recovery.img (NOT flashing it)"
    warn "Put the device into fastboot mode now if it isn't already."
    fastboot getvar product 2>&1 | head -1 || die "no fastboot device found"
    fastboot boot "$RECOVERY_IMG" || die "fastboot boot failed"
    warn "The device will sit on the Lenovo splash screen. That is expected."
fi

# ------------------------------------------------- wait for the USB network link

say "Waiting for the device at $DEVICE_IP"
for _ in $(seq 1 60); do
    ping -c1 -W1 "$DEVICE_IP" >/dev/null 2>&1 && break
    sleep 2
done
ping -c1 -W1 "$DEVICE_IP" >/dev/null 2>&1 \
    || die "device never appeared at $DEVICE_IP -- is the USB RNDIS interface up? (check: ip -br addr)"

# Discover our own address on that link rather than hardcoding it: ask the kernel
# which source address it would use to reach the device.
HOST_IP=$(ip -4 -o route get "$DEVICE_IP" 2>/dev/null | sed -n 's/.*[[:space:]]src[[:space:]]\([0-9.]\+\).*/\1/p')
[ -n "$HOST_IP" ] \
    || die "could not determine the host IP facing $DEVICE_IP (try: ip -4 route get $DEVICE_IP)"
say "Host address on the USB link: $HOST_IP"

# ------------------------------------------------------------------ http server

command -v ss >/dev/null && ss -ltn 2>/dev/null | grep -q ":$HTTP_PORT " \
    && die "port $HTTP_PORT is already in use; pass --port to pick another"

say "Serving $RELEASE_DIR on $HOST_IP:$HTTP_PORT"
python3 -m http.server "$HTTP_PORT" --bind "$HOST_IP" --directory "$RELEASE_DIR" \
    >/dev/null 2>&1 &
HTTP_PID=$!
cleanup() { [ -n "${HTTP_PID:-}" ] && kill "$HTTP_PID" 2>/dev/null || true; }
trap cleanup EXIT
sleep 1
kill -0 "$HTTP_PID" 2>/dev/null || die "http server failed to start"

# ------------------------------------------------------------ drive the recovery

say "Installing over the recovery shell (telnet $DEVICE_IP $TELNET_PORT)"
warn "This takes several minutes; the tarball is around 500 MB."

HOST_IP="$HOST_IP" DEVICE_IP="$DEVICE_IP" TELNET_PORT="$TELNET_PORT" \
HTTP_PORT="$HTTP_PORT" ROOTFS_NAME="$(basename "$ROOTFS")" \
BOOT_PARTITION="$BOOT_PARTITION" python3 - <<'PYEOF'
import os, socket, sys, time

HOST         = os.environ["DEVICE_IP"]
PORT         = int(os.environ["TELNET_PORT"])
HOST_IP      = os.environ["HOST_IP"]
HTTP_PORT    = os.environ["HTTP_PORT"]
ROOTFS_NAME  = os.environ["ROOTFS_NAME"]
BOOT_PART    = os.environ["BOOT_PARTITION"]
BASE         = f"http://{HOST_IP}:{HTTP_PORT}"

sock = socket.create_connection((HOST, PORT), timeout=15)
sock.settimeout(2)


def drain(seconds=2.0):
    """Collect whatever the shell has to say for `seconds`."""
    out, end = b"", time.time() + seconds
    while time.time() < end:
        try:
            chunk = sock.recv(65536)
            if not chunk:
                break
            out += chunk
        except socket.timeout:
            pass
    return out.decode("utf-8", "replace")


def run(cmd, settle=2.0, quiet=False):
    """Run one command, return its output. Uses a sentinel so we know it finished."""
    marker = "__done_%d__" % time.time_ns()
    sock.sendall(f"{cmd}; echo {marker}$?\n".encode())
    out, end = "", time.time() + 900          # extraction can legitimately take minutes
    while time.time() < end:
        out += drain(settle)
        if marker in out:
            break
    else:
        raise SystemExit(f"ERROR: timed out waiting for: {cmd}")
    status = out.split(marker)[1].split()[0] if marker in out else "?"
    body = out.split(marker)[0]
    if not quiet:
        for line in body.splitlines():
            if line.strip() and cmd not in line:
                print("    " + line.rstrip())
    return body, status


banner = drain(3)
if "Boat loader" not in banner and "#" not in banner:
    print("WARNING: unexpected greeting from the recovery shell:", banner[:200])

# /data is mounted automatically by this recovery. Confirm rather than mount it --
# mounting by-name fails here because the recovery has no by-name symlinks.
print("--> checking /data")
body, _ = run("mount | grep -c mmcblk0p54")
if "0" in body.split():
    raise SystemExit("ERROR: /data (mmcblk0p54) is not mounted; reboot into a fresh recovery")

# A busy /data means the recovery already tried to enter the installed system.
# docs/flashing.md: stop, do not continue.
print("--> clearing any previous installation")
body, status = run("rm -rf /data/.stowaways/sailfishos 2>&1; mkdir -p /data/.stowaways/sailfishos")
if "resource busy" in body.lower() or "Device or resource busy" in body:
    raise SystemExit(
        "ERROR: /data/.stowaways/sailfishos is busy -- the recovery has already tried to "
        "boot the installed system.\n"
        "       Reboot to fastboot, live-boot hybris-recovery.img again, and re-run."
    )

# This recovery ships wget, not curl.
print(f"--> extracting {ROOTFS_NAME} (several minutes)")
run(f"wget -O - {BASE}/{ROOTFS_NAME} | tar -xj -C /data/.stowaways/sailfishos", settle=5.0,
    quiet=True)

# Verify before touching the boot partition. A failed extraction leaves only "data".
print("--> verifying the extracted rootfs")
body, _ = run("ls /data/.stowaways/sailfishos")
present = set(body.split())
missing = {"bin", "etc", "usr", "var", "lib"} - present
if missing:
    raise SystemExit(
        f"ERROR: rootfs looks incomplete, missing {sorted(missing)}.\n"
        f"       Found: {sorted(present)}\n"
        "       NOT flashing the boot partition. Re-run the extraction."
    )
print("    rootfs looks sane")

print("--> fetching hybris-boot.img")
run(f"wget -O /tmp/hybris-boot.img {BASE}/hybris-boot.img", quiet=True)
body, _ = run("ls -l /tmp/hybris-boot.img")

print(f"--> writing hybris-boot.img to {BOOT_PART}")
run(f"dd if=/tmp/hybris-boot.img of={BOOT_PART}")
run("sync")

print("--> rebooting")
sock.sendall(b"reboot\n")
time.sleep(2)
sock.close()
print("done")
PYEOF

say "Installation finished"
warn "First boot takes 5-10 minutes while systemd settles and the filesystem expands."
warn "If it bootloops: telnet $DEVICE_IP 2323 (post-switch_root shell), then journalctl -b."
