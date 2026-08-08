#!/bin/bash
# Rebuild only the two hybris-boot images (boot + recovery) -- run from inside HABUILD.
# Used when the change is confined to hybris/hybris-boot (init-script, initramfs
# contents, fbsplash) and a full `make hybris-hal` is not needed.
cd /parentroot/parentroot/opencloud/hadk || exit 1
source build/envsetup.sh >/dev/null || exit 1
export USE_CCACHE=1
breakfast karatep >/dev/null 2>&1 || { echo "breakfast failed"; exit 1; }
echo "=== make fbsplash hybris-boot hybris-recovery ==="
make -j"$(nproc --all)" fbsplash hybris-boot hybris-recovery
