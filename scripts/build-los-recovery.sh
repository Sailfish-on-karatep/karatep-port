#!/bin/bash
# Build the LineageOS recovery and the updater. RUN FROM THE HOST.
#
# Parks every hybris-patched project at the manifest revision, builds, archives,
# and restores. Both artefacts must come from an unpatched tree; see
# karatep-port/docs/rca/broken-update-binary.md and docs/los-recovery.md.
set -e

source /opencloud/hadk.env
cd "$ANDROID_ROOT"

ARCHIVE=/opencloud/prebuilts/recovery
STATE=$ANDROID_ROOT/.los-recovery-heads

# Every project either patch series touches. Both apply per directory, and the
# directory name is the project path.
PROJECTS=$( { cd "$ANDROID_ROOT/hybris-patches" && find . -name '*.patch' -exec dirname {} \;
              cd "$ANDROID_ROOT/karatep-patches" && find . -name '*.patch' -exec dirname {} \;
            } | sed 's|^\./||' | sort -u)

# repo's manifest ref: the exact revision the manifest pins, resolvable offline.
MREF=$(git -C system/core for-each-ref --format='%(refname:short)' refs/remotes/m/ | head -1)
[ -n "$MREF" ] || { echo "error: no refs/remotes/m/ -- is this a repo checkout?" >&2; exit 1; }

# Restores by recorded sha, so it is correct even if parking died halfway.
restore() {
    local rc=$? p sha failed=0
    [ -s "$STATE" ] || return $rc
    echo "=== restoring patched heads ==="
    while read -r p sha; do
        if [ "$(git -C "$ANDROID_ROOT/$p" rev-parse HEAD)" = "$sha" ]; then
            continue
        fi
        if git -C "$ANDROID_ROOT/$p" checkout --quiet --detach "$sha"; then
            echo "  $p -> $(git -C "$ANDROID_ROOT/$p" rev-parse --short HEAD)"
        else
            echo "  ERROR: $p could not be restored to $sha" >&2
            failed=1
        fi
    done < "$STATE"
    if [ $failed -eq 0 ]; then
        rm -f "$STATE"
    else
        echo "error: tree left partly parked; heads are in $STATE" >&2
        [ $rc -eq 0 ] && rc=1
    fi
    return $rc
}

# Pre-flight: check everything before touching anything.
echo "=== projects to park at $MREF ==="
for p in $PROJECTS; do
    [ -d "$p/.git" ] || { echo "error: $p is not a git checkout" >&2; exit 1; }
    git -C "$p" rev-parse --verify --quiet "$MREF" >/dev/null \
        || { echo "error: $p has no $MREF" >&2; exit 1; }
    if [ -n "$(git -C "$p" status --porcelain --untracked-files=no)" ]; then
        echo "error: $p has uncommitted changes; refusing to touch it" >&2
        exit 1
    fi
    printf '  %-24s %s -> %s\n' "$p" \
        "$(git -C "$p" rev-parse --short HEAD)" "$(git -C "$p" rev-parse --short "$MREF")"
done

: > "$STATE"
for p in $PROJECTS; do
    printf '%s %s\n' "$p" "$(git -C "$p" rev-parse HEAD)" >> "$STATE"
done

# From here on, no exit path may leave the tree parked.
trap restore EXIT

for p in $PROJECTS; do
    git -C "$p" checkout --quiet --detach "$MREF"
done

echo "=== building recoveryimage (OUT_DIR=out-recovery) ==="
/opencloud/bin/habuild /parentroot/parentroot/opencloud/bin/build-recoveryimage.sh

IMG=$ANDROID_ROOT/out-recovery/target/product/karatep/recovery.img
[ -f "$IMG" ] || { echo "error: no recovery.img produced" >&2; exit 1; }

UPDATER=$ANDROID_ROOT/out-recovery/target/product/karatep/system/bin/updater
[ -f "$UPDATER" ] || { echo "error: no updater produced" >&2; exit 1; }

mkdir -p "$ARCHIVE"
STAMP=$(date +%Y%m%d)-$(git -C bootable/recovery rev-parse --short HEAD)
cp "$IMG" "$ARCHIVE/recovery-$STAMP.img"
cp "$IMG" "$ARCHIVE/recovery.img"
cp "$UPDATER" "$ARCHIVE/update-binary"
echo "=== archived $ARCHIVE/recovery-$STAMP.img ==="
echo "=== archived $ARCHIVE/update-binary ==="

# The patched heads go back on via the EXIT trap.
echo
echo "Live-boot it before flashing anything:"
echo "    fastboot boot $ARCHIVE/recovery.img"
echo "Installed copy lives at /dev/mmcblk0p35."
