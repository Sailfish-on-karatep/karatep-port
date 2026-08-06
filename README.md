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

Bluetooth: the Qualcomm HAL aborts with `controller init failed` roughly every 61 s. Next after
that: the IMS daemon crash-loop, and mobile data.

### Recently fixed

| | |
|---|---|
| **No UI until `killall vndservicemanager`** | Early vendor services linked the system copy of `libbinder` (`SYST`) instead of the VNDK copy (`VNDR`), so every `/dev/vndbinder` lookup was rejected and the graphics composer crash-looped. Verified on hardware: composer starts once, no `Parcel` errors, lipstick comes up unaided. → [full analysis](docs/rca/vndservicemanager-libbinder.md) |
| **WLAN did not come up** | Two independent causes: `wlan.ko` must match the running kernel exactly (so droid-hal has to be rebuilt after *any* kernel change), and it must be loaded early by `wlan-module-load.service` — a late `modprobe` always returns `ENODEV`. → [details](docs/porting-notes.md#wlan) |
| **Build died on `external/chromium-webview`** | LineageOS' manifest links a file that no longer exists upstream, leaving a dangling symlink. The project is now dropped in [`manifests/local_manifests.xml`](manifests/local_manifests.xml). |
| **No boot splash** (black screen between the Lenovo logo and the UI) | hybris-boot's `HYBRIS_BOOTLOGO` draws the splash with `zcat > /dev/fb0`, which on this panel *always* fails with `ENODEV` — a device-tree/driver mismatch leaves `fb0` with no backing memory until something `mmap()`s it, and MDP5 scans out nothing without an explicit commit. Replaced with `hybris-boot/fbsplash.c`, a 4.4 KB freestanding helper doing `mmap` → `read` → `FBIOPAN_DISPLAY` that then holds the fd (closing it blanks the panel). It must **not** be linked against bionic. Verified on hardware. → [details](docs/porting-notes.md#boot-splash-needs-a-helper-hybris_bootlogos-own-mechanism-cannot-work) |

### Hardware

> ⚠️ **This table is stale and must not be relied on.** Hands-on testing has shown entries that
> are simply wrong in both directions — the loudspeaker was marked working and does not work. Treat
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
| Audio — loudspeaker | ✅ | |
| Audio — 3.5 mm | ❌ | jack detected, not routed |
| Audio — earpiece | ❓ | untested |
| Audio — Bluetooth | ❌ | |
| Bluetooth | ❌ | BT HAL aborts on "controller init failed"; `bluebinder` masked → [details](docs/porting-notes.md#bluetooth-broken) |
| WLAN | ✅ | `wlan0` up at boot, connman scans and lists APs |
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
