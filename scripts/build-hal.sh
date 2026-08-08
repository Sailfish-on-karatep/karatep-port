#!/bin/bash
# HADK "Building the Android HAL" -- run from inside the HABUILD SDK.
set -e
# The HADK runs `hadk` (which sources the env) before breakfast. PORT_ARCH must be
# exported: droidmedia's target list comes from
# `detect_build_targets.sh $(PORT_ARCH) $(TARGET_ARCH)`, and without it the args
# shift, the script exits 1, and `droidmedia` builds nothing while make reports
# success.
source /parentroot/parentroot/opencloud/hadk.env
cd "$ANDROID_ROOT"
source build/envsetup.sh
export USE_CCACHE=1
# HADK specifies `breakfast $DEVICE`, not `lunch`: breakfast resolves the LineageOS
# product for the device and validates the device tree.
breakfast karatep
make -j$(nproc --all) hybris-hal droidmedia
