#!/bin/bash
# Repackage droid-hal and droid-configs after a kernel change.
#
# RUN FROM INSIDE THE PLATFORM SDK:
#     /opencloud/bin/sfossdk /opencloud/bin/build-hal-packages.sh
#
# droid-hal must be rebuilt after ANY kernel change: wlan.ko is packaged in
# droid-hal-karatep-kernel-modules and the prima module refuses to load
# against a kernel it was not built for (vermagic mismatch -> ENODEV, no
# wlan0). Rebuilding hybris-hal alone is not enough -- the module has to be
# repackaged too, or the image still ships the old one.
#
# droid-configs is rebuilt here as well because the adaptation pattern is part
# of it, and pattern changes only reach the image through a new
# droid-config RPM.
set -e

source /opencloud/hadk.env
cd "$ANDROID_ROOT"

# droid-hal packages out/'s updater as /boot/update-binary, and that copy does
# not run. Must happen after the last Android build and before -d.
/parentroot/opencloud/bin/install-clean-updater.sh

echo "=== droid-hal-device (kernel modules, boot image) ==="
rpm/dhd/helpers/build_packages.sh -d

echo "=== droid-configs (sparse overlay + patterns) ==="
rpm/dhd/helpers/build_packages.sh -c

echo
echo "=== kernel-modules RPM ==="
ls -lt "$ANDROID_ROOT/droid-local-repo/$DEVICE"/droid-hal-"$DEVICE"-kernel-modules-*.rpm | head -2
