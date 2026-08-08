# Waydroid on karatep

Waydroid runs a full Android system in an LXC container and renders it through the host's
Wayland compositor, reusing the device's own `/vendor` HALs over binder. It is the community
alternative to Jolla's proprietary AlienDalvik.

> **Status: built, not yet verified on hardware.** Everything below the "Building" section is
> written from the packaging sources and the upstream OTA metadata, not from a booted device.
> There is also a known Sailfish OS 5.1 regression — see [The 5.1 problem](#the-51-problem)
> before spending time on it.

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

Two repos, both plain clones under `hybris/mw/` (not `repo`-managed, not forked — we carry no
changes of our own yet):

| Repo | Gives |
|---|---|
| [`sailfishos-open/waydroid`](https://github.com/sailfishos-open/waydroid) | `waydroid`, `waydroid-settings`, `waydroid-gbinder-config-{hybris,mainline}` |
| [`sailfishos-open/waydroid-sensors`](https://github.com/sailfishos-open/waydroid-sensors) | `waydroid-sensors` — a sensorfw↔gbinder bridge daemon |

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

We build **1.5.4**. Chum ships only **1.4.3+git3**, and 1.5.0+ is what handles the Android 13
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
rpm -Uvh waydroid-sensors-*.rpm waydroid-1.5.4*.rpm waydroid-settings-*.rpm
```

Our 1.5.4 outranks Chum's 1.4.3 in version comparison, so zypper will not pull theirs over ours.

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

### dnsmasq will probably conflict

`waydroid-container.service` runs its own dnsmasq on `192.168.250.1` and will fail with
`failed to create listening socket for 192.168.250.1: Address already in use` if one is already
running. Either `systemctl disable --now dnsmasq`, or uncomment `bind-interfaces` in
`/etc/dnsmasq.conf` and restart it. Then `systemctl restart waydroid-container`.

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

Two things to try, neither verified:

1. **`persist.waydroid.multi_windows`** — `waydroid prop set persist.waydroid.multi_windows true`.
   This changes the surface path entirely and is what b100dian pointed piggz at.
2. **`waydroid-runner`** — nested compositor rather than direct Lipstick rendering. If the
   regression really is a Lipstick protocol change, the nested path may sidestep it. This is an
   inference, not something any source states.

Read the forum report carefully before assuming the worst: *"no IP address assigned"* is exactly
what a kernel missing `CONFIG_VETH` / `xt_CHECKSUM` produces, so that post may be two unrelated
problems stacked. Our kernel has both.

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
