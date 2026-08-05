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

ANDROID_ROOT="${ANDROID_ROOT:-/opencloud/hadk}"
RELEASE_DIR=""                    # auto-discovered below unless --release-dir is given
RECOVERY_IMG="$ANDROID_ROOT/out/target/product/karatep/hybris-recovery.img"
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

# The image directory and tarball names depend on how the image was built:
# build_packages.sh -i names them from the droid-config kickstart
# (SailfishOScommunity-release-$RELEASE-$DEVICE/sfe-$DEVICE-$RELEASE.tar.bz2), while a
# manual `mic create fs` may produce sailfishos-$DEVICE-release-$RELEASE.tar.bz2. Rather
# than hardcode either, find the newest directory that actually holds a rootfs tarball.
find_rootfs() {
    local dir=$1
    ls -1t "$dir"/sfe-*.tar.bz2 "$dir"/sailfishos-*.tar.bz2 2>/dev/null | head -1
}

if [ -n "$RELEASE_DIR" ]; then
    ROOTFS=$(find_rootfs "$RELEASE_DIR")
    [ -n "$ROOTFS" ] || die "no rootfs tarball (sfe-*.tar.bz2 / sailfishos-*.tar.bz2) in $RELEASE_DIR"
else
    # Newest tarball one level below $ANDROID_ROOT wins; skip archived builds.
    ROOTFS=$(ls -1t "$ANDROID_ROOT"/*/sfe-*.tar.bz2 \
                    "$ANDROID_ROOT"/*/sailfishos-*.tar.bz2 2>/dev/null \
             | grep -vE '\.(prev|old|bak)/' | head -1 || true)
    [ -n "$ROOTFS" ] \
        || die "could not find a built image under $ANDROID_ROOT -- pass --release-dir explicitly"
    RELEASE_DIR=$(dirname "$ROOTFS")
fi

# hybris-boot.img is only present in the release directory when mic extracted the
# kickstart's %attachment section; build_packages.sh -i does not always leave it there.
# The authoritative copy is the one the Android build produced.
BOOT_IMG="$RELEASE_DIR/hybris-boot.img"
if [ ! -f "$BOOT_IMG" ]; then
    BOOT_IMG="$ANDROID_ROOT/out/target/product/karatep/hybris-boot.img"
fi
[ -f "$BOOT_IMG" ] \
    || die "no hybris-boot.img in $RELEASE_DIR or $ANDROID_ROOT/out/target/product/karatep"
[ -f "$RECOVERY_IMG" ] || die "missing $RECOVERY_IMG"

# The recovery downloads it over HTTP from $RELEASE_DIR, so make sure it is there.
if [ ! -f "$RELEASE_DIR/hybris-boot.img" ]; then
    cp "$BOOT_IMG" "$RELEASE_DIR/hybris-boot.img" 2>/dev/null \
        || die "cannot copy hybris-boot.img into $RELEASE_DIR (root-owned? run: sudo cp '$BOOT_IMG' '$RELEASE_DIR/')"
fi

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
    # NB: plain `ping ... && break` would abort the script under `set -e` on the
    # first failed probe, which is exactly the case we are waiting through.
    if ping -c1 -W1 "$DEVICE_IP" >/dev/null 2>&1; then break; fi
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

# ------------------------------------- make sure we got the PRE-switch_root shell
#
# If a Sailfish rootfs is already installed, hybris init finds /target and
# switch_root's straight into it, even when booted from hybris-recovery.img. You
# then get the post-switch_root shell on 2323 instead of the installer shell on 23,
# and /data/.stowaways/sailfishos is busy so nothing can be replaced.
#
# hybris-boot's own escape hatch (init-script: "[ -f /target/init_enter_debug ]")
# is to drop a flag file in the installed rootfs, which makes init halt *before*
# switch_root. Set it via the 2323 shell, bounce to fastboot and re-boot the
# recovery, and port 23 appears.
port_open() { timeout 3 bash -c "echo > /dev/tcp/$DEVICE_IP/$1" 2>/dev/null; }

if ! port_open "$TELNET_PORT"; then
    if port_open 2323; then
        warn "Port $TELNET_PORT is closed but 2323 is open: the recovery switch_root'ed into"
        warn "the existing install. Setting /init_enter_debug so it halts before that."
        DEVICE_IP="$DEVICE_IP" python3 - <<'PYEOF' || die "could not set /init_enter_debug over the 2323 shell"
import os, socket, time
s = socket.create_connection((os.environ["DEVICE_IP"], 2323), timeout=15)
time.sleep(2)
try:
    s.recv(65536)
except Exception:
    pass
s.sendall(b"touch /init_enter_debug; sync\n")
time.sleep(3)
s.sendall(b"(sleep 2; /system/bin/reboot bootloader || reboot -f) >/dev/null 2>&1 &\n")
time.sleep(2)
s.close()
PYEOF
        say "Rebooting to fastboot and re-booting the recovery"
        for _ in $(seq 1 60); do
            if fastboot devices 2>/dev/null | grep -q .; then break; fi
            sleep 2
        done
        fastboot devices 2>/dev/null | grep -q . || die "device did not return to fastboot"
        fastboot boot "$RECOVERY_IMG" || die "fastboot boot failed on retry"
        for _ in $(seq 1 60); do
            if port_open "$TELNET_PORT"; then break; fi
            sleep 2
        done
    fi
fi
port_open "$TELNET_PORT" \
    || die "no installer shell on $DEVICE_IP:$TELNET_PORT. If 2323 is open the device booted
       its installed rootfs; see docs/flashing.md."

# ------------------------------------------------------------------ http server

if command -v ss >/dev/null && ss -ltn 2>/dev/null | grep -q ":$HTTP_PORT "; then
    die "port $HTTP_PORT is already in use; pass --port to pick another"
fi

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
    """Run one command, return its output. Uses a sentinel so we know it finished.

    The sentinel is sent SPLIT ("__done""_123__") so the literal string only ever
    appears in the command's *output*, never in the shell's echo of the input
    line. Matching the echo instead of the output is a trap: the wait returns
    instantly, and the next command then runs while this one is still going --
    which previously made the rootfs check read wget's progress bar and report a
    corrupt extraction.
    """
    tag = time.time_ns()
    marker = "__done_%d__" % tag
    sock.sendall(('%s; echo "__done""_%d__"$?\n' % (cmd, tag)).encode())
    out, end = "", time.time() + 1800         # extraction over USB is slow
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
