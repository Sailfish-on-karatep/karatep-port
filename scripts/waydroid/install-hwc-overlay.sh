#!/bin/sh
# Install the rebuilt Waydroid hwcomposer through Waydroid's overlay.
#
# Runs ON THE DEVICE. Fetches hwcomposer.waydroid.so from the host over the USB
# link and drops it into /var/lib/waydroid/overlay/vendor/lib64/hw/, which
# overlayfs stacks on top of the vendor image at session start. Nothing in the
# image is modified: `rm` the file and the stock library is back.
#
# The rebuilt library takes the wl_shell path instead of xdg-shell. See
# karatep-port/docs/rca/waydroid-touch-xdg-shell.md.
#
# Requires mount_overlays = True in /home/waydroid/waydroid.cfg (it is).
set -e

HOST="${HOST:-http://192.168.2.4:8000}"
DEST=/var/lib/waydroid/overlay/vendor/lib64/hw
SO=hwcomposer.waydroid.so

echo "=== stopping any running session ==="
# The overlay is assembled at session start, so the container must be down for
# the new library to be picked up.
pkill -f "waydroid session" 2>/dev/null || true
systemctl stop waydroid-container 2>/dev/null || true
sleep 3

# Drop any previously merged copy from the writable upper layer first. This
# port's overlayfs is a 3.18 single-lowerdir kernel, so patch 0003 emulates the
# stacked lower layers by copying `overlay` into `overlay_rw` at session start
# -- and it deliberately skips files that already exist there. A copy left from
# an earlier install therefore shadows the new library forever, and the change
# silently does nothing. See rca/waydroid-devpts.md for the overlay limitation.
echo "=== clearing any previously merged copy from overlay_rw ==="
rm -f "/var/lib/waydroid/overlay_rw/vendor/lib64/hw/$SO"

echo "=== installing $SO into the overlay ==="
mkdir -p "$DEST"
curl -sfL -o "$DEST/$SO.new" "$HOST/$SO"
[ -s "$DEST/$SO.new" ] || { echo "download failed or empty"; exit 1; }
mv "$DEST/$SO.new" "$DEST/$SO"
chmod 644 "$DEST/$SO"
ls -l "$DEST/$SO"

echo "=== sanity: does it reference wl_shell and the new property? ==="
for s in wl_shell xdg_wm_base persist.waydroid.prefer_xdg_shell; do
    printf "  %-38s %s\n" "$s" "$(grep -ac "$s" "$DEST/$SO" || true)"
done

echo
echo "Now start the container and a session, then confirm which shell was used:"
echo "  systemctl start waydroid-container"
echo "  waydroid session start          # as defaultuser, WAYLAND_DISPLAY=../../display/wayland-0"
echo "  waydroid shell -- logcat -d | grep -i 'Creating window with'"
echo
echo "Expected: 'Creating window with wl_shell'. If it says xdg-shell, the"
echo "overlay did not take -- check mount_overlays and that the session was"
echo "fully stopped before installing."
