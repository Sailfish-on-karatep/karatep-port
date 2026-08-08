#!/bin/bash
# Build the Sailfish-side fingerprint packages for sailfish-fpd-community.
#
# RUN FROM INSIDE THE PLATFORM SDK:
#     /opencloud/bin/sfossdk /opencloud/bin/build-fpd-rpm.sh
#
# Prerequisite: the Android side must already be built and staged, i.e.
#     /opencloud/bin/habuild /parentroot/parentroot/opencloud/bin/build-fpd.sh
#     hybris/mw/sailfish-fpd-community/rpm/copy-hal.sh
# which leaves libbiometry_fp_api.so under
# hybris/mw/sailfish-fpd-community/droid-biometry-fp-0.0.0/.
#
# Two packages, in this order -- the daemon Requires: droid-biometry-fp >= 1.0.0,
# so building the daemon first fails dependency resolution.
#
# NOTE: this script must live under /opencloud, not /tmp. The Platform SDK is a
# chroot with its own /tmp, so a script written to the host /tmp is not visible
# inside it.
set -e

source /opencloud/hadk.env
cd "$ANDROID_ROOT"

# Refresh the fork before building.
#
# hybris/mw is NOT repo-managed, so `repo sync` never touches it. The middleware
# that build_packages.sh -m knows about is refreshed for free, because buildmw()
# in rpm/dhd/helpers/util.sh does a git clone-or-pull on each repo it builds --
# but sailfish-fpd-community is not in that built-in list, and this script uses
# --build= (which builds a directory in place) rather than --mw= (which pulls).
# Nothing was pulling it, so the RPM silently drifted behind the fork: it was
# once built from a commit that had since been amended out of the branch.
#
# Not fatal on failure -- an offline rebuild of the current checkout is still
# useful, and the commit id is echoed so the RPM version can be traced.
FPD=hybris/mw/sailfish-fpd-community
echo "=== 0/2: refreshing $FPD ==="
if [ -d "$FPD/.git" ]; then
    # origin must be HTTPS: ssh inside the SDK chroot rejects ~/.ssh/config
    # with "Bad owner or permissions", so an SSH remote can never pull here.
    git -C "$FPD" pull --ff-only || echo "WARNING: pull failed, building the current checkout"
    echo "sailfish-fpd-community at $(git -C "$FPD" rev-parse --short HEAD) \
on $(git -C "$FPD" branch --show-current)"
else
    echo "WARNING: $FPD is not a git clone, skipping refresh"
fi

echo "=== 1/2: droid-biometry-fp (Android HAL glue) ==="
if ls "$ANDROID_ROOT/droid-local-repo/$DEVICE"/droid-biometry-fp-*.rpm >/dev/null 2>&1; then
    echo "already built, skipping (delete the RPM to force a rebuild)"
else
    rpm/dhd/helpers/build_packages.sh \
        --build=hybris/mw/sailfish-fpd-community \
        --spec=rpm/droid-biometry-fp.spec \
        --do-not-install
fi

# --spec is REQUIRED here even though upstream's README omits it. Without it,
# build_packages.sh builds every spec in rpm/, which includes
# droid-fake-crypt.spec. That spec unconditionally copies
# out/target/product/*/system/bin/fake_crypt and dies with
#     cp: cannot stat '.../system/bin/fake_crypt': No such file or directory
# on any device that does not need it. karatep is keymaster 3.0 (see
# device/lenovo/karate-common/manifest.xml), and fake_crypt is only for
# keymaster 4, so it is intentionally not built.
echo "=== 2/2: sailfish-fpd-community (the daemon) ==="
rpm/dhd/helpers/build_packages.sh \
    --build=hybris/mw/sailfish-fpd-community \
    --spec=rpm/sailfish-fpd-community.spec

echo
echo "=== resulting RPMs ==="
ls -l "$ANDROID_ROOT/droid-local-repo/$DEVICE"/{droid-biometry-fp,sailfish-fpd-community}*.rpm 2>/dev/null || \
    echo "(none found -- check the log above)"
