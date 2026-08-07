# Sailfish OS Flashing Guide: Lenovo K6 Note (`karatep`)

> **Warning**
>
> Flashing low-level partitions can permanently brick your device if performed incorrectly. This guide assumes you are using the official Sailfish OS build generated for **karatep**.

> **Automated alternative**
>
> [`scripts/flash.sh`](../scripts/flash.sh) performs every step below: it live-boots the
> recovery, finds the built image, works out the host address on the USB link with
> `ip route get` rather than assuming one, serves the rootfs, drives the recovery shell, and
> refuses to write the boot partition unless the extracted rootfs verifies.
>
> ```sh
> scripts/flash.sh                       # discovers the newest build under $ANDROID_ROOT
> scripts/flash.sh --release-dir DIR     # or point it at a specific one
> ```
>
> Read this document first anyway — it explains what each step does and how to recover when
> one of them fails.

## Two ways to install

| | |
|---|---|
| **Flashable zip, from the LineageOS recovery** | The standard Android mechanism. Requires the recovery from [`los-recovery.md`](los-recovery.md) and a **signed** zip. Described immediately below. |
| **Manual install, over the recovery shell** | The HADK's own *Manual Installation and Maintenance* procedure: extract the rootfs tarball to `/data/.stowaways/sailfishos` and `dd` the boot image. Works from either recovery. Described from [§1](#1-boot-the-sailfish-recovery) onward, and automated by [`scripts/flash.sh`](../scripts/flash.sh). |

TWRP is not usable on this device: it is 32-bit and fails the Sailfish
installer with Error 11 / Error 1.

---

## Installing the flashable zip

### Sign it first

`mic` emits the zip unsigned, and the recovery refuses an unsigned package:

```
Footer is wrong
Signature verification failed
Installation aborted.       (error 21 = kZipVerificationFailure)
```

This is a recurring porter problem — the same message appears in the
`#sailfishos-porters` archive in 2015, 2016, 2022 and 2025 — because TWRP has
zip signature verification **off** by default and hybris zips were never
signed. The LineageOS recovery always verifies.

```sh
bin/sign-installer-zip.sh
```

That whole-file signs with `build/make/target/product/security/testkey`, which
is one of the two certificates the recovery already trusts (the recovery's
`/system/etc/security/otacerts.zip` holds `testkey.x509.pem` and
`lineage.x509.pem`). The signed zip lands in `/opencloud/prebuilts/installer/`.
On success the device logs:

```
I:2 key(s) loaded from /system/etc/security/otacerts.zip
Verifying update package...
I:whole-file signature verified against RSA key 0
Update package verification took 26.1 s (result 0).
```

### Flash it

```sh
fastboot boot /opencloud/prebuilts/recovery/recovery.img
```

Then either pick *Apply update* → *Apply from ADB* on screen, or arm it from the
host — see [`los-recovery.md`](los-recovery.md#entering-sideload-mode-without-the-screen):

```sh
adb shell 'mount /cache; mkdir -p /cache/recovery; echo "--sideload" > /cache/recovery/command'
adb shell 'pkill -f /system/bin/recovery'
adb sideload /opencloud/prebuilts/installer/sailfishos-karatep-release-<version>-signed.zip
```

The zip mounts `/data`, copies the rootfs tarball to
`/data/sailfishos-rootfs.tar.bz2`, extracts it to `/data/.stowaways/sailfishos`
via `updater-unpack.sh`, then writes `hybris-boot.img` to
`/dev/block/bootdevice/by-name/boot`.

### If it fails

| Message | Cause |
|---|---|
| `Footer is wrong` / `Signature verification failed` | The zip is unsigned. Run `bin/sign-installer-zip.sh`. |
| `Failed to extract filesystem!` | The recovery has no `bzip2` — see [`los-recovery.md`](los-recovery.md#bzip2). |
| `killed by signal 11` right after `Installing update...` | The zip's `update-binary` was built from the hybris-patched tree — see [`rca/broken-update-binary.md`](rca/broken-update-binary.md). |
| `Failed to start fuse` | A stale `/sideload` mount from a killed sideload: `adb shell umount -l /sideload`. |

---

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

# 11. Install the fixed fingerprint HAL service

**Required for working fingerprints.** Without it the sensor appears to work but
misbehaves badly: enrolled fingerprints are not listed, enrolling again fails
with "already enrolled", and **a fingerprint deleted in Settings still unlocks
the device**.

This is the one part of the port an image build cannot deliver. The service
lives on the LineageOS **vendor** partition, which Sailfish never writes, so it
has to be installed by hand — once. It then survives Sailfish reimages, and is
only lost if you reflash the vendor partition.

Why it is needed, briefly: FPC's vendor library returns the template *count*
where `fingerprint.h` specifies 0, and the stock HIDL adapter treats any
non-zero return as an error — so it never hands the daemon a single template id.
Full analysis in
[`porting-notes.md`](porting-notes.md#root-caused-and-fixed-the-hidl-adapter-read-a-count-as-an-error-code).

Build it (HABUILD SDK), from a tree with `local_manifests.xml` applied, which
repins `hardware/lineage/interfaces` at the karatep fork carrying the fix:

```sh
make android.hardware.biometrics.fingerprint@2.0-service
# out/target/product/karatep/vendor/bin/hw/android.hardware.biometrics.fingerprint@2.0-service
```

Copy it to the booted device (over the USB network), then **as root on the
device**:

```sh
B=/vendor/bin/hw/android.hardware.biometrics.fingerprint@2.0-service

mount -o remount,rw /vendor
cp -a "$B" "$B.orig"                    # keep the original beside it
mv  "$B" "$B.busy"                      # a running binary cannot be overwritten
cp  /path/to/new-service "$B"           # ("Text file busy"); ctl.stop is not reliable
chmod 755 "$B"
chown root:shell "$B"

# restore the exec label -- a fresh cp lands as vendor_file, and chcon is not
# on the device
python3 -c 'import os; os.setxattr("'"$B"'", "security.selinux",
    b"u:object_r:hal_fingerprint_default_exec:s0\x00")'

rm -f "$B.busy"
reboot                                  # the ro remount fails while the old
                                        # binary is still open; the reboot settles it
```

Verify after reboot — the trustlet's count and the daemon's list must agree:

```sh
systemctl restart sailfish-fpd-community
/usr/libexec/droid-hybris/system/bin/logcat -d | grep -a "fpc_enumerate indices_count"
dbus-send --system --print-reply --dest=org.sailfishos.fingerprint1 \
    /org/sailfishos/fingerprint1 org.sailfishos.fingerprint1.GetAll
```

Templates already in the store but with no name appear as `Unknown <id>`; those
are real and can unlock the device, so remove any you do not recognise. Note the
lock screen holds a continuous identify session, so removal returns
`ALREADY_BUSY` until the device is unlocked and the screen kept awake.

To revert, restore `"$B.orig"` over `"$B"` the same way.

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
