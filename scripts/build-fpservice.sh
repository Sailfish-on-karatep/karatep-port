#!/bin/bash
# Build only the fingerprint HIDL service. RUN FROM INSIDE HABUILD.
# Source the env before breakfast, as the HADK does: some targets read PORT_ARCH.
source /parentroot/parentroot/opencloud/hadk.env || exit 1
cd "$ANDROID_ROOT" || exit 1
source build/envsetup.sh
export USE_CCACHE=1
breakfast karatep || exit 1
make -j$(nproc --all) android.hardware.biometrics.fingerprint@2.0-service
