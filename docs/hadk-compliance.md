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

Worked through against the running device on 2026-08-11 (Sailfish 5.1.0.11, kernel
`3.18.124-perf-g3a06ccbcfede`). Every verdict below rests on a command run on hardware, on the
defconfig, or on reading the consuming daemon's source — not on inspection of our own config.

| # | Item | Status |
|---|---|---|
| 1 | Thermal sensor config for dsme | 🔧 `/etc/dsme` was empty, `battery_temperature` returned the `-9999` no-data sentinel. Fixed by `sparse/etc/dsme/thermal_sensor_karatep.conf` (battery / core / surface) |
| 2 | memnotify patch + config for mce | 🔧 all four thresholds were 0, `get_memory_level` returned `unknown`. **No kernel patch needed** — `memnotify.c` wants `/dev/memnotify`, but `mempressure.c` needs only `CONFIG_MEMCG`, already set for Waydroid. Fixed by `sparse/etc/mce/60-karatep-memnotify.conf` |
| 3 | Watchdog driver + dsme | 📋 **broken.** No `/dev/watchdog*`, no `/sys/class/watchdog`, no `CONFIG_WATCHDOG*`. `CONFIG_MSM_WATCHDOG_V2=y` is the kernel-stuck hardware watchdog and exposes no chardev, so dsme falls through to *"Could not open any watchdog files"*. Nothing reboots the device if userspace hangs |
| 4 | usb-moded | ✅ active, mode `developer_mode`, cable state `connected`, dyn-modes present |
| 5 | USB diag mode | ➖ optional, untested |
| 6 | USB gadget + buteo-mtp | ❓ `buteo-mtp-qt5` and `mtp_mode-droid.ini` present, legacy `android_usb` gadget (not ConfigFS). Never connected to a host as MTP — doing so drops the RNDIS link every debug session runs over |
| 7 | ssu config files | ✅ `ssu` and `ssu-sysinfo` agree (karatep / K6 Note / Lenovo, 5.1.0.11) |
| 8 | Vibra driver | ✅ Android `timed_output` at `/sys/class/timed_output/vibrator`, driven by `ngfd-plugin-native-vibrator` + `libhybris-libvibrator`. No `ff-memless` needed |
| 9 | Suspend | 📋 **broken.** 25 successes against 4423 failures; 4125 of those are `suspend_noirq` vetoed by `qpnp-vadc-14` with `-EBUSY`, plus 256 `failed_freeze`. mce reported 181 s suspended in 34 786 s of uptime. Measured on USB, which holds `msm_otg`/`smbchg` wakelocks and explains why few attempts *start* — but not the noirq vetoes, which are a driver fault. Needs a run on battery to get a clean figure |
| 10 | Resume via iphb | ❓ plumbing is present — dsme's `iphb.so` loads and the socket `/dev/shm/iphb` exists — but it cannot be exercised while #9 fails |
| 11 | Volume key probing & policy | ❓ `gpio-keys` on event3; none of the three policy behaviours tested |
| 12 | Power key | ✅ `qpnp_pon` on event0, dsme `pwrkeymonitor.so` loaded, long-press reaches the power menu |
| 13 | Proximity sensor in suspend | ❓ sensorfw advertises `proximitysensor` and mce is set `on_demand=true`, but it has never been read. Blocked behind #9 regardless |
| 14 | Ambient light sensor | ⚠️ verified only indirectly, by measurement — mce scales the LED by an ALS-derived level and the LED tracks room brightness. Zero-lux-in-darkness and power-up latency untested |
| 15 | LED | ✅ see [porting-notes](porting-notes.md#notification-led); `60-karatep-led.ini` pins the backend and the ALS curve |
| 16 | Proximity blanking during a call | ❓ needs a usable SIM |
| 17 | CSD config | 🔧 hardware features were declared but `Hall=1` was wrong — no hall device in `/proc/bus/input/devices`, only `h2w`/`wfd` in `/sys/class/switch`, no driver in the defconfig or either device tree, and csd's own probe path `/proc/irq/396/sensor_hall` is absent — and `LedType=RGB` made `VerificationLED` unpassable on a single white LED. Factory and run-in sets were falling back to csd's built-in lists. All three fixed in `hw-settings.ini` |
| 18 | abootsettings | ✅ `abootsettings.so` present |
| 19 | Double tap | ⚠️ half-wired. `doubletap/mode` is 2 and `use_fake_double_tap` is true, so mce synthesises taps from touch — but mce's `doubletap.so` drives `/sys/class/i2c-adapter/i2c-3/3-0020/block_sleep_mode`, a Synaptics path that does not exist here. The real controller is `fts_ts` at `3-0038`, which exposes `wake_gesture` (reads `0xC0`) that nothing configures. Never tested by hand |
| 20 | zram | ✅ `CONFIG_ZRAM=y`, `zram0` active at 512 MB |
| 21 | Suspicious logging at boot/shutdown | 📋 **cannot be inspected.** The `SMBCHG` charger driver evicts the ring buffer within ~45 min of a 9.7 h uptime, ~75% of the surviving log being its own messages (plus `of_batterydata_get_best_profile` re-parsing the battery profile 140 times). 78 s after a fresh boot the log already started at t=13.4 s. The journal would settle the userspace half but needs root |
| 22 | usb-moded vs Android USB in `*.rc` | ✅ `vendor.usb-hal-1-2` is disabled, and the `android_usb` writes left in `/vendor/etc/init/hw/init.qcom.usb.rc` sit behind `on property:sys.usb.config=*` triggers usb-moded never sets. The `iSerial`/`iManufacturer` writes are the sanctioned Android-side serial logic |
| 23 | Touch reporting | ⚠️ works in daily use, but the checklist's specific concern — display power cycling with a finger already on the screen — is untested |
| 24 | Act dead mode | ❓ `jolla-actdead-charging` installed and `actdead.target` exists, but act-dead has never been entered |
| 25 | Extra filesystems | 🔧 **exFAT was already there** — the first pass read the defconfig and missed it, but `sdfat` is built in with `CONFIG_SDFAT_USE_FOR_EXFAT` defaulting to `y`, and the device's `/proc/filesystems` lists both `sdfat` and `exfat`. The real gap was BTRFS, UDF, NFS, CIFS, ISO9660, SquashFS and NTFS, all now enabled in `karatep_defconfig`. NTFS is read-only deliberately; `mer_verify_kernel_config` goes 45 → 30 warnings with no new ones. BTRFS still does **not** make factory reset work → [rca](rca/factory-reset-does-nothing.md) |

Items 1, 2 and 17 are fixed in `droid-config-karatep` and **verified on hardware** — installed
into `/etc` by hand and then confirmed across a cold boot, with no service restarts involved, so
this is the real startup path and not a reload artefact:

```sh
# 1 -- was int32 -9999 on every sensor
dbus-send --system --print-reply --dest=com.nokia.thermalmanager \
  /com/nokia/thermalmanager com.nokia.thermalmanager.battery_temperature          # int32 38
  # ... .core_temperature                                                          # int32 42
  # ... .estimate_surface_temperature                                              # int32 37
  # ... .get_thermal_state                                                         # "normal"

# 2 -- was "unknown"
dbus-send --system --print-reply --dest=com.nokia.mce \
  /com/nokia/mce/request com.nokia.mce.request.get_memory_level                   # "normal"
  # get_config /system/osso/dsm/memnotify/{warning,critical}/used  -> 738000 / 830000

# 17 -- was 36 features including Feature_CoverSensor
ssu-sysinfo -f | grep -c CoverSensor                                              # 0, of 35 features
```

Each reading was checked against ground truth read at the same instant: battery `38` against
`/sys/class/power_supply/battery/temp` of 380-385 dC, core `42` against `thermal_zone11` of 42,
surface `37` as battery minus the configured 1 degree.

Two things worth knowing for anyone repeating this. The surface method is
`estimate_surface_temperature`, not `surface_temperature` — the latter simply does not exist and
returns nothing, which looks exactly like a rejected sensor. And `core_temperature` lags: sampled
25 s after boot it read 50 against a live 43, because dsme had polled during the boot heat spike
and `minwait` is 60 s. It converged to the sensor value by the next poll and tracked it exactly
from then on. A single disagreeing sample is not evidence of a bad mapping.

The three genuinely broken items are #3 (watchdog), #9 (suspend) and #21 (log spam); #25 is a
defconfig gap.

The porter archive has almost nothing on any of them (control search `hybris-boot`: 2883 hits,
so the search itself works). `memnotify` and `mempressure` both return **zero** hits, so the mce
side of #2 appears to be novel here. `qpnp-vadc` returns a single 2016 line — and it is about a
*wakelock* of that name, not a `suspend_noirq` veto, so #9 has no prior art either. The closest
match for the shape of #9 is Thaodan and voidanix[m] on 2022-08-21 debugging
`cnss_pci_suspend_noirq()` returning `-11`, which is a different driver.

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
