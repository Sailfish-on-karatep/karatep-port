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

## Build environment gotchas

Three problems that stayed hidden because the original build environment had accumulated
state by hand. None would survive a rebuild on another machine, so they are worth knowing.

### scratchbox2 does not follow the `/opencloud` symlink

In the Platform SDK the workspace is reachable as `/opencloud` (a symlink to
`/parentroot/opencloud`). Ordinary shell commands follow it; **sb2 does not**:

```sh
sb2 -t lenovo-karatep-aarch64 ls /opencloud/hadk/.mb2/spec              # No such file or directory
sb2 -t lenovo-karatep-aarch64 ls /parentroot/opencloud/hadk/.mb2/spec   # works
```

`mb2` runs `rpmbuild` under sb2, so with the symlinked form every package build fails with

```
error: Unable to open $ANDROID_ROOT/.mb2/spec: No such file or directory
!! building of package failed
```

`$ANDROID_ROOT` must be `/parentroot/opencloud/hadk` inside the Platform SDK. The droid-config
kickstart already uses that form for the local repo. A build target created *before* the
symlink existed keeps working with the wrong form, which is why this only appeared after the
target was recreated.

### A stale `repodata/` silently disables the local RPM repo

`build_packages.sh` adds the locally built RPMs with `zypper --plus-repo dir:$LOCAL_REPO`. A
`dir:` URI is a **plaindir** repo — zypper scans the `.rpm` files directly. The helper
deliberately writes `createrepo_c` output to `$LOCAL_REPO/repo`, with the comment *"so that
`zypper --plus-repo` does not pick it up"*.

If anything leaves a `repodata/` directory in `$LOCAL_REPO` itself (for example a `createrepo`
run by hand), zypper treats the repo as rpm-md instead, fails the signature check on the
unsigned metadata, and drops the repo entirely:

```
Repository 'dir:.../droid-local-repo/karatep' is invalid.
 - Signature verification failed for repomd.xml
No provider of 'libhybris-libEGL' found.
```

Packages you just built become invisible to the next package that needs them. Fix: remove
`$ANDROID_ROOT/droid-local-repo/$DEVICE/repodata` (keep `repo/` — the kickstart points at it).

### `cpio` is missing from the chroot

See [Verify the boot image before flashing](#verify-the-boot-image-before-flashing) above.

---

## WLAN

Working. `wlan0` comes up at boot with a real MAC and connman scans normally:

```
wlan  3795573  0
wlan0  UP  00:0a:f5:02:15:e4
wlan: WCNSS software version CNSS-PR-4-0-00325
wlan: WCNSS hardware version WCN v2.0 RadioPhy with 19.2MHz XO
```

Two things matter, and both produce the same unhelpful symptom (`no wlan0`):

**1. The module must match the running kernel exactly.** `wlan.ko` lives in
`/lib/modules/$(uname -r)/`, shipped by `droid-hal-karatep-kernel-modules`. Any kernel change
alters the version string, so flashing a new `hybris-boot.img` **without** rebuilding
droid-hal and reinstalling the rootfs leaves the modules stranded under the old directory:

```
modprobe: FATAL: Module wlan not found in directory /lib/modules/3.18.124-perf-ga8b7042c2a84
```

Always rebuild `--droid-hal` (and the image) after touching the kernel — the boot partition and
the rootfs modules are a matched pair.

**2. The module must be loaded early, by `wlan-module-load.service`.** Loading it later always
fails, even with the correct module, WCNSS online and the platform device bound:

```
modprobe: ERROR: could not insert 'wlan': No such device
```

`dmesg` shows `wlan: loading driver v3.0.11.85.9` and then nothing — the probe finds no device
and the module unloads. The service exists precisely for this (HADK-HOT: *"if a particular
driver needs firmware before partitions are mounted, building the driver as a module and
creating a systemd service (like for wlan) which will modprobe it might help"*). Do not
diagnose WLAN by hand-modprobing after boot; reboot and let the unit do it.

Things that look suspicious here but are **not** the problem, all verified:
`droid.late_start=trigger_late_start` is set, `wcnss_service` is running, `subsys2: wcnss=ONLINE`,
the PIL firmware images (`wcnss.b00`…) are present, and `a000000.qcom,wcnss-wlan` is bound to
the `wcnss_wlan` platform driver.

---

## Bluetooth (broken)

`android.hardware.bluetooth@1.0-service-qti` aborts itself roughly every 61 s:

```
vendor.qti.bluetooth@1.0-data_handler: Aborting daemon to recover as controller init failed
libc: Fatal signal 6 (SIGABRT) in tid NNNN (bluetooth@1.0-s)
droid-hal-init: Service 'vendor.bluetooth-1-0-qti' (pid NNNN) received signal 6
hwservicemanager: Since android.hardware.bluetooth@1.0::IBluetoothHci/default is not
                  registered, trying to start it as a lazy HAL.
```

so `bluebinder` never activates and loops `Bluetooth binder service failed / Remote has died`,
climbing hci index each time (`Own hci index: 5`).

The HADK-FAQ prerequisites for binderized Bluetooth are already met — `CONFIG_BT_HCIVHCI=y` is
in the defconfig and bluebinder is installed — so this is device-specific controller bring-up,
not missing setup. The SMD transport nodes exist and are owned correctly
(`/dev/smd2`, `/dev/smd3`, `bluetooth:bluetooth`).

**Workaround:** `systemctl mask bluebinder`. With it masked the BT HAL starts once and stays
put instead of crash-looping.

**Also wrong regardless of the HAL bug:** `bluebinder.service` is `WantedBy=graphical.target`
*and* `Before=graphical.target`, so while it is stuck activating it delays the UI. A Bluetooth
proxy should not gate the display.

---

## Boot splash (needs a helper; `HYBRIS_BOOTLOGO`'s own mechanism cannot work)

Between the Lenovo bootloader logo and lipstick the screen is black for ~75 s, which reads as a
hang. The obvious fix — hybris-boot's `HYBRIS_BOOTLOGO`, which makes the initrd run
`zcat /bootsplash.gz > /dev/fb0` — **cannot work on this device**. The diagnosis is below; the
implemented replacement is [`hybris-boot/fbsplash.c`](https://github.com/Sailfish-on-karatep/hybris-boot),
where the same explanation is kept inline next to the code.

**No upstream guidance exists.** The [HADK](https://hadk.sailfishos.org/) (all chapters),
[`hadk-faq`](https://github.com/mer-hybris/hadk-faq) and
[HADK Hot](https://sailfishos.wiki/books/hardware/page/hadk-hot) contain **zero** mentions of a
boot splash, boot logo, `HYBRIS_BOOTLOGO` or `/dev/fb0`. HADK Hot's single mention of
`bootanimation` — `ANDROID_ROOT="/system" /system/bin/bootanimation` — is a *graphics-stack test
binary* listed beside `surfaceflinger` for use after `systemctl mask user@100000`. It is not a
splash mechanism. Do not go looking again.

Nor is there community guidance. The
[`#sailfishos-porters` archive](https://piggz.co.uk/sailfishos-porters-archive/) — greppable, and
the right first stop for anything like this — has **no hit at all** for `FBIOPAN` or `cont_splash`
in eleven years of logs, and what it does say about the splash is uniformly negative:

| | |
|---|---|
| `2015-02-13` sledges | *"nope, bootsplash was never implemented"* |
| `2016-04-01` alterego | *"there's no bootsplash for HADK port, so the framebuffer may get stuck until something clears it, or it may go black for a bit whilst Sailfish starts"* |
| `2019-04-12` r0kk3rz | *"there isnt a sailfish bootsplash"* — in reply to someone who had found `bootsplash()` in the init script and assumed it worked |
| `2018-01-25` lSDriim | *"I enabled bootlogo and when I tried do `zcat /bootsplash.gz > /dev/fb0` manualy it never returns"* |
| `2017-12-26` r0kk3rz | *"apparently the bootsplash was causing my device to hang at boot"* |
| `2014-08-12` energycsdx | *"bootlogo is disabled by default in hybris-boot"* |

So `HYBRIS_BOOTLOGO` is a vestigial feature that nobody has working, and on other devices it has
**hung the boot** rather than merely failing. That is why `fbsplash` forks into the background
before it touches the framebuffer and gives up on the first error: on this code path a cosmetic
splash must never be able to cost boot time, let alone block it. (Related, and worth remembering
if display power ever misbehaves after a splash: `2014-08-15` spiiroin, on a backlight bug —
*"current guess is: the boot logo leaves kernel side in some 'bootlogo mode'"*.)

**Why the write fails.** `/init.log` shows `zcat: write error: No such device` (ENODEV) on every
attempt. mdss defines no `.fb_write`, so generic `fb_write()` from `fbmem.c` runs, and it returns
`-ENODEV` whenever `info->screen_base` is NULL. On karatep it is always NULL, because of a
device-tree/driver mismatch in our own kernel:

```c
/* drivers/video/msm/mdss/mdss_fb.c — mdss_fb_alloc_fbmem_iommu() */
fbmem_pnode = of_parse_phandle(pdev->dev.of_node, "linux,contiguous-region", 0);
if (!fbmem_pnode) {
        mfd->fbi->screen_base = NULL;   /* no memory... */
        mfd->fbi->fix.smem_start = 0;
        return 0;                       /* ...and it reports success */
}
```

```dts
/* arch/arm64/boot/dts/qcom/msm8937-mdss.dtsi:193 */
mdss_fb0: qcom,mdss_fb_primary {
        compatible = "qcom,mdss-fb";
        qcom,cont-splash-memory {                       /* child node! */
                linux,contiguous-region = <&cont_splash_mem>;
        };
};
```

The property is one level below where the driver looks, so `of_parse_phandle()` returns NULL and
the framebuffer gets no memory — silently, with no warning. It is allocated lazily by
`mdss_fb_mmap()` under `if (!mfd->fbi->screen_base)`, and nothing in the initramfs ever mmaps fb0.

Reproduce it on a booted device (stop the UI first, per HADK Hot):

```sh
systemctl stop user@100000
python3 -c 'import os; fd=os.open("/dev/fb0", os.O_RDWR); os.write(fd, b"\xff"*4352*1920)'
# OSError: [Errno 19] No such device
systemctl start user@100000
```

**Second, independent blocker.** Even with memory, the panel is composited by MDP5: the visible
image comes from pipes reprogrammed only on an explicit commit (`mdss_mdp_overlay.c:3383` has a
dedicated "pan display is called before handoff is completed" branch), and `/proc/cmdline`
confirms continuous splash is on:

```
mdss_mdp.panel=1:dsi:0:qcom,mdss_dsi_nt35596_tm_1080p_video:1:none:cfg:single_dsi
                                                            ^ cont_splash_enabled
```

A `memcpy` into `smem` is never scanned out.

**Third blocker, found while writing the helper: closing the fd undoes the splash.**
`mdss_fb_release_all()` (`mdss_fb.c:3004`) runs on the *last* close of `/dev/fb0`, and at
refcount zero it does `mdss_fb_set_backlight(mfd, 0)` (`:3075`),
`mdss_fb_blank_sub(FB_BLANK_POWERDOWN, …)` (`:3077`) and `mdss_fb_free_fb_ion_memory()` (`:3085`)
— backlight off, panel off, buffer freed. A helper that exits after committing would show the
image for a few milliseconds and then go dark, which looks exactly like the bug being fixed.

### The fix: `fbsplash`

`hybris-boot/fbsplash.c` — a 4.4 KB **freestanding** aarch64 binary staged into the initramfs
beside busybox (`$(PRODUCT_OUT)/utilities/`, copied in by the ramdisk rules in `Android.mk`).
`bootsplash()` in `init-script` now runs:

```sh
zcat /bootsplash.gz | /bin/fbsplash
```

The sequence, and why each step is needed:

| Step | Why |
|---|---|
| `clone(SIGCHLD)`, parent exits | boot never waits on the display; the only cost is `zcat` filling the pipe |
| `open("/dev/fb0", O_RDWR)` | also unblanks: `mdss_fb_open()` (`:2951`) runs `FB_BLANK_UNBLANK` on first open, without which `mdss_fb_pan_display_ex()` bails at `mdss_fb_is_power_off()` (`:3423`) |
| `FBIOGET_{F,V}SCREENINFO` | `fix.smem_len` is populated at registration (`:2888`) even with no memory allocated |
| `mmap(smem_len)` | **the load-bearing step** — triggers the lazy ION allocation (`:2495` → `:2371`), which is what sets `screen_base` (`:2437`) *and* `mfd->fbmem_buf` (`:2400`), the dma_buf MDP scans out from. Larger than `smem_len` → `-EOVERFLOW` (`:2490`) |
| `read(stdin)` into the map | mapping is write-combine (`:1426`, `:2532`), so no cache maintenance and `read(2)` can land straight in it |
| `FBIOPAN_DISPLAY`, `yoffset = 0` | the commit. `mdss_mdp_overlay_pan_display()` sources the pipe at `xoffset*bpp + yoffset*line_length` (`mdss_mdp_overlay.c:3414`) and calls `mdss_mdp_overlay_start()`, whose kerneldoc (`:1342`) describes this exact splash→bootanimation handoff. `var.activate` is **not** consulted on this path (only `FBIOPUT_VSCREENINFO` reads it, `fbmem.c:1002`) |
| **do not close** — sleep forever holding the fd | see the third blocker above |

Because the pan offset is now explicit, the image only needs **one** screen
(4352 × 1920 = 8,355,840 bytes); `make-bootsplash.py` defaults to `--buffers 1`.

Escape hatch if the holder process is ever in the way: `killall fbsplash` (it releases the fd and
blanks the panel). Turning the whole thing off again is `HYBRIS_BOOTLOGO := 0` in `Android.mk`.

### Fourth blocker: do not link this against bionic

The first working version of the logic above still produced nothing, because it never reached
`main()`:

```
[    4.000389] random: fbsplash urandom read with 93 bits of entropy available
[    4.000674] Unhandled fault: alignment fault (0x92000021) at 0x9aab744d0267201b

+ zcat /bootsplash.gz
+ /bin/fbsplash
Bus error
```

The fault address is different on every run and carries full entropy, i.e. bionic's static startup
dereferences random bytes shortly after seeding itself from `/dev/urandom` — its TLS/main-thread
setup does not survive this environment. A 1.3 KB freestanding test binary runs in the same shell
without complaint, so the initramfs itself is fine.

The reason this has never bitten anyone before: **the busybox in the initramfs is not an Android
binary.** `file` calls it *"statically linked, for GNU/Linux 3.7.0"* — a glibc prebuilt. Nothing
else in the hybris-boot ramdisk is compiled against bionic.

So `fbsplash.c` is freestanding: raw `svc #0` syscalls, its own `_start`, `-nostdlib`, hand-copied
`fb_{fix,var}_screeninfo` (with `_Static_assert`s on their sizes, since a hand-copied UAPI struct's
size is the one thing that can silently drift). `Android.mk` therefore builds it with a direct
`clang` rule rather than `BUILD_EXECUTABLE`. **Do not convert it back to a normal module.**

Being freestanding also removed the last reason for a watchdog. An earlier draft armed `alarm(10)`
around the ioctls because `FBIOPAN_DISPLAY` can block for `WAIT_DISP_OP_TIMEOUT` = 30 s
(`mdss_fb.h:46`). That buys nothing here: the process forks before touching the display, so a hang
costs no boot time, and it is *meant* to live forever holding `fb0` — a hung pan and a successful
one cost exactly the same. Three syscalls of signal setup, deleted.

### Verified on hardware

`fastboot boot hybris-recovery.img`, then on the telnet shell at `192.168.2.15:23`:

```
# zcat /bootsplash.gz | /bin/fbsplash
fbsplash: fb0 xres=1080 yres=1920 yres_virtual=3840 bpp=32 line_length=4352 smem_len=16711680
fbsplash: copied 8355840 of 16711680 bytes
fbsplash: splash displayed, holding /dev/fb0 open
```

The wordmark appears on the panel and stays. The geometry line doubles as a check on the
hand-copied structs — it must match `/sys/class/graphics/fb0/{virtual_size,stride,bits_per_pixel}`.
Killing an older `fbsplash` while a newer one holds `fb0` leaves the image up, as the refcount
analysis predicts.

### The artwork

`assets/sfosboot.png` is the official Sailfish wordmark: **black artwork on a transparent
background**. Composited onto a black splash that is a black rectangle, so `make-bootsplash.py`
defaults to `--ink white`, which ignores the artwork's own colour and uses its **alpha channel as
ink coverage**, painted white on black — preserving the anti-aliasing. Scaling is a single
`min()` factor for both axes (never stretched), positioned by `--fit-width` / `--center-y`.

> **Dead ends, recorded so they are not retried:** writing the image into both framebuffer
> buffers (the offset is not the problem); correcting the image geometry to stride 4352 × 1920
> (the size was already right — it matches `stride * virtual_h` exactly); retrying the write
> up to 15 times (it can never succeed, and cost 15 s on every boot until reverted); and building
> the helper as an ordinary Android `BUILD_EXECUTABLE` (see the fourth blocker above).

---

## Known-good workarounds (not yet root-caused)

* **`bluebinder` and WLAN conflict at boot.** With `bluebinder` unmasked, `wlan0` never
  appears and `modprobe wlan` reports no such device; `bluebinder` itself hangs in
  "activating". Masking `bluebinder` makes WLAN work, and Bluetooth works if the service is
  started manually after boot. Possibly related to an incorrect WLAN MAC — unconfirmed.
* **`ofono` sometimes needs `systemctl restart ofono` after boot** for RIL to come up.
* **To escape a bootloop**, create `/data/.stowaways/sailfishos/init_enter_debug2`. init then
  halts before starting systemd and telnet is available on port 2323. (Thanks @mal)
* **Always reboot with `reboot -f`.** A graceful `reboot` hangs indefinitely — once observed
  wedged for ~36 minutes with `systemctl is-system-running` reporting `initializing` and 46 jobs
  queued. `droid-hal-init.service` never stops, so systemd waits for it forever. `-f` skips the
  shutdown transaction. This matters when a fix is applied over telnet: without `-f` the device
  appears to have crashed.

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
