#!/bin/bash
# Replace out/'s updater with the one built from the unpatched tree.
#
# RUN FROM THE HOST, after the last Android build and before
# build_packages.sh -d.
#
# See karatep-port/docs/rca/broken-update-binary.md.
set -e

source /opencloud/hadk.env

CLEAN=/opencloud/prebuilts/recovery/update-binary
DEST=$ANDROID_ROOT/out/target/product/$DEVICE/system/bin/updater

if [ ! -f "$CLEAN" ]; then
    echo "error: $CLEAN missing -- run bin/build-los-recovery.sh first" >&2
    exit 1
fi

cmp -s "$CLEAN" "$DEST" && { echo "updater already clean"; exit 0; }
cp -a "$CLEAN" "$DEST"
echo "installed clean updater -> $DEST"
