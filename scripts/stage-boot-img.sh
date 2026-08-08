#!/bin/bash
# Copy the freshly built hybris-boot.img into the mic output directory.
#
# RUN FROM INSIDE THE PLATFORM SDK:  /opencloud/bin/sfossdk /opencloud/bin/stage-boot-img.sh
#
# mic creates the release directory as root and wipes it each run, so the boot
# image the recovery downloads over HTTP has to be put back afterwards. The host
# user cannot write there and host sudo needs a terminal; sudo is passwordless
# inside the Platform SDK, so the copy belongs here.
set -e
source /opencloud/hadk.env
SRC="$ANDROID_ROOT/out/target/product/$DEVICE/hybris-boot.img"
DST="$ANDROID_ROOT/SailfishOScommunity-release-$RELEASE-$DEVICE/hybris-boot.img"
sudo cp -v "$SRC" "$DST"
sudo chmod 0644 "$DST"
md5sum "$SRC" "$DST"
