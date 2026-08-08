#!/bin/bash
# Rebuild geoclue-providers-hybris from the working tree. RUN FROM INSIDE THE
# PLATFORM SDK.
#
# build-geoclue.sh uses --mw=, which routes through buildmw() and does a
# clone-or-pull on the repo. That is wrong while testing a commit that is not
# pushed to the fork yet -- and a pull on a dirty or ahead tree is exactly the
# failure mode CLAUDE.md warns about. --build= builds the directory in place
# and never pulls, the same way bin/build-fpd-rpm.sh does.
set -e
source /opencloud/hadk.env
cd "$ANDROID_ROOT"
rpm/dhd/helpers/build_packages.sh --offline --build=hybris/mw/geoclue-providers-hybris
