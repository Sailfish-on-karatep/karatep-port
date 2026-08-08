#!/bin/bash
# Whole-file-sign the installer zip. mic emits it unsigned, and the recovery
# refuses an unsigned package.
#
# RUN FROM THE HOST. Output goes to /opencloud/prebuilts/installer/.
#
# See karatep-port/docs/flashing.md.
set -e

source /opencloud/hadk.env

IN=${1:-$ANDROID_ROOT/SailfishOScommunity-release-$RELEASE-$DEVICE/sailfishos-$DEVICE-release-$RELEASE.zip}
OUTDIR=/opencloud/prebuilts/installer
OUT=$OUTDIR/$(basename "${IN%.zip}")-signed.zip

KEYDIR=$ANDROID_ROOT/build/make/target/product/security
JAVA=$ANDROID_ROOT/prebuilts/jdk/jdk11/linux-x86/bin/java
SIGNAPK=$ANDROID_ROOT/out/host/linux-x86/framework/signapk.jar

[ -s "$IN" ] || { echo "error: no zip at $IN" >&2; exit 1; }
[ -f "$SIGNAPK" ] || { echo "error: $SIGNAPK missing -- build it with 'make signapk'" >&2; exit 1; }

mkdir -p "$OUTDIR"
"$JAVA" -Xmx4096m -Djava.library.path="$ANDROID_ROOT/out/host/linux-x86/lib64" \
    -jar "$SIGNAPK" -w \
    "$KEYDIR/testkey.x509.pem" "$KEYDIR/testkey.pk8" \
    "$IN" "$OUT"

# The signature lives in the zip comment; the last two bytes give its length.
tail -c 6 "$OUT" | od -An -tx1 | grep -q 'ff ff' \
    || { echo "error: $OUT has no whole-file signature footer" >&2; exit 1; }

ls -l "$OUT"
