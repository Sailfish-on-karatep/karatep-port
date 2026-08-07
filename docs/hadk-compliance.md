# HADK compliance audit — lenovo/karatep

A chapter-by-chapter check of this port against the
[Sailfish OS HADK](https://hadk.sailfishos.org/). The goal is that following the HADK on a
fresh machine, plus `manifests/local_manifests.xml`, reproduces this build with no local
knowledge.

Legend: ✅ compliant · 🔧 was non-compliant, fixed · ⚠️ deliberate documented deviation ·
📋 outstanding

---

## Prerequisites

| Item | Status |
|---|---|
| 64-bit x86 host, 64-bit Linux kernel | ✅ |
| ≥30 GiB free (Android 11 needs more) | ✅ 306 GiB free |
| ≥4 GiB RAM | ✅ |
| Device supported by LineageOS, running the matching base | ✅ LineageOS 18.1 |
| **`cpio` available to the build** | 🔧 **absent from the Jolla Ubuntu chroot** — see [porting-notes](porting-notes.md#cause-cpio-is-missing-from-the-habuild-chroot). Not listed in HADK prerequisites; arguably a gap in the manual, since `hybris-boot` cannot build a ramdisk without it and fails *silently*. |

## Setting up the SDKs

| Item | Status |
|---|---|
| Platform SDK installed | ✅ |
| `android-tools-hadk`, `kmod`, `createrepo_c` installed in the Platform SDK | ✅ all three present |
| Ubuntu (HABUILD) chroot for the Android build | ✅ focal 20210531 |
| `$HOME/.hadk.env` | ⚠️ relocated to `/opencloud/hadk.env` — this workspace may not write to `~`. Same contents; sourced explicitly. |
| `$HOME/.mersdkubu.profile` | ⚠️ relocated to `/opencloud/mersdkubu.profile`; not auto-sourced by `ubu-chroot` as a result, so `hadk.env` is sourced explicitly. |
| `ANDROID_ROOT`, `VENDOR`, `DEVICE`, `PORT_ARCH` | ✅ |
| Optional `mb2 --output-dir` alias from the template | 🔧 now defined |

## Building the Android HAL

| Item | Status |
|---|---|
| `repo init -u .../mer-hybris/android.git -b hybris-18.1` | ✅ |
| Device repos pinned via `.repo/local_manifests/$DEVICE.xml` | ✅ |
| `repo sync` **without** `--fetch-submodules` on hybris-18.1+ | ✅ |
| `fixup-mountpoints` carried by a **fork pinned in local_manifests** | 🔧 was a patch; now `Sailfish-on-karatep/hybris-boot`, exactly as the manual prescribes |
| `breakfast $DEVICE` | 🔧 was `lunch lineage_karatep-userdebug` |
| `make -j$(nproc --all) hybris-hal droidmedia` | 🔧 was `-j$(nproc)` |
| `hadk` (i.e. the env) sourced **before** `breakfast` | 🔧 `bin/build-hal.sh` skipped it. `droidmedia`'s prerequisites come from `detect_build_targets.sh $(PORT_ARCH) $(TARGET_ARCH)`; with `PORT_ARCH` unset the arguments shift, the script exits 1, and the phony target builds **nothing** while `make` still reports success. Silent: no `libdroidmedia`, `libminisf`, `minimediaservice` or `minisfservice` in `out/`. |
| `mer_verify_kernel_config` on the built `.config` | 🔧 never run before → **0 errors, 48 warnings** (all optional: NFS, autofs, netfilter accounting) |

## Installing Build Tools for Your Device

| Item | Status |
|---|---|
| Tooling named for the release, not `SailfishOS-latest` | 🔧 now `SailfishOS-5.1.0` |
| Target named `$VENDOR-$DEVICE-$PORT_ARCH` | ✅ `lenovo-karatep-aarch64` |
| `aarch64` target for a 64-bit userspace port | ✅ |

## Packaging Droid HAL

| Item | Status |
|---|---|
| `--droid-hal`, `--configs`, `--mw`, `--gg`, `--version` in that order | 🔧 previously only `--droid-hal`/`--version` were run |

## Creating the Sailfish OS Root Filesystem

| Item | Status |
|---|---|
| `RELEASE` exported | 🔧 was unset; `--mic` refuses without it |
| `EXTRA_NAME` | ⚠️ unset (optional) |
| `build_packages.sh --mic` producing a flashable `.zip` | ✅ |

## Modifications and Patches

| Item | Status |
|---|---|
| Upstream changes carried as forks repinned in `local_manifests` ("Contribute your mods back") | ✅ kernel, both device trees, vendor blobs, droid-hal/-config, hybris-boot |
| `mer-hybris/hybris-patches` used unmodified | ✅ synced normally, never forked or overridden |
| Port-specific patches that provably cannot be forks | ✅ `karatep-patches` (`system/core`, `bionic`) — verified they fail to apply to a pristine tree |
| No hand-edits in the Android source | ✅ `repo status` reports zero tracked modifications |

## Flashing the rootfs image / Manual Installation and Maintenance

The HADK has two install chapters. `flashing/` assumes Android Recovery and two `.zip`s pushed
with `adb`; `manual-install/` extracts the rootfs tarball into `/data/.stowaways/sailfishos` and
`dd`s `hybris-boot.img` onto the raw boot node, with `fastboot boot` for testing.

| Item | Status |
|---|---|
| Install by `tar -xj` into `/data/.stowaways/sailfishos` + `dd` of `hybris-boot.img` | ✅ this is `manual-install/`, followed step for step |
| The shell those commands run in | ⚠️ the HADK assumes rooted `adb` in booted Android; here it is the recovery's telnet on port 23, because that shell is guaranteed to exist. The commands are unchanged. |
| `.zip` install via recovery (`flashing/`) | ❌ karatep's TWRP is 32-bit and rejects the aarch64 installer (Error 11 / Error 1). The archive shows this path failing on many devices (Error 7, signal 7/11, "Failed to extract filesystem") in both TWRP and LineageOS recovery, so it is not a karatep-only problem. |
| `fastboot boot hybris-recovery.img` for the install shell | ⚠️ not in the HADK, but the only way to reach a shell here. Never flashed — that would clobber the recovery partition. |

## Package Naming Policy

| Item | Status |
|---|---|
| Lower-case `$DEVICE` | ✅ `karatep` |
| `droid-hal-$DEVICE`, `droid-hal-$DEVICE-*` | ✅ incl. `-kernel`, `-kernel-modules`, `-img-boot`, `-img-recovery` |
| `droid-config-$DEVICE`, `droid-hal-version-$DEVICE` | ✅ |

## List of Repositories

| Repo | Status |
|---|---|
| `droid-hal-$DEVICE` | ✅ |
| `hybris-boot` | ✅ (karatep fork) |
| `libhybris` | ✅ at `external/libhybris`, where the manifest places it |
| `qt5-qpa-hwcomposer-plugin` | ✅ |
| `mer-kernel-check` | ✅ |
| `hybris-installer` | ⚠️ not used — the flashable `.zip` comes from droid-hal's `installable_zip` macro instead |

## Hardware Adaptation Checklist

📋 **Not yet worked through.** 25 runtime-verification items (thermal/dsme, watchdog, usb-moded,
buteo-mtp, ssu config, vibra, suspend/resume via iphb, volume & power keys, proximity and ALS,
LED, CSD, double tap, zram, act-dead, extra filesystems). Several already have known problems
recorded in [porting-notes](porting-notes.md) — WLAN/`bluebinder`, 3.5 mm routing, mobile data,
fingerprint. This is the natural next body of work once the port boots reliably.

---

## Deviations that are deliberate

1. **Nothing may be written to `$HOME`.** The HADK puts `.hadk.env`, `.mersdkubu.profile` and a
   `.bashrc` snippet there; this workspace keeps everything under `/opencloud` and sources the
   env file explicitly. Functionally equivalent.
2. **`hadk.env` is shell-aware.** The HADK template hardcodes one `ANDROID_ROOT`. Here the same
   directory has three different paths (HOST / Platform SDK / HABUILD) and a fourth view inside
   scratchbox2, so the file detects its environment. See
   [porting-notes](porting-notes.md#scratchbox2-does-not-follow-the-opencloud-symlink).
3. **`hybris-installer` unused**, as above.
4. **`karatep-patches` as a second patch series.** Accepted as it stands: the project is pinned in
   `.repo/local_manifests/karatep.xml`, so `repo sync` brings it in with everything else and there
   is no manual setup. Applying it is one command beside the one the HADK already requires:

   ```sh
   hybris-patches/apply-patches.sh --mb      # upstream, first
   karatep-patches/apply-patches.sh --mb     # then ours
   ```

   Both must be re-run after every `repo sync`, which resets the tree to the manifest revisions.

---

## Alignment plan (open)

Recorded rather than acted on. Ordering is deliberate: nothing here is worth destabilising a
working port for.

### Deferred until the current fixes are proven stable

- **`geoclue-providers-hybris` fork.** `build_packages.sh -m` only builds geoclue from its built-in
  list when the Android base is ≤ 7 (`build_packages.sh:288`), so on an 18.1 base it is built
  explicitly with `--mw=geoclue-providers-hybris` — the driver's own single-package interface.
  The deviation that remains is the fork itself. Upstream it to `mer-hybris` once the GPS work is
  finished and the fix is known good; the fork disappears if it is merged.
- **`sailfish-fpd-community` fork.** Built with `--build=` + `--spec=`, which is upstream's own
  documented flow, so only the fork is a deviation. PR filed upstream
  (`sailfishos-open/sailfish-fpd-community` #40). Leave as is until enrolment/unlock/removal have
  survived a few weeks of daily use.
- **`android.hardware.biometrics.fingerprint@2.0-service`** lives on the **vendor** partition, which
  a Sailfish image never writes, so it is installed by hand ([flashing.md](flashing.md) step 11).
  It could be shipped in the rootfs and bind-mounted over `/vendor/bin/hw/…` from a droid-config
  unit, making the image self-contained — but that adds a boot-ordering failure mode to a subsystem
  that currently works. Not worth it yet.

### Replacing TWRP — blocked, and not by the obvious thing

TWRP here is 32-bit and cannot run the aarch64 installer, so it earns its keep only as an on-device
backup tool. The natural replacement is the LineageOS recovery, which the device tree already
supports (`BoardConfigCommon.mk:152,178`) and which the build emits for free as
`out/target/product/karatep/recovery.img` — a genuine 64-bit recovery with a GUI, `adbd` and
toybox, sized 19 MB against a 64 MB recovery partition (`mmcblk0p35`).

**But an image built from this tree is unusable.** The recovery ramdisk's `/init` is
`system/bin/init`, built from our patched `system/core`, and carries the whole hybris series —
`Disable ueventd service`, `Disable SELinux`, `Don't create/mount proc, apex, dev/null, random`,
`don't try to mount`, `ignore "mount" and "mkdir /tmp"`, `Do not SetupMountNamespaces()`. Verified
by extracting the ramdisk: the strings `/sbin/droid-hal-init` and
`/usr/libexec/droid-hybris/system/etc/init/hw/init.rc` are both present in it. A recovery whose
init refuses to populate `/dev` or mount anything will not come up.

The fix is sequencing, not a new checkout: build `recoveryimage` **after `repo sync` and before
`apply-patches.sh`**, archive the artifact, and keep using it — a recovery does not need rebuilding
on every port change.

### Not planned

Building a 64-bit TWRP. It needs a separate TWRP device tree and ongoing maintenance, and buys
nothing the LineageOS recovery does not already give.

### `hybris-recovery` is not a recovery, and cannot become one

It is the same `init-script` as `hybris-boot` with `%ALWAYSDEBUG%` set to 1
(`hybris-boot/Android.mk:198-227`, `init-script:397`); the initramfs holds busybox, `fbsplash` and
a splash image. Capabilities: RNDIS, telnet, mass-storage export of `/dev/mmcblk0`, static splash.
No touch, no menus, no backup/restore, no zip installer, no adb, and no work in flight to add any.
`#sailfishos-porters` concurs — *"the only difference between recovery and normal image is
ALWAYSDEBUG value"* (elros34, 2022-07-29), *"usually nobody uses it"* (elros34, 2024-11-13),
*"that recovery is quite useless anyway"* (mal, 2026-05-15). Its role is the rescue/install shell,
and that is the right role for it.
