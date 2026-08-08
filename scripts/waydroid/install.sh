#!/bin/sh
# Runs ON THE DEVICE over the telnet 2323 debug shell, after the reflash.
# Fetches the staged RPMs from the host over the USB link and restores the
# preserved Waydroid images.
set -e
HOST=http://192.168.2.4:8000

cd /tmp
for f in \
    lxc-libs-6.0.3+git2-1.7.3.jolla.aarch64.rpm \
    lxc-6.0.3+git2-1.7.3.jolla.aarch64.rpm \
    dnsmasq-2.86-1.4.1.bso.aarch64.rpm \
    python3-dbus-1.2.18+git1-1.5.3.jolla.aarch64.rpm \
    gobject-introspection-1.86.0+git1-1.10.4.jolla.aarch64.rpm \
    python3-gobject-3.50.0+git1-1.7.4.jolla.aarch64.rpm \
    python3-gbinder-1.3.1+git7-1.2.1.bso.aarch64.rpm \
    waydroid-sensors-0.2.0+git2+main.20260808105807.8c667de+upstream.1c01bf8-1.aarch64.rpm \
    waydroid-1.6.3+git1+hybris.18.1.20260808105817.e2b788b+upstream.6ce3a09-1.noarch.rpm \
    waydroid-settings-1.6.3+git1+hybris.18.1.20260808105817.e2b788b+upstream.6ce3a09-1.noarch.rpm
do
    echo "fetching $f"
    curl -sfL -o "/tmp/$f" "$HOST/$f"
done

echo "=== installing ==="
rpm -Uvh --replacepkgs /tmp/*.rpm

# Waydroid needs the dnsmasq *binary* -- waydroid-net.sh starts its own instance
# bound to 192.168.240.1 for the container's DHCP/DNS. The RPM also drops in a
# system-wide dnsmasq.service which the systemd preset enables, and that
# instance binds 0.0.0.0:53. Waydroid's then cannot bind:
#
#   dnsmasq: failed to create listening socket for 192.168.240.1: Address already in use
#   Failed to setup waydroid-net.
#
# which aborts `waydroid session start` outright -- and, when it half-succeeds,
# leaves the container with no working DNS. Turn the system service off; the
# binary stays.
echo "=== disabling system dnsmasq (conflicts with waydroid-net) ==="
systemctl disable --now dnsmasq.service 2>/dev/null || true

echo "=== restoring images ==="
# /home/waydroid is created by the waydroid RPM; drop it and rename the
# preserved tree in. Both paths are inside the /data mount, so this is a
# rename, not a copy -- see rca/waydroid-devpts.md.
rm -rf /data/.stowaways/sailfishos/home/waydroid
mv /data/waydroid-keep /data/.stowaways/sailfishos/home/waydroid
chown -R defaultuser:users /home/waydroid 2>/dev/null || true

echo "=== re-enabling overlays ==="
sed -i 's/^mount_overlays = False$/mount_overlays = True/' /home/waydroid/waydroid.cfg
grep mount_overlays /home/waydroid/waydroid.cfg

echo "=== done ==="
ls -l /home/waydroid/images/
