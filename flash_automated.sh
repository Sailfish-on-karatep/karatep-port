#!/bin/bash
# Sailfish OS Automated Flasher for Lenovo Karatep
# Run this from your host PC while the phone is in fastboot mode.

HOST_IP="192.168.2.14"
RELEASE_DIR="/opencloud/hadk/SailfishOScommunity-release-5.1.0.11-karatep"
RECOVERY_IMG="/opencloud/hadk/out/target/product/karatep/hybris-recovery.img"
BOOT_IMG="/opencloud/hadk/SailfishOScommunity-release-5.1.0.11-karatep/hybris-boot.img"

echo "====================================="
echo " Sailfish OS Karatep Auto-Flasher"
echo "====================================="

# FASTBOOT OPERATIONS
echo "[1/5] Flashing Kernel (hybris-boot) safely via fastboot..."
fastboot flash boot "$BOOT_IMG"

echo "[2/5] Erasing and Formatting Userdata..."
fastboot format:ext4 userdata || fastboot erase userdata

echo "[3/5] Live-booting hybris-recovery..."
fastboot boot "$RECOVERY_IMG"

# HOST SERVER
echo "[4/5] Starting local web server..."
cd "$RELEASE_DIR" || exit 1
if ! fuser 8080/tcp > /dev/null 2>&1; then
    python3 -m http.server 8080 &
    SERVER_PID=$!
else
    echo "Port 8080 is already in use, assuming server is running."
fi

# DEVICE CONNECTION
echo "[5/5] Waiting for device network (192.168.2.15)..."
while ! ping -c 1 -W 1 192.168.2.15 &> /dev/null; do
    sleep 1
done
sleep 5 # Give the Boat Loader a moment to expose port 23

echo "Injecting payload into Port 23..."

cat << 'EOF' > /tmp/do_flash.py
import telnetlib
import time
import sys

print("Connecting to device via Telnet on port 23...")
try:
    tn = telnetlib.Telnet("192.168.2.15", 23, timeout=10)
except Exception as e:
    print(f"Failed to connect: {e}")
    sys.exit(1)

def send_cmd(cmd):
    print("Executing: " + cmd)
    full_cmd = f"echo '{cmd}' > /init-ctl/stdin\n"
    tn.write(full_cmd.encode('ascii'))
    time.sleep(2)

send_cmd("mount -t ext4 /dev/block/bootdevice/by-name/userdata /data")
send_cmd("mkdir -p /data/.stowaways/sailfishos")

print("Starting OS extraction... this will take a few minutes.")
print("The device will automatically reboot when extraction finishes!")
# Chaining the reboot command ensures we only reboot AFTER extraction completes.
send_cmd("curl -f -L http://192.168.2.14:8080/sailfishos-karatep-release-5.1.0.11.tar.bz2 | tar -xj -C /data/.stowaways/sailfishos && reboot")

tn.close()
print("Payload successfully delivered. Keep an eye on the phone screen.")
EOF

python3 /tmp/do_flash.py

if [ -n "$SERVER_PID" ]; then
    kill $SERVER_PID
fi
rm -f /tmp/do_flash.py

echo "====================================="
echo "Done!"
echo "====================================="
