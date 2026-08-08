#!/bin/bash
# Build the Android-side fingerprint library for sailfish-fpd-community.
#
# RUN FROM INSIDE THE HABUILD SDK:
#     /opencloud/bin/habuild /parentroot/parentroot/opencloud/bin/build-fpd.sh
#
# sailfish-fpd-community talks to the device's Android fingerprint HAL
# (android.hardware.biometrics.fingerprint@2.1 IBiometricsFingerprint on
# karatep) through libbiometry_fp_api, which is built inside the Android tree
# and then packaged as droid-biometry-fp.
#
# karatep is a 64-bit port, so it is libbiometry_fp_api (not the _32 variant
# used by 32-bit ports).
set -e

# Source the env before breakfast, as the HADK does: some targets read PORT_ARCH.
source /parentroot/parentroot/opencloud/hadk.env
cd "$ANDROID_ROOT"

source build/envsetup.sh
export USE_CCACHE=1

# HADK uses breakfast, not lunch -- the LineageOS device tree provides the combo.
breakfast karatep

make -j"$(nproc --all)" libbiometry_fp_api

echo
echo "=== build-fpd.sh: checking output ==="
ls -l out/target/product/karatep/system/lib64/libbiometry_fp_api.so
