#!/bin/bash
# Rebuild the hybris geoclue provider. RUN FROM INSIDE THE PLATFORM SDK.
set -e
source /opencloud/hadk.env
cd "$ANDROID_ROOT"
rpm/dhd/helpers/build_packages.sh --offline --mw=geoclue-providers-hybris
