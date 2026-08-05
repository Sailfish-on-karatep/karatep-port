# Sailfish OS for the Lenovo Vibe K6 Note / Plus (`karatep`)

An unofficial Sailfish OS **5.1.0.11 (Pispala)** hardware adaptation for the Lenovo Vibe K6
Note / Plus — Qualcomm **MSM8937** (Snapdragon 430), Adreno 505 — built on the
**LineageOS 18.1 / Android 11** hybris base, `aarch64` userspace, Linux 3.18.

> This is the documentation hub for the [Sailfish-on-karatep](https://github.com/Sailfish-on-karatep)
> organisation. Start here.

---

## Build status

**Boots to UI.** The device reaches the Sailfish OS home screen unaided.

### Currently working on

Rebuilding and reflashing with the linker-configuration fix
([`0042`](https://github.com/Sailfish-on-karatep/karatep-patches/blob/hybris-18.1/system/core/0042-hybris-finalise-linker-config-before-on-init-starts-.patch)),
so the UI comes up without the old `killall vndservicemanager` workaround. Next after that:
the IMS daemon crash-loop and the `bluebinder` / WLAN conflict.

### Recently fixed

| | |
|---|---|
| **No UI until `killall vndservicemanager`** | Early vendor services linked the system copy of `libbinder` (`SYST`) instead of the VNDK copy (`VNDR`), so every `/dev/vndbinder` lookup was rejected and the graphics composer crash-looped. → [full analysis](docs/rca/vndservicemanager-libbinder.md) |
| **Build died on `external/chromium-webview`** | LineageOS' manifest links a file that no longer exists upstream, leaving a dangling symlink. The project is now dropped in [`manifests/local_manifests.xml`](manifests/local_manifests.xml). |

### Hardware

| Subsystem | Status | Notes |
|---|---|---|
| Display | ✅ | pixel ratio 1.6 |
| Touch | ✅ | |
| Graphics / UI | ✅ | `hwcomposer-2-1` + lipstick |
| Cameras (front & rear) | ⚠️ | work, but flaky |
| Camera flash | ❓ | untested |
| Audio — loudspeaker | ✅ | |
| Audio — 3.5 mm | ❌ | jack detected, not routed |
| Audio — earpiece | ❓ | untested |
| Audio — Bluetooth | ❌ | |
| Bluetooth | ⚠️ | works only if `bluebinder` is started manually after boot |
| WLAN | ⚠️ | works only with `bluebinder` masked — the two conflict at boot |
| WLAN hotspot | ❌ | |
| Cellular — signal (SIM 1) | ✅ | |
| Cellular — calls / SMS | ❓ | untested |
| Cellular — mobile data | ❌ | |
| Cellular — SIM 2 | ❌ | "Network: Denied" |
| VoLTE | ➖ | needs Jolla proprietary bits |
| GPS | ❓ | untested |
| Sensors | ⚠️ | rotation works; individual sensors unverified |
| Fingerprint (FPC 1020) | ❌ | |
| Vibration | ✅ | |
| Notification LED | ⚠️ | lights up, behaviour unverified |
| Keys — power, volume | ✅ | |
| Keys — back | ✅ | |
| Keys — home, nav | ❌ | |
| USB — networking, data, charging | ✅ | |
| Power management | ❓ | untested |
| RTC alarms | ❓ | untested |
| FM radio | ❓ | untested |
| NFC | ➖ | no hardware |

✅ works · ⚠️ partial / needs a workaround · ❌ broken · ❓ untested · ➖ not applicable

---

## Where to find what

### In this repo

| Path | What it is |
|---|---|
| [`docs/flashing.md`](docs/flashing.md) | **Start here to install.** Step-by-step flashing guide, plus troubleshooting for the errors this device actually produces. |
| [`docs/porting-notes.md`](docs/porting-notes.md) | Accumulated device knowledge: partition map, fixes already in the tree, known workarounds, how to debug the boot. |
| [`docs/rca/`](docs/rca/) | Root-cause write-ups for bugs that were properly diagnosed. |
| [`docs/useful-commands.md`](docs/useful-commands.md) | Short command reference (rebooting to fastboot/recovery from Sailfish, etc.). |
| [`manifests/local_manifests.xml`](manifests/local_manifests.xml) | The `repo` local manifest. Copy to `$ANDROID_ROOT/.repo/local_manifests/`. |
| [`scripts/flash.sh`](scripts/flash.sh) | Semi-automated flasher; discovers the USB network address itself. |

### Other repos in the organisation

| Repo | Role |
|---|---|
| [`karatep-patches`](https://github.com/Sailfish-on-karatep/karatep-patches) | The four Android-source patches that **cannot** be carried as a fork, because they are written against the tree *after* `mer-hybris/hybris-patches` has been applied. Applied as a second pass. Upstream's series is synced normally and is not forked. |
| [`hybris-boot`](https://github.com/Sailfish-on-karatep/hybris-boot) | Fork of `mer-hybris/hybris-boot` carrying the karatep entry in `fixup-mountpoints`. |
| [`android_device_lenovo_karatep`](https://github.com/Sailfish-on-karatep/android_device_lenovo_karatep) | Device tree. |
| [`android_device_lenovo_karate-common`](https://github.com/Sailfish-on-karatep/android_device_lenovo_karate-common) | Shared `karate` family device tree (`fstab.qcom` lives here). |
| [`android_kernel_lenovo_msm8937`](https://github.com/Sailfish-on-karatep/android_kernel_lenovo_msm8937) | Kernel 3.18 + `karatep_defconfig`. |
| [`proprietary_vendor_lenovo`](https://github.com/Sailfish-on-karatep/proprietary_vendor_lenovo) | Vendor blobs. |
| [`droid-hal-karatep`](https://github.com/Sailfish-on-karatep/droid-hal-karatep) | `droid-hal` packaging (`rpm/`). |
| [`droid-config-karatep`](https://github.com/Sailfish-on-karatep/droid-config-karatep) | Sailfish-side device configuration, `sparse/` overlay, patterns, kickstart. |
| [`droid-hal-version-karatep`](https://github.com/Sailfish-on-karatep/droid-hal-version-karatep) | Version package. |

---

## Building

Follow the [HADK](https://hadk.sailfishos.org/) — this port adds nothing unusual to the
procedure. In outline, with `$ANDROID_ROOT` set:

```sh
# 1. Sources. local_manifests.xml is the only karatep-specific step: it repins the
#    device trees, kernel, vendor blobs, droid-hal/-config and hybris-boot at the
#    Sailfish-on-karatep forks, and adds karatep-patches.
repo init -u https://github.com/mer-hybris/android.git -b hybris-18.1
cp manifests/local_manifests.xml $ANDROID_ROOT/.repo/local_manifests/karatep.xml
repo sync

# 2. Patches, in this order. Nothing is ever edited in the tree by hand.
hybris-patches/apply-patches.sh --mb       # mer-hybris' series (plain HADK step)
karatep-patches/apply-patches.sh --mb      # the four karatep patches, on top

# 3. Android HAL (HABUILD SDK)
source build/envsetup.sh && lunch lineage_karatep-userdebug
make -j$(nproc) hybris-hal droidmedia

# 4. Sailfish packages and image (PLATFORM SDK). Set RELEASE first — build_packages.sh -i
#    refuses without it; this port uses RELEASE=5.1.0.11.
rpm/dhd/helpers/build_packages.sh
```

Everything device-specific is either a fork repinned in `local_manifests.xml` (the HADK's
"Contribute your mods back" pattern) or one of the four patches in `karatep-patches`. There is
no other local state, so the same two files reproduce the tree on any machine.

Then see [`docs/flashing.md`](docs/flashing.md).

---

## Credits

Built on the work of the Sailfish OS porters community — particularly **@mal** and
**@elros34** on `#sailfishos-porters`, whose advice is cited throughout
[`docs/porting-notes.md`](docs/porting-notes.md).

Sailfish OS is a product of [Jolla](https://jolla.com/). This is an unofficial community
adaptation with no affiliation or warranty.
