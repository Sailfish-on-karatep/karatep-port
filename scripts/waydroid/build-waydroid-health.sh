#!/bin/bash
# Rebuild Waydroid's health HAL -- run from inside the HABUILD SDK.
#
# Produces android.hardware.health@2.0-service.waydroid from hardware/waydroid
# (our fork, branch hybris-18.1), which reports the host's real battery instead
# of the hardcoded 85%-and-charging mock upstream ships. See
# karatep-port/docs/rca/waydroid-battery-mocked.md.
#
# Like the hwcomposer, this builds against *this* tree because the installed
# Waydroid vendor image is HALIUM_11, i.e. Android 11, the same API level as
# LineageOS 18.1 -- and the binary is a vendor one (proprietary: true), so it
# links only against vendor libraries. It is installed through Waydroid's own
# overlay (/var/lib/waydroid/overlay/vendor/bin/hw/), so no Waydroid image is
# rebuilt and the change is reverted by deleting one file.
#
# Only one Soong build may run at a time -- a second dies on "Tried to lock
# out/.lock, but timed out". Serialise this against build-hal.sh.
set -e
source /parentroot/parentroot/opencloud/hadk.env
cd "$ANDROID_ROOT"
source build/envsetup.sh
export USE_CCACHE=1
breakfast karatep
make -j$(nproc --all) android.hardware.health@2.0-service.waydroid

echo
echo "=== built artifacts ==="
find out/target/product/karatep -name "android.hardware.health@2.0-service.waydroid" \
     -type f -exec ls -l {} \;
