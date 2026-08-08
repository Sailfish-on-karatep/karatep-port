#!/bin/bash
# Build the LineageOS recovery. RUN FROM INSIDE THE HABUILD SDK.
#
# Driven by bin/build-los-recovery.sh, which is what parks the tree at the
# unpatched revision -- do not call this directly.
#
# OUT_DIR must be absolute: vendor/lineage/config/BoardConfigKernel.mk only
# prefixes the kernel's O= with $(BUILD_TOP) when OUT_DIR is literally "out".
set -e
source /parentroot/parentroot/opencloud/hadk.env
cd "$ANDROID_ROOT"
export OUT_DIR="$ANDROID_ROOT/out-recovery"
source build/envsetup.sh
export USE_CCACHE=1
breakfast karatep
# updater is built here, not in out/, because it is statically linked and the
# hybris-patched bionic makes it crash under the recovery.
make -j"$(nproc --all)" recoveryimage updater
ls -l "$OUT_DIR/target/product/karatep/recovery.img" \
      "$OUT_DIR/target/product/karatep/system/bin/updater"
