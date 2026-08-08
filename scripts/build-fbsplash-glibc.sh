#!/bin/bash
# Cross-build hybris-boot's fbsplash.c against the device target's glibc, from inside the
# PLATFORM SDK. This is a DIAGNOSTIC build only -- the shipped fbsplash is the bionic one
# produced by hybris-boot/Android.mk. It exists so the same source can be run on the device
# built two different ways, to tell a bug in fbsplash.c apart from a bug in the toolchain.
#
# Note the /parentroot/ paths: sb2 does not resolve the /opencloud symlink (see CLAUDE.md).
set -e
SRC=/parentroot/opencloud/hadk/hybris/hybris-boot/fbsplash.c
OUT=${1:-/parentroot/opencloud/hadk/out/target/product/karatep/utilities/fbsplash-glibc}
sb2 -t lenovo-karatep-aarch64 gcc -Wall -Wextra -Werror -O2 -static -o "$OUT" "$SRC"
ls -l "$OUT"
