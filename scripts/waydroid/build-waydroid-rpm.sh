#!/bin/bash
# Build the Sailfish-side Waydroid packages.
#
# RUN FROM INSIDE THE PLATFORM SDK:
#     /opencloud/bin/sfossdk /opencloud/bin/build-waydroid-rpm.sh
#
# Two repos, both plain clones under hybris/mw (which is NOT repo-managed):
#
#   hybris/mw/waydroid-sensors  sailfishos-open/waydroid-sensors -- a sensorfw
#                               to gbinder bridge, /usr/bin/waydroid-sensord
#   hybris/mw/waydroid          Sailfish-on-karatep/waydroid (branch hybris-18.1,
#                               upstream = sailfishos-open) -- SFOS packaging over
#                               upstream waydroid, carried as a git submodule.
#                               Ours because sailfishos-open is still pinned to
#                               1.5.4; the fork tracks 1.6.3 and adds the
#                               single-lowerdir overlay patch for this kernel.
#
# mb2 takes the RPM version from `git describe`, NOT from Version: in the spec.
# Bumping the spec alone produced waydroid-1.5.4+git1+hybris.18.1..., which rpm
# would then refuse as an upgrade over ...+main... . Tag the fork (1.6.3+git1)
# whenever the submodule moves.
#
# Order matters: waydroid has "Requires: waydroid-sensors", and
# build_packages.sh installs what it builds into the target, so building
# waydroid first fails dependency resolution.
#
# Neither repo is in buildmw()'s built-in list in rpm/dhd/helpers/util.sh, so
# `build_packages.sh -m` never refreshes them and --build= builds a directory in
# place without pulling. This script therefore pulls both explicitly, exactly as
# build-fpd-rpm.sh does, otherwise the RPMs silently drift behind the branch.
#
# The submodule is the part that actually bites: a plain `git pull` moves the
# superproject's recorded submodule commit without checking the submodule out,
# leaving upstream/ at the old revision -- so the build would quietly produce the
# previous waydroid version. Hence --recurse-submodules on the pull, plus an
# explicit `submodule update --init`.
#
# NOTE: this script must live under /opencloud, not /tmp. The Platform SDK is a
# chroot with its own /tmp, so a script written to the host /tmp is not visible
# inside it.
set -e

source /opencloud/hadk.env
cd "$ANDROID_ROOT"

refresh() {
    local dir="hybris/mw/$1"
    echo "=== refreshing $dir ==="
    if [ -d "$dir/.git" ]; then
        # origin must be HTTPS: ssh inside the SDK chroot rejects ~/.ssh/config
        # with "Bad owner or permissions", so an SSH remote can never pull here.
        git -C "$dir" pull --ff-only --recurse-submodules ||
            echo "WARNING: pull failed, building the current checkout"
        git -C "$dir" submodule update --init --recursive ||
            echo "WARNING: submodule update failed"
        echo "$1 at $(git -C "$dir" rev-parse --short HEAD) \
on $(git -C "$dir" branch --show-current), \
upstream at $(git -C "$dir/upstream" rev-parse --short HEAD 2>/dev/null || echo '?')"
    else
        echo "WARNING: $dir is not a git clone, skipping refresh"
    fi
}

# Apply the packaging repo's own rpm/*.patch into the submodule working tree.
#
# Both specs do their patching with `%autosetup -p1 -n %{name}-%{version}/upstream`,
# which only runs during %prep -- and mb2 never executes %prep. It builds the
# working tree in place (the build log goes straight to `Executing(%build)` with
# cwd already inside upstream/), so nothing unpacks a tarball and nothing applies
# a patch. OBS does not hit this because tar_git hands rpmbuild a real tarball
# and %prep runs normally.
#
# Without this, waydroid-sensors fails to compile: upstream's
# sensorfw-core/utils/dbus_connection_handle.cpp uses uint32_t without including
# <cstdint>, which is exactly what 001-cstdint.patch fixes.
#
# `git checkout -- .` first makes this idempotent -- the tree is reset to the
# submodule's pinned revision, so a rebuild never stacks a patch on itself.
# Filename order is apply order, matching %autosetup's PatchN order.
apply_patches() {
    local dir="hybris/mw/$1"
    echo "=== applying $1 packaging patches to upstream/ ==="
    git -C "$dir/upstream" checkout -- .
    for p in "$dir"/rpm/*.patch; do
        [ -e "$p" ] || continue
        echo "  $(basename "$p")"
        git -C "$dir/upstream" apply -p1 "$PWD/$p"
    done
}

refresh waydroid-sensors
apply_patches waydroid-sensors
refresh waydroid
apply_patches waydroid

echo
echo "=== 1/2: waydroid-sensors ==="
rpm/dhd/helpers/build_packages.sh \
    --build=hybris/mw/waydroid-sensors \
    --spec=rpm/waydroid-sensors.spec

echo
echo "=== 2/2: waydroid ==="
rpm/dhd/helpers/build_packages.sh \
    --build=hybris/mw/waydroid \
    --spec=rpm/waydroid.spec

echo
echo "=== resulting RPMs ==="
ls -l "$ANDROID_ROOT/droid-local-repo/$DEVICE"/waydroid*.rpm 2>/dev/null ||
    echo "(none found -- check the log above)"
