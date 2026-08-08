#!/bin/bash
# Rebuild Waydroid's hwcomposer HAL -- run from inside the HABUILD SDK.
#
# Produces hwcomposer.waydroid.so from hardware/waydroid (our fork, branch
# hybris-18.1), which takes the wl_shell path instead of xdg-shell. Waydroid
# prefers xdg-shell whenever the compositor advertises xdg_wm_base, and lipstick
# started advertising it in Sailfish OS 5.1 with a deliberately partial
# implementation on which Waydroid's surfaces render but never receive touch.
# See karatep-port/docs/rca/waydroid-touch-xdg-shell.md.
#
# This builds against *this* tree because the installed Waydroid vendor image is
# HALIUM_11, i.e. Android 11, the same API level as LineageOS 18.1. The result is
# installed through Waydroid's own overlay
# (/var/lib/waydroid/overlay/vendor/lib64/hw/), so no Waydroid image is rebuilt
# and the change is reverted by deleting one file.
#
# Only one Soong build may run at a time -- a second dies on "Tried to lock
# out/.lock, but timed out". Serialise this against build-hal.sh.
set -e
source /parentroot/parentroot/opencloud/hadk.env
cd "$ANDROID_ROOT"
source build/envsetup.sh
export USE_CCACHE=1
breakfast karatep
make -j$(nproc --all) hwcomposer.waydroid

echo
echo "=== built artifacts ==="
find out/target/product/karatep -name "hwcomposer.waydroid.so" -exec ls -l {} \;
