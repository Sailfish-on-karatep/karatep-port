# Waydroid on karatep

Waydroid runs a full Android system in an LXC container and renders it through the host's
Wayland compositor, reusing the device's own `/vendor` HALs over binder. It is the community
alternative to Jolla's proprietary AlienDalvik.

> **Status: running on hardware, with gaps.** The container boots Android, touch works, and it
> survives a display-size change. Both cameras enumerate inside it and the camera app opens them.
> Hardware video decode and encode work, GPU acceleration is the real Adreno driver, the
> microphone records, and all eight of the host's sensors are bridged through.
>
> Getting here took five fixes: `CONFIG_DEVPTS_MULTIPLE_INSTANCES` for the container to start at
> all ([rca](rca/waydroid-devpts.md)); taking the `wl_shell` path instead of xdg-shell, which is
> what the 5.1 regression actually was ([rca](rca/waydroid-touch-xdg-shell.md)); `cgroup.clone_children`
> so the container stops killing the *host's* camera ([rca](rca/waydroid-poisons-host-cgroups.md));
> staging the host's camera HAL under the name the container looks for
> ([rca](rca/waydroid-camera-hal-name.md)); and feeding the device's vendor video properties back
> into the container, without which **no video plays at all**
> ([rca](rca/waydroid-video-decode-split-mode.md)).
>
> Still open: `configureStreams` rejects the JPEG stream — the install pairs a lineage-20
> (Android 13) system image with a lineage-18.1 (Android 11) HALIUM_11 vendor, so an Android 13
> framework is negotiating with a `camera.device@3.3` HAL. Per-app network accounting and data
> restrictions are permanently unavailable (eBPF needs a 4.9+ kernel). GPS now works through a bridge to the
> host's positioning stack, and Bluetooth is not exposed to the container.

---

## Feature status

Measured on the device, 2026-08-09, container up 17 h.

| Feature | State | Evidence / note |
|---|---|---|
| Touch | ✅ | `wayland_touch` at input device 5; survives a display-size change |
| GPU acceleration | ✅ | Real driver, not swiftshader: `GLES: Qualcomm, Adreno (TM) 505, OpenGL ES 3.2 V@0502.0`, `ro.hardware.egl=adreno`. Waydroid bind-mounts the host's `/vendor/lib{,64}/egl` into the container |
| Video decode (HW) | ✅ | `OMX.qcom.video.decoder.avc`, 1080p, after the split-mode fix — [rca](rca/waydroid-video-decode-split-mode.md) |
| Video encode (HW) | ✅ | `OMX.qcom.video.encoder.avc`; recordings are valid 1080p H.264 Baseline / AAC |
| Camera (enumerate + preview) | ✅ | 2 devices, `ICameraProvider/legacy/0`, provider stable — [rca](rca/waydroid-camera-hal-name.md) |
| Camera (JPEG capture stream) | ❌ | `configureStreams: Stream 1: DataSpace override not allowed for format 0x21` — Android 13 framework vs `camera.device@3.3` |
| Microphone | ✅ | Container recording contains real captured audio (296 960 samples, mean −53.5 dB, max −32.9 dB — not digital silence) |
| Audio out | ✅ | `android.hardware.audio@4.0::IDevicesFactory` up, routed to the host's PulseAudio via `waydroid.pulse_runtime_path=/run/xdg/pulse` |
| Headset / BT SCO / A2DP routing | ⚠️ | The container's audio policy declares wired headset, headphones, BT SCO and A2DP ports, but all of them are Waydroid's *stub* devices — the real routing decision belongs to the host's PulseAudio, so Android's in-container routing UI is cosmetic. Untested with a headset plugged in |
| Sensors | ✅ | 8/8 bridged by `waydroid-sensors` through Sailfish `sensorfw`: accelerometer, gyroscope, light, magnetometer (+uncalibrated), device orientation, pressure, proximity. Humidity / step counter / temperature correctly report "not found" — the device has none |
| Vibration | ✅ | `android.hardware.vibrator@1.0-service.waydroid`, `mVibratorInfoLoadSuccessful=true`, TOUCH vibrations logged `status: finished` from launcher and virtual keys. No amplitude control (`mCapabilities=[]`) |
| Networking | ✅ | veth up, `192.168.240.112`, ping and DNS out to the internet both work |
| Per-app network accounting / data saver | ❌ | **Structurally impossible here.** Android 13's `netd` does this with eBPF; kernel 3.18 has no `BPF_PROG_TYPE_CGROUP_SKB`, so every call returns `Function not implemented (code 38)`. Constant `NetworkStats` / `NetworkPolicy` / `TrafficController` log spam is this and only this. Traffic itself is unaffected |
| GPS | ✅ | Works, via [`waydroid-gnss`](https://github.com/Sailfish-on-karatep/waydroid-gnss) bridging the container's `IGnss` to the host's Geoclue, so Sailfish keeps the HAL and both stacks can position at once. The container previously reached GPS by *seizing* the host HAL through `hosthals.xml` → [design](waydroid-gps-bluetooth.md#gps--solved-via-a-bridge-to-the-hosts-positioning-stack) |
| Bluetooth | ❌ | Not fixable at this layer: the Waydroid system image ships **no Bluetooth stack at all** (zero packages of 152, no APK, no APEX), and the HCI transport is already exclusively held by `bluebinder` → BlueZ → [analysis](waydroid-gps-bluetooth.md#bluetooth--two-independent-blockers) |
| Clipboard | ⚠️ | `vendor.waydroid.clipboard@1.0::IWaydroidClipboard` is registered; host↔container copy/paste not exercised |
| `/dev/video` bind | ⚠️ | LXC logs `Failed to mount "/dev/video" onto ".../dev/video"` and leaves a 0-byte regular file there, because on karatep `/dev/video` is a *directory* of `venus_dec` / `venus_enc` symlinks. Harmless in practice — the OMX components open `/dev/video32` and `/dev/video33` directly, and both work |

---

## Why karatep is a good fit

This is the one axis where a 2016 Snapdragon 430 has an advantage over newer ports.

Waydroid's vendor images are published per Halium generation, and `initializer.py:get_vendor_type()`
picks one from `ro.vndk.version`:

```
HALIUM_<vndk - 19>      # vndk 30 (Android 11) -> HALIUM_11
```

karatep is LineageOS 18.1 / Android 11, so it resolves to **`HALIUM_11`**, which upstream still
builds — `lineage-18.1-20260402-HALIUM_11-waydroid_arm64-vendor.zip` as of April 2026. The system
image on top is `lineage-20.0` (Android 13).

Ports on newer bases are the ones that struggle. From `#sailfishos-porters`:

| Date | Who | What |
|---|---|---|
| 2026-05-02 | mal | *"android 14 base is too new for current waydroid"* |
| 2026-05-02 | mal | *"it seems waydroid is still lineage 20 based which is a problem for fairphone ports"* |
| 2025-08-29 | deathmist | *"you can't go backwards, if your vendor is newer you must also update the OS"* |

An Android 11 vendor is exactly what waydroid's Halium path targets. Nothing here needs the
`libhidlbase` symlink hacks that the A13/A14 ports resort to.

---

## Kernel

Waydroid needs a second set of binder nodes plus container networking. Added to
`karatep_defconfig` in `android_kernel_lenovo_msm8937`:

```
CONFIG_ANDROID_BINDER_DEVICES="binder,hwbinder,vndbinder,puddlejumper,hwpuddlejumper,vndpuddlejumper"
CONFIG_VETH=y
CONFIG_NETFILTER_XT_TARGET_CHECKSUM=y
CONFIG_NET_CLS_CGROUP=y
CONFIG_CGROUP_NET_CLASSID=y
```

A second round was needed once the container actually tried to start (`7b25d38ec43e`):

```
CONFIG_DEVPTS_MULTIPLE_INSTANCES=y   # required -- LXC cannot build the container's /dev without it
CONFIG_OVERLAY_FS=y                  # writable /system and /vendor
CONFIG_CGROUP_DEVICE=y               # mer-kernel-check, systemd
CONFIG_MEMCG=y
CONFIG_MEMCG_KMEM=y
```

`DEVPTS_MULTIPLE_INSTANCES` is the one that matters: without it there is no `/dev/pts/ptmx` for
LXC to bind, the container never spawns, and the app just closes. Full write-up in
[`rca/waydroid-devpts.md`](rca/waydroid-devpts.md).

`POSIX_MQUEUE` was considered and dropped — neither Android's `init.rc` nor waydroid's
`lxc.mount.auto` touches `/dev/mqueue`. `MACVLAN`, `VLAN_8021Q`, `IP6_NF_TARGET_MASQUERADE`,
`CHECKPOINT_RESTORE` and the `*_DIAG` options are all things `lxc-checkconfig` will flag and none
of them are used: waydroid networks over veth, and `waydroid-net.sh:89` only reaches for
ip6tables when IPv6 is enabled, which it is not by default.

`lxc-checkconfig` is the right tool for this, with one catch: it shells out to `zgrep`, which the
device does not have, so every line reports "missing". Decompress first —
`zcat /proc/config.gz > /run/kconfig && CONFIG=/run/kconfig lxc-checkconfig`.

The binder nodes **must** be static. Linux 3.18 predates binderfs (5.0), so waydroid's
`allocBinderNodes()` path — which adds nodes at runtime by `ioctl`-ing
`/dev/binderfs/binder-control` — cannot work here. `probeBinderDriver()` then finds the nodes
already present in `/dev` and uses them. The `puddlejumper` naming is not arbitrary: it is the
second entry in each of waydroid's `BINDER_DRIVERS` / `VNDBINDER_DRIVERS` / `HWBINDER_DRIVERS`
lists in `tools/helpers/drivers.py`.

Four options the upstream `check-kernel-config.sh` also wants were **already on** in the generated
`.config` even though they are absent from the defconfig — `SW_SYNC_USER`, `DEBUG_FS`, `BRIDGE`,
`FUSE_FS`. `SW_SYNC_USER` is the one that costs other porters days; check the built `.config`, not
the defconfig, before concluding anything is missing.

After any kernel change, **rebuild droid-hal** (`build_packages.sh -d`) — it ships `wlan.ko`, which
must match the running kernel exactly or `wlan0` never appears.

---

## Packages

Two repos, both plain clones under `hybris/mw/` (not `repo`-managed):

| Repo | Gives |
|---|---|
| [`Sailfish-on-karatep/waydroid`](https://github.com/Sailfish-on-karatep/waydroid) — our fork, branch `hybris-18.1`, `upstream` = `sailfishos-open` | `waydroid`, `waydroid-settings`, `waydroid-gbinder-config-{hybris,mainline}` |
| [`sailfishos-open/waydroid-sensors`](https://github.com/sailfishos-open/waydroid-sensors) | `waydroid-sensors` — a sensorfw↔gbinder bridge daemon |

### Why `waydroid` is forked

`sailfishos-open` has been pinned to 1.5.4 since June 2025 and upstream is 73 commits further
on, at **1.6.3**. The fork bumps the submodule and carries a third patch. What 1.6.x brings that
we want: the **notification manager** (Android notifications surfaced on the host — started from
`session_manager.py:109`, independently of `user_manager`, so the fork's patch 0001 does not
disable it), `waydroid bugreport`, correct handling of an Android-side reboot or shutdown,
logfile rotation, and a run of init/DBus race fixes.

Nothing in the packaging had to move: `make install` gains four files and they all land under
`%{_prefix}/lib/waydroid`, which `%files` already globs. Patch 0001 needed a context-only rebase.

**`mb2` takes the version from `git describe`, not from the spec.** Bumping `Version:` to 1.6.3
alone produced `waydroid-1.5.4+git1+hybris.18.1.…`, because the newest reachable tag was still
`1.5.4+git1` — and that RPM would have been *refused* as an upgrade, since `hybris` sorts before
`main`. Tag the fork (`1.6.3+git1`, matching sailfishos-open's own convention) before building.

### Patch 0003 — overlays on a pre-4.0 kernel

`mount.py` joins the lower layers with `:`, but overlayfs only learned to stack several lower
layers in 4.0. On 3.18 `lowerdir=a:b` is looked up as one literal path, the mount fails,
`images.py:167` writes `mount_overlays = False` into `waydroid.cfg` and the port silently loses
its writable `/system` and `/vendor`. The patch folds the extra lower layers into the upper layer
when the kernel is too old. See [`rca/waydroid-devpts.md`](rca/waydroid-devpts.md#also-found-same-investigation).

### Patch 0004 — propagate the host's vendor video properties

A Halium container mounts a generic vendor image over `/vendor` and rbinds the device's real
vendor partition at `/vendor_extra`, so it keeps loading the device's own vendor *libraries*
while losing the vendor *properties* that tell those libraries what this SoC's firmware can do.
On karatep the missing `vendor.vidc.disable.split.mode=1` made `libOmxVdec` ask Venus for a
split DPB/OPB it cannot do, and **no video decoded at all**. The patch adds the `vendor.vidc.` /
`vidc.` namespaces to the host properties `make_base_props()` already copies across. Values are
copied, never hardcoded, so it is a no-op on a device that sets none — nothing about it is
specific to karatep or to `hybris-18.1`. Full write-up in
[`rca/waydroid-video-decode-split-mode.md`](rca/waydroid-video-decode-split-mode.md).

It takes effect at `waydroid init` / `waydroid upgrade`, **not** at session start, so an existing
install needs `waydroid upgrade -o` once after the package is installed.

Both carry upstream as a **git submodule**, and both do their patching with
`%autosetup -p1 -n %{name}-%{version}/upstream`.

### mb2 never runs `%prep` — patches are silently skipped

This is the trap, and it is not device-specific.

`mb2` builds the working tree **in place**. The build log jumps straight to `Executing(%build)`
with the working directory already inside `upstream/` — nothing unpacks a tarball, so `%autosetup`
never runs and **every `PatchN` in the spec is skipped**. OBS does not hit this because its
`tar_git` service hands `rpmbuild` a real tarball and `%prep` proceeds normally.

The visible symptom is `waydroid-sensors` failing to compile:

```
sensorfw-core/utils/dbus_connection_handle.cpp:52:22: error: 'uint32_t' does not name a type
sensorfw-core/utils/dbus_connection_handle.cpp:65:37: error: 'DBUS_NAME_FLAG_DO_NOT_QUEUE' was not declared in this scope
```

All six errors are one cause: no `#include <cstdint>`, so the two `static constexpr uint32_t`
constants inside `request_name()` never get declared. That is precisely what the repo's own
`rpm/001-cstdint.patch` fixes — it was there all along and simply never applied.

`bin/build-waydroid-rpm.sh` therefore applies each repo's `rpm/*.patch` into the submodule tree
itself before building, doing a `git checkout -- .` first so a rebuild never stacks a patch on
itself. The `waydroid` package's two patches (disable the systemd user manager, drop the apparmor
reference) were being skipped the same way.

### Do not install the gbinder-config packages

The repo README tells you to install `waydroid-gbinder-config-hybris` and says a config file is
required for libgbinder < 1.1.20. **That advice is years out of date.** karatep ships libgbinder
**1.1.45**, which negotiates the binder protocol at runtime. deathmist, 2025-03-26:

> editing gbinder config files should be avoided at all cost when it comes to running waydroid
> […] this advice is over 2 years outdated

> editing libgbinder config is an easy way to make waydroid not work anymore if it receives any
> OS update

The two subpackages are still built (they come from the same spec) — just never install them, and
do not hand-write anything into `/etc/gbinder.d/`.

---

## Building

```sh
/opencloud/bin/sfossdk /opencloud/bin/build-waydroid-rpm.sh
```

Refreshes both clones **and their submodules**, applies the packaging patches, then builds each
spec with `build_packages.sh --build=`. Output lands in `droid-local-repo/karatep/`.

The `--recurse-submodules` on the pull matters: a plain `git pull` advances the superproject's
recorded submodule commit without checking the submodule out, so `upstream/` stays at the old
revision and the build quietly produces the *previous* waydroid version.

We build **1.6.3**. Chum ships only **1.4.3+git3**, and 1.5.0+ is what handles the Android 13
system images the OTA channel now serves (deathmist, 2025-03-26). Prefer ours.

### Not in the image

`waydroid` is deliberately **not** in `patterns-sailfish-device-adaptation-karatep.inc`. Pulling it
in would drag `dnsmasq` and `python3-gbinder` into the image, both of which live in Chum, which is
not in the kickstart's repo list — a change not worth making while the 5.1 regression below is
unresolved. Install on-device instead.

---

## Installing on the device

Runtime dependencies come from three places:

| From | Packages |
|---|---|
| Jolla 5.1.0.11 repo | `lxc` (6.0.3+git2), `python3-dbus`, `python3-gobject` |
| Chum `5.1_aarch64` | `dnsmasq`, `python3-gbinder` |
| `droid-local-repo/karatep` | `waydroid`, `waydroid-settings`, `waydroid-sensors` |

```sh
devel-su
ssu ar chum https://repo.sailfishos.org/obs/sailfishos:/chum/5.1_aarch64/
zypper ref
zypper in lxc dnsmasq python3-gbinder python3-gobject python3-dbus
# then the locally built RPMs, copied to the device:
rpm -Uvh waydroid-sensors-*.rpm waydroid-1.6.3*.rpm waydroid-settings-*.rpm
```

Our 1.6.3 outranks Chum's 1.4.3 in version comparison, so zypper will not pull theirs over ours.

A reflash destroys all of this: `hybris-updater-unpack.sh:6` does `rm -rf /data/.stowaways/sailfishos`,
which takes `/home/waydroid` and its ~2 GB of images with it. Rename it out of the way from the
recovery shell first — it is the same filesystem, so this costs nothing:

```sh
mv /data/.stowaways/sailfishos/home/waydroid /data/waydroid-keep
# ... install, boot, reinstall the packages, then ...
rm -rf /home/waydroid && mv /data/waydroid-keep /home/waydroid
```

Then, as root:

```sh
waydroid init          # downloads ~1 GB; see `waydroid init -h` for image choices
```

The spec symlinks `/var/lib/waydroid` → `/home/waydroid`, so the images land on the home partition
rather than the small rootfs.

Reboot — `waydroid-container.service` is enabled by `%post` and starts on the next boot. Then either:

- **Settings** → *Waydroid* → start the **Session** service, then launch Waydroid from the
  launcher for the full-screen UI (renders directly on Lipstick); or
- install `waydroid-runner`, which starts a session inside its **own nested Wayland compositor**.
  Slower, but it does not depend on the Lipstick path.

### dnsmasq conflicts — confirmed on hardware

Waydroid needs the dnsmasq **binary**: `waydroid-net.sh start` runs its own instance bound to
`192.168.240.1` to serve the container's DHCP and DNS. Installing the `dnsmasq` RPM as a
dependency also drops in a system-wide `dnsmasq.service`, which the systemd preset enables and
which binds `0.0.0.0:53`. Waydroid's instance then cannot bind:

```
dnsmasq: failed to create listening socket for 192.168.240.1: Address already in use
Failed to setup waydroid-net.
RuntimeError: Command failed: % /usr/lib/waydroid/data/scripts/waydroid-net.sh start
```

Note *where* this fires: not at `waydroid-container.service` start — that service is only the
ContainerManager D-Bus daemon, and it comes up fine. The LXC container starts on
**`waydroid session start`**, and that is what aborts, leaving `waydroid0` non-existent and the
session silently dead. This was almost certainly behind the "no internet in the container"
symptom too.

Fix:

```sh
systemctl disable --now dnsmasq.service
```

The binary stays; only the system-wide daemon goes. `prebuilts/waydroid/install.sh` now does this
automatically after installing the RPMs.

---

## The 5.1 problem

**Waydroid is reported broken on Sailfish OS 5.1, which is the release this port targets.**

| Date | Source | What |
|---|---|---|
| 2026-05-27 | adampigg, IRC | *"5,1 appears to break waydroid :/"* |
| 2026-05-28 | adampigg, IRC | *"waydroid issue seems to be some protocol issue in new lipstick. Not sure yet if a fix is coming"* |
| 2026-05-27 | b100dian, IRC | suggests trying *"the (once forgotten) multi-window switch"* |
| 2026-07-28 | j_sarkari, [forum](https://forum.sailfishos.org/t/waydroid-on-sfos/7442/306) | Fairphone 5 on **5.1.0.11**: stuck at *"Waiting for Android UI"*, no IP assigned |

No fix has been published anywhere findable. The IRC archive's coverage appears to stop around
2026-05-30, so later discussion may exist that is not greppable.

**Solved.** The cause is the shell protocol, not lipstick being broken in general.

Waydroid's Android-side client prefers xdg-shell whenever the compositor advertises
`xdg_wm_base`, falling back to `wl_shell` only if it does not. Lipstick gained an xdg-shell
implementation in [sailfishos/lipstick 4b9745ef](https://github.com/sailfishos/lipstick/commit/4b9745ef)
("Add XDG shell support", 2026-03-30), which shipped in 5.1 — so from 5.1 Waydroid switched
itself onto a path lipstick renders but does not route touch on. That commit says outright it
implements "only the basic parts needed to show maximized toplevel windows and position popups".

Isolated by comparing compositors rather than by reasoning. `scripts/waydroid/wlinfo.py` shows
lipstick advertising `xdg_wm_base v6`, while `waydroid-runner`'s nested compositor is built on
`libQt5Compositor.so.5`, which contains no xdg-shell at all and therefore cannot advertise it.
Same client, same device, same seat, same container — touch works under the one that offers only
`wl_shell`. That also corrects the folklore that `waydroid-runner` works *because* it is nested:
the nesting is incidental, it works because its compositor is too old to speak xdg-shell.

The fix is in our `android_hardware_waydroid` fork: do not bind `xdg_wm_base` unless
`persist.waydroid.prefer_xdg_shell=true`, so the client takes the `wl_shell` path Sailfish speaks
natively. Fractional scaling is gated the same way, since lipstick's scale override arrived with
its xdg-shell work and applies to xdg surfaces.

**Removing xdg-shell from lipstick is not an option**, tempting as it looks:
[PR #68](https://github.com/sailfishos/lipstick/pull/68) added it specifically to run GTK apps
under Flatpak, and the porter archive shows it had been wanted since 2019 and was blocked on Qt
being too old. Disabling it would trade Waydroid for every foreign app on the platform.

Read the earlier forum report carefully before assuming the worst: *"no IP address assigned"* is
exactly what a kernel missing `CONFIG_VETH` / `xt_CHECKSUM` produces, so that post may be two
unrelated problems stacked. Our kernel has both.

### What to capture when it fails

```sh
waydroid log                                    # the container's own log
waydroid status
journalctl -u waydroid-container -b
systemctl --user status waydroid-session
dmesg | grep -i binder
ls -l /dev/*puddlejumper                        # all three nodes must exist
ip addr show waydroid0                          # the veth pair and its address
```

`Failed to get service waydroidplatform` and
`Could not find 'vendor.waydroid.window@1.2::IWaydroidWindow/default'` are the two signatures
that have shown up on other ports when the UI never appears (edp_17, 2025-02-15).

---

## Expectations

Snapdragon 430 / Adreno 505 running an Android 13 system image over an Android 11 vendor. Nobody
has benchmarked this SoC class with Waydroid. Expect it to be slow; that is a caveat, not a fault.
Budget ~1 GB on `/home` for the images.
