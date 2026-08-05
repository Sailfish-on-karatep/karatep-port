#!/bin/bash
# Sailfish OS Automated Flasher for Lenovo Karatep
# Run this from your host PC while the phone is in fastboot mode.

HOST_IP="192.168.2.14"
RELEASE_DIR="/opencloud/hadk/SailfishOScommunity-release-5.1.0.11-karatep"
RECOVERY_IMG="/opencloud/hadk/out/target/product/karatep/hybris-recovery.img"

echo "====================================="
echo " Sailfish OS Karatep Auto-Flasher"
echo "====================================="

echo "[1/4] Erasing and Formatting Userdata..."
# Formatting from fastboot skips the need for TWRP entirely
fastboot format:ext4 userdata || fastboot erase userdata

echo "[2/4] Live-booting hybris-recovery..."
fastboot boot "$RECOVERY_IMG"

echo "[3/4] Starting local web server..."
cd "$RELEASE_DIR" || exit 1
python3 -m http.server 8000 &
SERVER_PID=$!

echo "[4/4] Waiting for device network (192.168.2.15)..."
while ! ping -c 1 -W 1 192.168.2.15 &> /dev/null; do
    sleep 1
done
sleep 5 # Give the Boat Loader a moment to expose port 23

echo "Injecting payload into Port 23..."
# Since userdata is formatted, the boot will fail to find rootfs and drop to port 23 (Boat Loader debug)
cat << 'EOF' > /tmp/flash_payload.sh
sleep 1
echo 'mount -t ext4 /dev/block/bootdevice/by-name/userdata /data' > /init-ctl/stdin
sleep 1
echo 'rm -rf /data/.stowaways/sailfishos && mkdir -p /data/.stowaways/sailfishos' > /init-ctl/stdin
sleep 1
echo 'curl -f -L http://192.168.2.14:8000/sailfishos-karatep-release-5.1.0.11.tar.bz2 | tar -xj -C /data/.stowaways/sailfishos' > /init-ctl/stdin
# Give it enough time to extract the tarball
sleep 120
echo 'curl -f -L -o /tmp/hybris-boot.img http://192.168.2.14:8000/hybris-boot.img' > /init-ctl/stdin
sleep 2
echo 'dd if=/tmp/hybris-boot.img of=/dev/block/bootdevice/by-name/boot' > /init-ctl/stdin
sleep 1
echo 'reboot' > /init-ctl/stdin
EOF

nc 192.168.2.15 23 < /tmp/flash_payload.sh

kill $SERVER_PID
rm /tmp/flash_payload.sh

echo "====================================="
echo "Flashing completed! Device should reboot."
echo "====================================="
