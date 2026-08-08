#!/bin/bash
# Rebuild droid-hal-version and then the flashable image.
#
# RUN FROM INSIDE THE PLATFORM SDK:
#     /opencloud/bin/sfossdk /opencloud/bin/build-image.sh
#
# -v is included because droid-hal-version pins the droid-hal package versions,
# and droid-hal's release string carries a build timestamp -- so it moves on
# every -d rebuild and the version package has to follow it.
#
# -i runs `sudo mic create fs`. The mersdk user has passwordless sudo inside the
# Platform SDK chroot, so this does not prompt.
#
# $RELEASE must be exported or build_packages.sh --mic refuses to run; hadk.env
# sets it to 5.1.0.11.
#
# NOTE: must live under /opencloud, not /tmp -- the Platform SDK is a chroot
# with its own /tmp, so a script written to the host /tmp is invisible inside it.
set -e

source /opencloud/hadk.env
cd "$ANDROID_ROOT"

# Guard: out/'s updater must be the one built from the unpatched tree.
#
# droid-hal packages out/target/product/$DEVICE/system/bin/updater as
# /boot/update-binary, which the kickstart then packs into the zip as
# META-INF/com/google/android/update-binary. The copy the hybris-patched tree
# produces is statically linked against patched bionic and dies on a stock
# LineageOS recovery with "killed by signal 7" straight after "Installing
# update...", wasting a full sign + 554 MB sideload before it shows up.
#
# ANY `make` regenerates the patched binary, so bin/install-clean-updater.sh has
# to run after the last Android build and before -d. bin/build-hal-packages.sh
# does that; a hand-run `build_packages.sh -d` does not. Fail loudly here rather
# than ship a zip that cannot install.
#
# See karatep-port/docs/rca/broken-update-binary.md.
CLEAN=/opencloud/prebuilts/recovery/update-binary
STAGED=$ANDROID_ROOT/out/target/product/$DEVICE/system/bin/updater
if [ -f "$CLEAN" ] && ! cmp -s "$CLEAN" "$STAGED"; then
    echo "ERROR: $STAGED is not the clean updater." >&2
    echo "       Run: /opencloud/bin/sfossdk /opencloud/bin/build-hal-packages.sh" >&2
    echo "       (installs the clean updater, then rebuilds droid-hal)" >&2
    exit 1
fi

echo "=== 1/2: droid-hal-version ==="
rpm/dhd/helpers/build_packages.sh -v

OUT="$ANDROID_ROOT/SailfishOScommunity-release-$RELEASE-$DEVICE"
ROOTFS="$OUT/sfe-$DEVICE-$RELEASE.tar.bz2"

echo "=== 2/2: image ==="
# mic asks "Target image/dir: ... already exists, clean up and continue?(Y/n)"
# whenever a previous rootfs is still in place. With no tty -- which is every
# non-interactive run -- it reads EOF and dies with a traceback, and
# build_packages.sh does NOT propagate that: it still exits 0, so `set -e` never
# fires and the build looks like it succeeded while silently leaving the OLD
# image in place. Answer the prompt; Y is the default and build_packages.sh
# already snapshots the previous outputs as .prev-<timestamp>.
printf 'y\n' | rpm/dhd/helpers/build_packages.sh -i

# Verify by mtime rather than trusting the exit status, for the reason above.
if [ -n "$(find "$ROOTFS" -newermt '-30 minutes' 2>/dev/null)" ]; then
    echo "OK: rootfs rebuilt just now"
else
    echo "ERROR: $ROOTFS was not regenerated -- mic did not run to completion" >&2
    exit 1
fi

echo
echo "=== image output ==="
ls -l "$OUT/" 2>/dev/null | grep -vE '\.(prev|stale)-' ||
    echo "(nothing found -- check the log above)"
