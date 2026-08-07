# The LineageOS recovery

karatep's TWRP is 32-bit and cannot install a 64-bit Sailfish package
(Error 11 / Error 1). `hybris-recovery.img` is not a recovery in any usual
sense — it is `hybris-boot` built with `%ALWAYSDEBUG%`, giving busybox, fbsplash
and a telnet shell, with no UI, no adb, no zip installer. So the port builds the
LineageOS recovery instead.

Everything below is verified on hardware.

## Building it

```sh
bin/build-los-recovery.sh          # from the HOST
```

Artefacts land in `/opencloud/prebuilts/recovery/`:

| File | What |
|---|---|
| `recovery.img`, `recovery-<date>-<sha>.img` | the recovery |
| `update-binary` | the updater, for `bin/install-clean-updater.sh` |

This recovery is **installed on the device**, at `/dev/mmcblk0p35`. It replaced
TWRP, which is 32-bit and cannot install a 64-bit Sailfish package.

To try a new build without committing to it, live-boot instead of flashing:

```sh
fastboot boot /opencloud/prebuilts/recovery/recovery.img
```

To install one, from a booted Sailfish (verify the readback before rebooting):

```sh
dd if=recovery.img of=/dev/block/bootdevice/by-name/recovery bs=1M && sync
head -c $(stat -c%s recovery.img) /dev/block/bootdevice/by-name/recovery | md5sum
```

`fastboot boot` still works and is the way back if an installed recovery ever
breaks — the bootloader is unlocked.

### Why the tree is parked first

The recovery ramdisk's `/init` is `system/bin/init` from `system/core`. With
mer-hybris' series applied it carries *Disable ueventd service*, *Disable
SELinux* and *don't try to mount*, so the recovery dies before minui starts —
on hardware: no display and no USB enumeration at all. Confirmed by `strings`
on the ramdisk `/init`, which showed `/sbin/droid-hal-init` and
`/usr/libexec/droid-hybris/...`.

`bin/build-los-recovery.sh` therefore checks out every project either patch
series touches at `refs/remotes/m/hybris-18.1` (repo's exact manifest
revision, resolvable offline), builds, and restores the recorded SHAs through
an `EXIT` trap. It records the SHAs rather than re-running `apply-patches.sh`,
so restoring is still correct if parking died half-way.

`OUT_DIR=out-recovery` keeps this build out of `out/`, so no HAL rebuild is
owed afterwards. It must be **absolute**:
`vendor/lineage/config/BoardConfigKernel.mk` only prefixes the kernel's `O=`
with `$(BUILD_TOP)` when `OUT_DIR` is literally `out`, and any other relative
value makes the kernel land in `kernel/lenovo/msm8937/<out-dir>/`.

The same parking is why `updater` is built here — see
[rca/broken-update-binary.md](rca/broken-update-binary.md).

## karatep changes

### `/dev/block/bootdevice`

Recovery never mounts `/vendor`, so `init.target.rc` is never imported and
nothing creates the symlink that every by-name path in `fstab.qcom` needs.
Without it `/cache`, `/misc` and `/data` all fail to open, and the screen fills
with `E:` lines. `rootdir/etc/init.recovery.qcom.rc` creates it in `on fs`.

`init.recovery.${ro.hardware}.rc` is imported by init from the recovery ramdisk
root; the file is installed there by a `BUILD_PREBUILT` block in
`rootdir/Android.mk` with `LOCAL_MODULE_PATH := $(TARGET_RECOVERY_ROOT_OUT)`.

This has no effect on the Sailfish image: droid-hal packages nothing from
`$(TARGET_RECOVERY_ROOT_OUT)` and no vendor `init.*.rc`.

### bzip2

The installer zip runs `tar --numeric-owner -xvjf` from `updater-unpack.sh`.
toybox's `tar` does not decompress bzip2 itself — it execs an external `bzip2`,
which the stock ramdisk does not ship. The install then fails with the
updater's opaque

```
Failed to extract filesystem!
```

while the real error, visible only from a shell, is

```
tar: exec bzip2: No such file or directory
```

This is a long-standing community problem, not a karatep quirk. From the
`#sailfishos-porters` archive:

| Date | |
|---|---|
| 2026-04-12 | `the_hiktor`: `tar: exec bzip2: No such file or directory` after `tar --numeric-owner -xvjf $FS_ARC -C $FS_DST` |
| 2025-09-27 | `elros34`: "totally unexpected: tar: exec bzip2: No such file or directory" |
| 2022-02-06 | `HengYeDev`: "Only available recovery for me right now is lineage recovery which fails with Failed to extract filesystem!" |
| 2021-02-21 | `osum4est`: `tar: exec bunzip2: Too many symbolic links encountered` |

`Failed to extract filesystem!` recurs across 2021–2026 with no general fix on
record.

Two changes fix it:

1. `Sailfish-on-karatep/android_external_bzip2` — a fork of AOSP
   `platform/external/bzip2` at `android-11.0.0_r46` whose only change is
   `recovery_available: true` on the `bzip2` binary. `libbz` already had it.
2. `TARGET_RECOVERY_DEVICE_MODULES += bzip2.recovery` in `BoardConfigCommon.mk`.

The second is the part that is easy to get wrong. Ramdisk contents come from
the `recovery` module's dependency graph, not from `PRODUCT_PACKAGES`:
`bootable/recovery/Android.mk:73` folds `TARGET_RECOVERY_DEVICE_MODULES` into
the `recovery_deps` phony package, which `recovery` requires. A
`PRODUCT_PACKAGES` entry installs into the system image instead. Module names
carry Soong's `.recovery` variant suffix, as `make_f2fs.recovery` and
`sload_f2fs.recovery` already do in that file.

### adb without authorisation

`ro.adb.secure` is 1 on a userdebug build, and the recovery has no way to grant
authorisation before a screen is up. `init.recovery.qcom.rc` clears the recovery
override in `on init`:

```
setprop ro.adb.secure.recovery 0
```

`system/core/adb/daemon/main.cpp:218-223`:

```c
auth_required = GetBoolProperty("ro.adb.secure", false);
if (is_recovery)
    auth_required = auth_required && GetBoolProperty("ro.adb.secure.recovery", true);
```

No host key is ever written to the device — verified: `/adb_keys` does not
exist after boot, and adb still connects without a prompt. The property is
undeclared in `plat_property_contexts`, so it lands in `default_prop`, which
init may set. `on init` is early enough: adbd does not start until the recovery
binary sets `sys.usb.config=adb`. Setting it here rather than in `prop.default`
keeps it out of the system image.

## Verified state

| Check | Result |
|---|---|
| `E:` lines in the recovery log | 0 |
| `/cache` mount | `/dev/block/mmcblk0p50`, rw |
| `/data` mount | `/dev/block/mmcblk0p54`, rw |
| `/dev/block/bootdevice` | → `/dev/block/platform/soc/7824900.sdhci` |
| adb | connects with no prompt, `/adb_keys` absent |
| `bzip2` | `/system/bin/bzip2` + `bunzip2`, `bzcat`; version 1.0.6 |
| ELF files in the ramdisk | 79, all `ARM aarch64`; there is no `system/lib` |

## Never mount /system

In an Android 11 recovery `/system` **is** the ramdisk — `/bin` and `/etc` are
symlinks into `/system/bin` and `/system/etc`. Mounting the system partition
there shadows every binary, and from that moment every `exec` fails:

```
error: Exec '/system/bin/sh' failed: Too many symbolic links encountered
```

`adb reboot bootloader` fails too, because `ro.debuggable=1` makes adbd take the
`/system/bin/reboot` path rather than the `sys.powerctl` one
(`system/core/adb/daemon/services.cpp:144-160`). The only way out is the
on-screen menu or a power cycle.

The correct mount point is `/mnt/system`:

```sh
mount -o ro /dev/block/bootdevice/by-name/system /mnt/system
```

The recovery's own Advanced → Mount /system already does this
(`bootable/recovery/recovery.cpp:618`) and is safe.

`osum4est`'s 2021 report above — `tar: exec bunzip2: Too many symbolic links
encountered` — is the same trap from the other end.

### Why the kernel device tree had to change

`bootable/recovery/recovery_utils/roots.cpp:98` writes a generated `/etc/fstab`
from the merged volume table, so that shell tools work. The merge is
`ReadDefaultFstab()`: the **kernel device-tree fstab** first, then
`/etc/recovery.fstab`. The DT node

```
/proc/device-tree/firmware/android/fstab/system/
    compatible = android,system
    dev        = /dev/block/platform/soc/7824900.sdhci/by-name/system
```

carried no `mnt_point`, and `fs_mgr` then derives one from the node name
(`system/core/fs_mgr/fs_mgr_fstab.cpp:361-369`), producing `/system`. That
contradicted `rootdir/etc/fstab.qcom`, where the same partition is already
mapped to `/` — the Android root is what is labelled `system` on this device,
with the real system tree at `/system/system`.

The consequence is not only an operator typing the wrong command: any zip whose
updater-script does `mount("/system")` — a very common pattern — takes the
recovery down mid-install.

Fixed in `karate-common/msm8937-lenovo-common.dtsi` by adding
`mnt_point = "/"`, which is the mechanism fs_mgr provides for exactly this. The
alternatives were rejected:

| Option | Why not |
|---|---|
| Document only | The DT contradicts our own `fstab.qcom`; the hazard stays |
| `status = "disabled"` | fs_mgr skips it, but the node then reads as "no such partition", which is false |
| Delete the node | Same runtime effect, but silently drops the partition from the DT |

With no `/system` entry, `GetSystemRoot()` falls back to `/`
(`system/core/fs_mgr/fs_mgr_roots.cpp:169-171`) and Advanced → Mount /system
still mounts the same partition at `/mnt/system`. The `vendor` node beside it
was already consistent with `fstab.qcom` (`by-name/preload` → `/vendor`).

Changing the DT means rebuilding the kernel, and therefore `hybris-boot.img` and
`recovery.img`.

## Recovery version

`bootable/recovery` is at `ac8c9e96` (2022-11-09), the tip of `lineage-18.1` —
there is nothing newer to pull for this platform:

- LineageOS official has no `karatep` device tree (404).
- The HyperTeam device and kernel trees this port forks stop at `lineage-18.1`.
- A `lineage-20` recovery cannot build against an Android 11 platform.

## Entering sideload mode without the screen

The recovery reads its arguments from the BCB and then from
`/cache/recovery/command` (`recovery_main.cpp:103,153`). The `recovery` service
in `bootable/recovery/etc/init.rc:103` is not `oneshot`, so init restarts it:

```sh
adb shell 'mount /cache; mkdir -p /cache/recovery; echo "--sideload" > /cache/recovery/command'
adb shell 'pkill -f /system/bin/recovery'
# adb devices now reports state `sideload`
```

Use `echo`, not `printf -- "--sideload\n"` — toybox `printf` does not treat
`--` as end-of-options and writes a bare `--`.

A killed `adb sideload` leaves a stale fuse mount; the next attempt then fails
with `Failed to start fuse` and `read request: No such device`. Clear it:

```sh
adb shell 'umount -l /sideload'
```
