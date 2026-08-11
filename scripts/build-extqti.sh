#!/bin/bash
# Build ofono-binder-plugin-ext-qti from our fork.
#
# RUN FROM INSIDE THE PLATFORM SDK:
#     /opencloud/bin/sfossdk /opencloud/bin/build-extqti.sh
#
# This carries the VoLTE work: setServiceStatus (IImsRadio@1.0 transaction 9),
# which is what actually enables IMS on this modem -- requestRegistrationChange
# is wired to QMI IMSS "set IMS test mode" here and is refused. See
# karatep-port/docs/rca/volte-registration-change-is-test-mode.md.
#
# hybris/mw is NOT repo-managed, and ext-qti is not in buildmw()'s built-in
# list, so nothing refreshes this clone for us -- pull it explicitly, exactly as
# build-fpd-rpm.sh does, or the RPM silently drifts behind the branch.
#
# NOTE: this script must live under /opencloud, not /tmp. The Platform SDK is a
# chroot with its own /tmp, so a script written to the host /tmp is invisible
# inside it.
set -e

source /opencloud/hadk.env
cd "$ANDROID_ROOT"

MW=hybris/mw/ofono-binder-plugin-ext-qti
echo "=== refreshing $MW ==="
if [ -d "$MW/.git" ]; then
    # origin must be HTTPS: ssh inside the SDK chroot rejects ~/.ssh/config
    # with "Bad owner or permissions", so an SSH remote can never pull here.
    git -C "$MW" pull --ff-only || echo "WARNING: pull failed, building the current checkout"
    echo "ext-qti at $(git -C "$MW" rev-parse --short HEAD) \
on $(git -C "$MW" branch --show-current)"
else
    echo "WARNING: $MW is not a git clone, skipping refresh"
fi

echo "=== building ==="
rpm/dhd/helpers/build_packages.sh \
    --build="$MW" \
    --spec=rpm/ofono-binder-plugin-ext-qti.spec

echo
echo "=== resulting RPMs ==="
ls -l "$ANDROID_ROOT/droid-local-repo/$DEVICE"/ofono-binder-plugin-ext-qti*.rpm 2>/dev/null || \
    echo "(none found -- check the log above)"
