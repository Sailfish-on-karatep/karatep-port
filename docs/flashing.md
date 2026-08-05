# Sailfish OS Flashing Guide: Lenovo K6 Note (`karatep`)

> **Warning**
>
> Flashing low-level partitions can permanently brick your device if performed incorrectly. This guide assumes you are using the official Sailfish OS build generated for **karatep**.

## Prerequisites

### Android Base

This Sailfish OS port is built against the **LineageOS 18.1 (Android 11)** hybris base.

Before installing Sailfish OS, ensure that:

* LineageOS 18.1 is installed.
* The device boots successfully.
* The bootloader is unlocked.

---

## Generated Files

Locate the generated files on your build machine.

* **Root Filesystem**

  ```
  /opencloud/hadk/SailfishOScommunity-release-5.1.0.11-karatep/sailfishos-karatep-release-5.1.0.11.tar.bz2
  ```

* **Boot Image**

  ```
  /opencloud/hadk/SailfishOScommunity-release-5.1.0.11-karatep/hybris-boot.img
  ```

* **Recovery Image**

  ```
  /opencloud/hadk/out/target/product/karatep/hybris-recovery.img
  ```

It is convenient (though not required) to copy `hybris-recovery.img` into the release directory so all required files are located together.

---

# 1. Boot the Sailfish Recovery

Do **not** flash the recovery.

Instead, boot it temporarily:

```bash
fastboot boot /opencloud/hadk/out/target/product/karatep/hybris-recovery.img
```

The device will remain on the Lenovo splash screen.

This is expected.

---

# 2. Start the HTTP Server

On the host PC:

```bash
cd /opencloud/hadk/SailfishOScommunity-release-5.1.0.11-karatep

python3 -m http.server 8000
```

Determine the host IP address on the USB network interface.

Example:

```
192.168.2.20
```

Replace this address in all commands below if your address differs.

---

# 3. Connect to the Recovery Shell

```bash
telnet 192.168.2.15 23
```

You should see the Mer Boat Loader shell.

---

# 4. Verify Userdata is Mounted

Run:

```sh
mount | grep mmcblk0p54
```

You should see:

```
/dev/mmcblk0p54 on /data
```

Depending on the recovery state, additional mounts such as `/target` and `/target/data` may also be present.

---

# Important

**Do not run**

```sh
echo "umount_stowaways" >/init-ctl/stdin
```

when using the network installation method.

That command is intended for the USB mass-storage export workflow and unmounts the userdata filesystem, preventing installation.

---

# 5. Remove Any Previous Installation

If this is a **fresh recovery boot**, remove any previous installation:

```sh
rm -rf /data/.stowaways/sailfishos
mkdir -p /data/.stowaways/sailfishos
```

If `rm` reports:

```
Device or resource busy
```

the recovery has already attempted to boot the installed system.

Do **not** continue.

Instead:

1. Reboot back into Fastboot.
2. Boot `hybris-recovery.img` again.
3. Repeat the installation from the beginning.

---

# 6. Extract the Root Filesystem

The recovery environment provides `wget` rather than `curl`.

Extract the root filesystem directly:

```sh
wget -O - \
http://192.168.2.20:8000/sailfishos-karatep-release-5.1.0.11.tar.bz2 \
| tar -xjv -C /data/.stowaways/sailfishos
```

---

# 7. Verify the Extraction

Before flashing anything, verify that the root filesystem extracted correctly.

```sh
ls /data/.stowaways/sailfishos
```

A successful extraction should contain directories similar to:

```
bin
boot
dev
etc
lib
mnt
proc
run
sbin
sys
usr
var
```

If the directory only contains:

```
data
```

or extraction failed, **stop**.

Do **not** flash the boot image until the extraction succeeds.

---

# 8. Download the Boot Image

```sh
wget -O /tmp/hybris-boot.img \
http://192.168.2.20:8000/hybris-boot.img
```

---

# 9. Flash the Boot Partition

This recovery does **not** expose Android's `by-name` partition symlinks.

Flash using the raw partition node:

```sh
dd if=/tmp/hybris-boot.img of=/dev/mmcblk0p34
sync
```

---

# 10. Reboot

```sh
reboot
```

---

# Troubleshooting

## `curl: not found`

The recovery includes `wget` but not `curl`.

Use:

```sh
wget -O - URL | tar -xj ...
```

---

## `mount: ... by-name/userdata: No such file or directory`

Ignore this error.

`/data` is already mounted automatically by the recovery.

Do not mount it manually.

---

## `dd: can't open '/dev/block/bootdevice/by-name/boot'`

This recovery does not create Android `by-name` partition symlinks.

Use:

```text
/dev/mmcblk0p34
```

instead.

---

## `Device or resource busy`

This indicates the recovery has already attempted to transition into the installed system and parts of the installation remain mounted.

Reboot into a **fresh** `hybris-recovery.img` session before attempting another installation.

---

## `failed to boot init in real rootfs`

This indicates the installed root filesystem is incomplete or invalid.

Verify that `/data/.stowaways/sailfishos` contains the expected root filesystem (`bin`, `etc`, `usr`, `var`, etc.) before flashing the boot image.
