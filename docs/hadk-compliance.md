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
