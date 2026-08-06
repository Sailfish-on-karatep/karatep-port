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

Camera video-mode crash, power management, and the unclean shutdown. Mobile data, calls/SMS and
SIM 2 are parked until a real SIM is available.

### Recently fixed

| | |
|---|---|
| **Enrolled fingerprints "lost" after a reboot; re-enrolment errors out** | karatep's FPC HAL fails `enumerate()` precisely when templates exist — the vendor library returns the count and the HIDL service treats non-zero as an error. The daemon therefore never loaded its finger map and reported nothing enrolled, while the TEE still held the templates, so re-enrolling the same finger was refused with `do_enroll finger already enrolled`. Nothing was ever lost. Fixed in the `sailfish-fpd-community` fork; `GetAll` returns both fingers again. → [details](docs/porting-notes.md#enrolled-fingerprints-vanish-after-a-reboot-and-re-enrolment-fails) |
| **No audio at all, on any output, plus no microphone** | Three independent faults stacked. A 21-byte `xpolicy.conf.d/fmradio.conf` stub broke `module-policy-enforcement`, so with `arm_droid_default.pa` defaulting to `sink.null`/`source.null` nothing was ever routed off them. This HAL has no `create_audio_patch`, so routing changes after stream open failed with `-ENOSYS` and streams stayed on the device they opened with — which is why only the loudspeaker ever worked. And the vendor policy config omits the built-in mics from the `primary input` route, so capture was opened as `AUDIO_SOURCE_VOICE_CALL` and refused. Loudspeaker, earpiece, 3.5 mm and all mics verified on hardware; Fluence dual-mic noise suppression enabled (+21 dB SNR, measured). → [details](docs/porting-notes.md#audio) |
| **Headset mic dead; 4-pole headsets seen as 3-pole** | The TS3A227E accessory-detection chip never ran a detection, so MBHC classified every headset as a headphone. An inherited LineageOS regression (`8d2f38f67c27`) had braced `-Wmisleading-indentation` around the wrong statements, leaving the `DET_TRIGGER` write unreachable after a `return`. → [details](docs/porting-notes.md#headset-detected-as-headphone-the-ts3a227e-never-ran-a-detection) |
| **No UI until `killall vndservicemanager`** | Early vendor services linked the system copy of `libbinder` (`SYST`) instead of the VNDK copy (`VNDR`), so every `/dev/vndbinder` lookup was rejected and the graphics composer crash-looped. Verified on hardware: composer starts once, no `Parcel` errors, lipstick comes up unaided. → [full analysis](docs/rca/vndservicemanager-libbinder.md) |
| **WLAN did not come up** | Two independent causes: `wlan.ko` must match the running kernel exactly (so droid-hal has to be rebuilt after *any* kernel change), and it must be loaded early by `wlan-module-load.service` — a late `modprobe` always returns `ENODEV`. → [details](docs/porting-notes.md#wlan) |
| **Build died on `external/chromium-webview`** | LineageOS' manifest links a file that no longer exists upstream, leaving a dangling symlink. The project is now dropped in [`manifests/local_manifests.xml`](manifests/local_manifests.xml). |
| **No boot splash** (black screen between the Lenovo logo and the UI) | hybris-boot's `HYBRIS_BOOTLOGO` draws the splash with `zcat > /dev/fb0`, which on this panel *always* fails with `ENODEV` — a device-tree/driver mismatch leaves `fb0` with no backing memory until something `mmap()`s it, and MDP5 scans out nothing without an explicit commit. Replaced with `hybris-boot/fbsplash.c`, a 4.4 KB freestanding helper doing `mmap` → `read` → `FBIOPAN_DISPLAY` that then holds the fd (closing it blanks the panel). It must **not** be linked against bionic. Verified on hardware. → [details](docs/porting-notes.md#boot-splash-needs-a-helper-hybris_bootlogos-own-mechanism-cannot-work) |

### Hardware

> ⚠️ **This table is stale and must not be relied on.** Hands-on testing has shown entries that
> are simply wrong in both directions. Treat
> every row below as *unverified*, including the ✅ ones, until it is re-tested. It is being replaced
> by an evidence-based `docs/feature-matrix.md`, where each status is backed by a log line, a config
> line, or a hands-on result.
>
> Note also that **cellular cannot currently be tested beyond SIM detection** — only a dummy SIM is
> available, so mobile data, SMS, calls, VoLTE and the SIM 2 status are unverifiable rather than
> broken. Camera flash is likewise unverified.

| Subsystem | Status | Notes |
|---|---|---|
| Display | ✅ | pixel ratio 1.6 |
| Touch | ✅ | |
| Graphics / UI | ✅ | `hwcomposer-2-1` + lipstick |
| Cameras (front & rear) | ⚠️ | work, but flaky |
| Camera flash | ❓ | untested |
| Audio — loudspeaker | ✅ | verified on hardware |
| Audio — 3.5 mm | ✅ | verified, both channels; needs `use_legacy_stream_set_parameters` |
| Audio — earpiece | ✅ | verified; no call needed, force `output-earpiece` via `output-parking` |
| Audio — Bluetooth | ✅ | A2DP verified by hand |
| Mic — built-in (AMIC1) | ✅ | `68: handset-mic` |
| Mic — secondary (AMIC3) | ✅ | `77: speaker-mic`; dual-mic Fluence enabled |
| Mic — headset (AMIC2) | ✅ | `85: headset-mic`, after the TS3A227E kernel fix |
| Bluetooth | ✅ | pairing + A2DP verified. The old "controller init failed" reading was wrong — the HAL logs `Init succeded`; the 61 s cycle was `bluebinder` racing WLAN for the shared WCNSS SoC. Fixed by `Type=oneshot` on `wlan-module-load.service` plus an ordering drop-in; `bluebinder` no longer masked, `NRestarts=0` |
| WLAN | ✅ | `wlan0` up at boot with the device's real MAC, associates, holds an IP and reaches the internet. **2.4 GHz only — hardware limit**, not a config gap. DNS needed a fix of its own: paranoid networking denied systemd-resolved (uid 997) any socket → [details](docs/porting-notes.md#associated-ip-address-gateway-reachable--and-every-name-lookup-fails) |
| WLAN — WPA3 | ⚠️ | WPA3-**transition** APs work (WPA2-PSK + PMF) after the prima `MFPEnabled` fix. WPA3-**only** cannot work: SAE needs `NL80211_CMD_EXTERNAL_AUTH` (Linux 4.17), absent on 3.18 → [details](docs/porting-notes.md#wpa3-transition-aps-ctrl-event-assoc-reject-status_code1) |
| WLAN hotspot | ❌ | |
| Cellular — signal (SIM 1) | ✅ | |
| Cellular — calls / SMS | ❓ | untested |
| Cellular — mobile data | ❌ | |
| Cellular — SIM 2 | ❌ | "Network: Denied" |
| VoLTE | ➖ | needs Jolla proprietary bits |
| GPS | ❓ | untested |
| Sensors | ⚠️ | rotation works; individual sensors unverified |
| Fingerprint (FPC 1020) | ✅ | enrolment and unlock verified on hardware (two templates enrolled, daemon cycles `IDENTIFYING`). Uses `sailfish-fpd-community` (Jolla's `sailfish-fpd` is unusable — no `sailfish-fpd-slave` exists for karatep) built from our fork (`Sailfish-on-karatep/sailfish-fpd-community`, `hybris-18.1`): this vendor HIDL 2.1 HAL never calls the enumerate callback when no templates exist, leaving the daemon wedged in `FPSTATE_ENUMERATING` forever, so `AndroidFP::enumerate()` arms a 3 s timeout and treats silence as "nothing enrolled". The same HAL quirk from the other side — `enumerate()` fails outright once templates *do* exist — made enrolled fingerprints appear lost after a reboot and blocked re-enrolment; also fixed in the fork. Enrolment, unlock and **removal** all verified. Root cause of every fingerprint fault here: the FPC vendor library returns the template *count* where `fingerprint.h` specifies 0, and the LineageOS HIDL adapter treated that as an error, so it never synthesised the `onEnumerate` callbacks and the daemon never learned a template id. Fixed in our fork of `android_hardware_lineage_interfaces`. **The rebuilt service must be installed onto `/vendor` by hand** — it is the one change an image build cannot deliver. → [details](docs/porting-notes.md#fingerprint-fpc-1020) |
| Vibration | ✅ | |
| Notification LED | ⚠️ | lights up, behaviour unverified |
| Keys — power, volume | ✅ | |
| Keys — back | ✅ | |
| Keys — home, nav | ✅ | home → app switcher, square → top menu. The keys come from the touch controller and always worked; lipstick just needed them declared to `ssu-sysinfo` → [details](docs/porting-notes.md#hardware-keys) |
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
| [`docs/hadk-compliance.md`](docs/hadk-compliance.md) | Chapter-by-chapter audit of this port against the HADK: what complies, what was fixed, and which deviations are deliberate. |
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

# 3. Android HAL (HABUILD SDK). HADK specifies `breakfast`, not `lunch`.
source build/envsetup.sh
export USE_CCACHE=1
breakfast karatep
make -j$(nproc --all) hybris-hal droidmedia

# 4. Droid HAL packaging (PLATFORM SDK, from $ANDROID_ROOT), in this order
rpm/dhd/helpers/build_packages.sh --droid-hal
rpm/dhd/helpers/build_packages.sh --configs
rpm/dhd/helpers/build_packages.sh --mw
rpm/dhd/helpers/build_packages.sh --gg
rpm/dhd/helpers/build_packages.sh --version

# 5. Root filesystem image. RELEASE must be exported or --mic refuses to run.
export RELEASE=5.1.0.11
rpm/dhd/helpers/build_packages.sh --mic
```

> **Check the boot image before flashing.** `Install: … hybris-boot.img` at the end of the
> Android build is not proof of success. hybris-boot builds its ramdisk with
> `find … | cpio -H newc -o | gzip -9`, and **the Jolla Ubuntu chroot does not ship `cpio`** —
> the pipeline then emits nothing, `gzip` still exits 0, and make happily writes an image with
> no `init`. Flashing it drops the device into Qualcomm bulk mode (`05c6:900e`) with no USB
> networking, so the installer waits forever for a recovery shell that never appears.
>
> ```sh
> ls -l out/target/product/karatep/{kernel,hybris-boot.img}
> # good: hybris-boot.img is ~1.5 MB larger than kernel
> # bad:  the difference is a few kilobytes -> no initramfs
> ```
>
> Fix once per chroot with `sudo apt-get install -y cpio`, then delete
> `out/target/product/karatep/obj/ROOT/hybris-{boot,recovery}_intermediates` — a 20-byte
> `boot-initramfs.gz` is otherwise considered up to date. Details in
> [`docs/porting-notes.md`](docs/porting-notes.md#cause-cpio-is-missing-from-the-habuild-chroot).

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
