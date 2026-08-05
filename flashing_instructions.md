# Sailfish OS Flashing Guide: Lenovo Karatep (aarch64)

**WARNING:** Flashing operating systems manipulates low-level partitions. One incorrect step or flashing an incompatible base can hard-brick your device. Follow these instructions precisely.

## Prerequisite: The Android Base
Sailfish OS ports are essentially a "Hardware Abstraction Layer" (HAL) placed on top of an existing Android system's vendor binaries and firmware blobs. 
* **Base Required:** Your `karatep` build was compiled against the **LineageOS 18.1 (Android 11)** hybris base.
* **Critical Check:** Your device **must** be running a fully functional installation of LineageOS 18.1 before proceeding.

## 1. Wipe User Data (Required)
Sailfish OS needs a clean `/data` partition to unpack its rootfs.
1. Reboot into your custom recovery (TWRP/Lineage).
2. Perform a standard **Factory Reset** (Wipes Data, Cache, and Dalvik).
   * *Note: Do NOT wipe the `System` or `Vendor` partitions. Sailfish OS needs the Android vendor partition intact!*

## 2. Boot into Sailfish Recovery (Live Boot)
Since TWRP is often 32-bit and cannot process the 64-bit Sailfish installer scripts (Error 11) or lacks proper tar capabilities (Error 1), we will use the native Sailfish OS `hybris-recovery.img`. To protect your existing TWRP installation, we will live-boot this image into RAM without flashing it.

1. Locate your generated files on your host PC:
   - **Rootfs Tarball**: `/opencloud/hadk/SailfishOScommunity-release-5.1.0.11-karatep/sailfishos-karatep-release-5.1.0.11.tar.bz2`
   - **Boot Image**: `/opencloud/hadk/SailfishOScommunity-release-5.1.0.11-karatep/hybris-boot.img`
   - **Recovery Image**: `/opencloud/hadk/out/target/product/karatep/hybris-recovery.img`
   
   *Tip: Copy `hybris-recovery.img` into the `/opencloud/hadk/SailfishOScommunity-release-5.1.0.11-karatep/` folder so everything is in one place.*

2. Put the phone into Fastboot/Bootloader mode.
3. Live-boot the Sailfish OS recovery image into RAM:
   ```bash
   fastboot boot /opencloud/hadk/out/target/product/karatep/hybris-recovery.img
   ```
4. The device will boot. Since it has no touch UI, it will appear stuck on the Lenovo splash screen, but it will automatically expose an emergency network shell over USB.

## 3. Flash via Network Injection (Port 23)
The Sailfish emergency shell exposes a USB network interface. We will serve the root filesystem from your host PC and command the device to download and extract it.

1. **Start the Web Server:**
   On your host PC, navigate to the folder containing the tarball and boot image, and start a Python HTTP server:
   ```bash
   cd /opencloud/hadk/SailfishOScommunity-release-5.1.0.11-karatep
   python3 -m http.server 8000
   ```
   *(Note your host PC's IP address on the new USB interface, usually `192.168.2.14` or similar).*

2. **Connect via Telnet:**
   On your host PC, connect to the device's pre-switch_root debug shell on port **23**:
   ```bash
   telnet 192.168.2.15 23
   ```
   *You should see "Welcome to the Mer/SailfishOS Boat loader debug init system."*

3. **Inject Flashing Commands:**
   In this environment, you inject commands directly into PID 1 using `echo "..." > /init-ctl/stdin`.
   
   *Start the log watcher to see command output:*
   ```bash
   tail -f /init.log &
   ```
   
   *Mount the userdata partition and prepare the directory:*
   ```bash
   echo "mount -t ext4 /dev/block/bootdevice/by-name/userdata /data" > /init-ctl/stdin
   echo "rm -rf /data/.stowaways/sailfishos && mkdir -p /data/.stowaways/sailfishos" > /init-ctl/stdin
   ```
   
   *Stream and extract the OS directly (replace IP with your host IP):*
   ```bash
   echo "curl -f -L http://192.168.2.14:8000/sailfishos-karatep-release-5.1.0.11.tar.bz2 | tar -xj -C /data/.stowaways/sailfishos" > /init-ctl/stdin
   ```
   *(Wait for the file extraction to complete in the `init.log` output).*

   *Flash the Sailfish OS kernel permanently to the boot partition:*
   ```bash
   echo "curl -f -L -o /tmp/hybris-boot.img http://192.168.2.14:8000/hybris-boot.img" > /init-ctl/stdin
   echo "dd if=/tmp/hybris-boot.img of=/dev/block/bootdevice/by-name/boot" > /init-ctl/stdin
   ```

4. **Reboot:**
   ```bash
   echo "reboot" > /init-ctl/stdin
   ```

## 4. Boot & Initial Verification
1. The device will reboot and show the Lenovo boot logo, followed by the Sailfish OS logo. 
2. **First Boot Patience:** The first boot takes significantly longer (up to 5-10 minutes) as systemd services initialize and the filesystem expands.

### Telnet / SSH Debugging (If it bootloops)
If the device doesn't reach the UI, it will expose an emergency USB network interface (RNDIS).
1. Connect the USB cable.
2. You can connect via `telnet 192.168.2.15 2323` or `ssh nemo@192.168.2.15` to access the journal logs (`journalctl -xe`) and debug why the UI (`lipstick`) or hardware services (`vndservicemanager`) are failing.
