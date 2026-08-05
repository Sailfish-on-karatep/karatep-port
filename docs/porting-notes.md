# Porting notes — lenovo/karatep

Accumulated device-specific knowledge for the Sailfish OS port of the Lenovo Vibe K6 Note /
Plus (`karatep`, MSM8937 / Snapdragon 430, Adreno 505), based on LineageOS 18.1 /
`hybris-18.1`.

These are working notes. Anything with a firm root cause gets promoted to its own write-up
under [`rca/`](rca/).

---

## Device facts

| | |
|---|---|
| Android codename | `karatep` |
| SoC | Qualcomm MSM8937, Snapdragon 430, Adreno 505 |
| Kernel | 3.18, defconfig `arch/arm64/configs/karatep_defconfig` |
| Android base | LineageOS 18.1 (Android 11), `hybris-18.1` |
| Port arch | `aarch64` (full 64-bit userspace) |
| Sailfish OS | 5.1.0.11 (Pispala) |
| Display pixel ratio | **1.6** |
| boot partition | `/dev/mmcblk0p34` |
| system partition | `/dev/mmcblk0p52` |
| userdata partition | `/dev/mmcblk0p54` |

The recovery does **not** expose Android's `by-name` symlinks, which is why the flashing
instructions use raw `mmcblk0p*` nodes.

---

## Fixes that are in the tree

* **`/system` must map to `/` in `fstab.qcom`.** The partition labelled `system` is really the
  Android *root* partition; the actual system lives at `/system/system`. File:
  `device/lenovo/karate-common/rootdir/etc/fstab.qcom`. (Thanks @mal)
* **Kernel bootflags go in `BoardConfig.mk`**, not the defconfig — `selinux=1` (permissive) and
  `audit=0` (silences audit spam). (Thanks @elros34 for `audit=0`)
* **SELinux files must be copied from the device's `/vendor`, never symlinked.** (Thanks @mal)
* **Sailjail needs namespaces**: `CONFIG_UTS_NS`, `CONFIG_IPC_NS`, `CONFIG_USER_NS`,
  `CONFIG_PID_NS`, `CONFIG_NET_NS`, `CONFIG_NF_CONNTRACK_NETBIOS_NS` in `karatep_defconfig`.
* **`CONFIG_ANDROID_BINDER_DEVICES` is a string, not a bool.** It must read
  `CONFIG_ANDROID_BINDER_DEVICES="binder,hwbinder,vndbinder"`. Writing `=y` is silently
  discarded and the Kconfig default is used instead — it happens to be the same value, so the
  mistake is invisible until a Kconfig change drops a binder node.
* **Flattened-APEX linker spam** (`Expecting header 0x53595354 but found 0x564e4452`) — see
  [rca/vndservicemanager-libbinder.md](rca/vndservicemanager-libbinder.md). Fixed properly by
  the `karatep-patches` `system/core/0040..0042`; the older advice to "apply the linkerconfig patch" only
  addressed half of it.
* **Chromium WebView is not synced.** LineageOS' manifest links `patches/Android.mk`, which no
  longer exists upstream, leaving a dangling symlink that kills the build with
  `build/make/core/prebuilt.mk:53: error: external/chromium-webview/Android.mk: No such file
  or directory`. `manifests/local_manifests.xml` drops the project.

---

## Verify the boot image before flashing

`make hybris-hal` ending in

```
Install: out/target/product/karatep/hybris-boot.img
```

is **not** proof of a usable image. hybris-boot builds its initramfs by copying
`hybris/hybris-boot/initramfs/` into a staging directory; if that source directory is empty,
it packs a 20-byte empty gzip and produces a boot image with no `init` at all. The build
reports success.

Flashing such an image does not brick the device, but it does not boot either: the kernel
comes up with no initramfs, panics, and the device drops off USB and re-enumerates as
`05c6:900e Qualcomm QHSUSB__BULK` before returning to fastboot. There is no RNDIS, so the
recovery shell never appears and any network-based installer just waits forever.

Cheap pre-flight check — a sound image is roughly **1.5 MB larger** than the bare kernel:

```sh
cd $ANDROID_ROOT/out/target/product/karatep
ls -l kernel hybris-boot.img
# good: kernel 10,691,936   hybris-boot.img 12,283,904   (~1.59 MB of initramfs)
# bad:  kernel 10,691,936   hybris-boot.img 10,696,704   (4,768 bytes — no initramfs)
```

Or directly:

```sh
ls -l out/target/product/karatep/obj/ROOT/hybris-boot_intermediates/boot-initramfs.gz
# 20 bytes == an empty gzip stream == no initramfs
```

### Cause: `cpio` is missing from the HABUILD chroot

`hybris/hybris-boot/Android.mk` builds the ramdisk with

```make
@(cd $(BOOT_INTERMEDIATE)/initramfs && find . -printf '%P\n' | cpio -H newc -o ) | gzip -9 > $@
```

The Jolla Ubuntu chroot (`ubuntu-focal-20210531-android-rootfs.tar.bz2`) **does not ship
`cpio`** — no binary, no dpkg record. `cpio` is also absent from Soong's PATH sandbox
(`prebuilts/build-tools/path/linux-x86/`, which carries ~60 host tools). mer-hybris'
`build/soong/0001-hybris-Add-cpio-to-allowed-commands.patch` marks it `Allowed`, but that only
permits pass-through to the *host* binary — it does not provide one.

With `cpio` absent the pipeline emits nothing, and because the recipe's exit status is `gzip`'s
(which succeeds on empty input), **make reports success**. You get
`Install: … hybris-boot.img` and an image with no `init`.

Fix, once per chroot:

```sh
/opencloud/bin/habuild -u $USER /bin/bash -c 'sudo apt-get update && sudo apt-get install -y cpio'
```

Then delete the stale intermediates so the ramdisk is regenerated — a 20-byte
`boot-initramfs.gz` is otherwise considered up to date:

```sh
rm -rf $ANDROID_ROOT/out/target/product/karatep/obj/ROOT/hybris-{boot,recovery}_intermediates
```

> An earlier revision of this note blamed an empty `hybris/hybris-boot/initramfs/` source
> directory after a partial `repo sync`. That was wrong: a later build had the staging
> directory fully populated and still produced a 20-byte ramdisk. The missing `cpio` is the
> actual cause.

Separately — and still true — `repo sync` resets `hybris/hybris-boot` unless the local manifest
pins it to a fork; the HADK warns about this in *Configure Mountpoint Information*, and
`manifests/local_manifests.xml` now pins `Sailfish-on-karatep/hybris-boot`. HADK's *Common
Pitfalls* also says a `--force-sync` should be followed by a full `repo sync`.

---

## Known-good workarounds (not yet root-caused)

* **`bluebinder` and WLAN conflict at boot.** With `bluebinder` unmasked, `wlan0` never
  appears and `modprobe wlan` reports no such device; `bluebinder` itself hangs in
  "activating". Masking `bluebinder` makes WLAN work, and Bluetooth works if the service is
  started manually after boot. Possibly related to an incorrect WLAN MAC — unconfirmed.
* **`ofono` sometimes needs `systemctl restart ofono` after boot** for RIL to come up.
* **To escape a bootloop**, create `/data/.stowaways/sailfishos/init_enter_debug2`. init then
  halts before starting systemd and telnet is available on port 2323. (Thanks @mal)

---

## Debugging the boot

The stock journald configuration on this rootfs destroys boot evidence:

```
Storage=volatile
RuntimeMaxUse=1M
RateLimitBurst=300
```

With anything crash-looping, `journalctl -b` and `dmesg` both wrap within ~500 s, so the first
two minutes of boot are gone by the time you look. Before debugging any boot problem:

```sh
cp /etc/systemd/journald.conf /etc/systemd/journald.conf.bak
printf '%s\n' '[Journal]' Storage=persistent SplitMode=none \
    RateLimitIntervalSec=0 RateLimitBurst=0 SystemMaxUse=300M RuntimeMaxUse=64M \
    > /etc/systemd/journald.conf
```

`/system/bin/logcat` exists but is **not on `PATH`** in the debug shell. The Android-side log
is where HAL and binder errors actually land, so invoke it by full path:

```sh
/system/bin/logcat -d | grep -iE "composer|binder|servicemanager"
```

Useful one-liner to see which `libbinder` each process actually linked — the fastest way to
spot a VNDK namespace problem:

```sh
for d in /proc/[0-9]*; do p=${d#/proc/}
  grep -o "/[^ ]*libbinder\.so" /proc/$p/maps 2>/dev/null | sort -u |
    sed "s|^|$(cat /proc/$p/comm 2>/dev/null) |"
done | sort -u
```

---

## Still to fix

* IMS daemons (`vendor.imsrcsservice`, `vendor.ims_rtp_daemon`, `vendor.imsdatadaemon`)
  crash-loop continuously, spamming the journal.
* `hwservicemanager` retries `android.hardware.bluetooth@1.0::IBluetoothHci` every ~61 s.
* Failed systemd units: `droid-bootctl.service`, `systemd-tmpfiles-setup.service`,
  `wlan-module-load.service`. (`dev-binderfs.mount` also fails, but that is expected — binderfs
  is Linux 5.0+ and this kernel is 3.18; the legacy `/dev/{,hw,vnd}binder` nodes are correct.)
* 3.5 mm audio routing — jack is detected, audio is not routed.
* Mobile data does not work; SIM slot 2 reports "Network: Denied".
* Fingerprint (FPC 1020) unsupported.
* Cameras and RIL are flaky.

---

## References

* HADK — <https://hadk.sailfishos.org/>
* HADK Hot — <https://sailfishos.wiki/books/hardware/page/hadk-hot>
* Sailfish OS wiki — <https://sailfishos.wiki/>
* `#sailfishos-porters` IRC logs — <https://irclogs.sailfishos.org/logs/%23sailfishos-porters/>
* ofono conflict discussion —
  <https://irclogs.sailfishos.org/logs/%23sailfishos-porters/%23sailfishos-porters.2023-02-03.log.html>
* SELinux permissive setup —
  <https://piggz.co.uk/sailfishos-porters-archive/index.php?log=2024-08-20.txt#line64>
* Reverse USB tethering —
  <https://forum.sailfishos.org/t/using-internet-over-rndis-usb-computer-reverse-tethering/10104/6>
* LineageOS device settings reference —
  <https://github.com/tanvirr007/CustomROM_build_guide_aosp>
