# Root cause: `vndservicemanager` boot hang (no UI until `killall vndservicemanager`)

Device: **lenovo/karatep** — Lenovo Vibe K6 Note / Plus, **MSM8937 / Snapdragon 430**, Adreno 505.
Base: LineageOS 18.1 (Android 11) / `hybris-18.1`, aarch64, Sailfish OS 5.1.0.11.

Status: **fixed** by `karatep-patches/system/core/0042-hybris-finalise-linker-config-before-on-init-starts-.patch`.
Verified on hardware.

---

## Symptom

The device boots, `systemd` reaches `graphical.target`, but no UI ever appears.
Running `killall vndservicemanager` from the debug shell (`telnet 192.168.2.15 2323`)
makes the UI come up within a minute or so.

This made `vndservicemanager` look like the culprit. **It is not.** It is healthy —
it is speaking the wrong dialect.

---

## What actually happens

### 1. `libbinder` exists in two incompatible flavours

`frameworks/native/libs/binder/Parcel.cpp`:

```c
#if defined(__ANDROID_VNDK__) && !defined(__ANDROID_APEX__)
constexpr int32_t kHeader = B_PACK_CHARS('V', 'N', 'D', 'R');   // 0x564e4452
#else
constexpr int32_t kHeader = B_PACK_CHARS('S', 'Y', 'S', 'T');   // 0x53595354
#endif
```

`Parcel::writeInterfaceToken()` writes `kHeader`; `Parcel::enforceInterface()` rejects
the transaction if the header does not match its own:

```c
if (header != kHeader) {
    ALOGE("Expecting header 0x%x but found 0x%x. Mixing copies of libbinder?", kHeader, header);
    return false;
}
```

So a **system** `libbinder` (`/system/lib64/libbinder.so`, `SYST`) and a **VNDK**
`libbinder` (`/apex/com.android.vndk.v30/lib64/libbinder.so`, `VNDR`) cannot talk to
each other. Every transaction between them is rejected.

### 2. hybris deletes the mechanism that keeps the two apart

Stock Android separates them with **mount namespaces**. Services started from `on init`
run in the *bootstrap* namespace and see a minimal `/linkerconfig/ld.config.txt`;
init later switches to the *default* namespace, where `/linkerconfig` is re-bound to the
full, APEX-aware configuration.

hybris compiles that machinery out — see the applied patches in `system/core` (referenced by
patch number, since the commit hashes change every time `apply-patches.sh` re-applies them):

* `system/core/0034-hybris-Do-not-SetupMountNamespaces.patch` (upstream mer-hybris)
* `system/core/0040-hybris-linkerconfig-switch-for-no-updatable-apex.patch` (elros34) — wraps
  the namespace logic in `#ifdef DISABLED_FOR_HYBRIS_SUPPORT` and adds `SetupFlattenedApexes()`
* `system/core/0041-hybris-always-regenerate-the-linker-configuration.patch` — makes
  `do_update_linker_config()` always regenerate, which it otherwise never does because
  `ro.apex.updatable` is unset on this device

With namespaces gone, the **only** thing that swaps `/linkerconfig` from the bootstrap
config to the default one is `enter_default_mount_ns` →
`MountLinkerConfigForDefaultNamespace()`:

```c
if (access("/linkerconfig/default", 0) != 0) return {};
mount("/linkerconfig/default", "/linkerconfig", nullptr, MS_BIND | MS_REC, nullptr);
```

and that lives in **`on post-fs-data`** (`init.rc:554`), followed by
`perform_apex_config` (`init.rc:757`) which regenerates the config into it.

### 3. `on init` runs before `on post-fs-data`

`init.rc:398-400`, in `on init`:

```
    start servicemanager
    start hwservicemanager
    start vndservicemanager
```

Measured boot timeline (persistent journal, monotonic):

| time | event |
|---|---|
| 12.46s | APEXes activated (incl. `com.android.vndk.v30`) |
| 12.66s | `on early-init` — bootstrap `ld.config.txt` generated, `update_linker_config` |
| 13.34s | `on init` begins (`init.rc:115`) |
| **13.46s** | **`starting service 'vndservicemanager'`** ← still bootstrap linker config |
| 14.02s | `on post-fs-data` begins → `enter_default_mount_ns`, `perform_apex_config` |

So `vndservicemanager` — and every other early vendor service — links against
`/system/lib64/libbinder.so` (`SYST`).

### 4. Measured proof

Correlating each process's start time with the `libbinder` it mapped shows a sharp
cutoff exactly at the `post-fs-data` boundary:

| start | process | libbinder |
|---|---|---|
| 13s | `vndservicemanager` | `/system/lib64` ❌ |
| 13s | `android.hardware.sensors@1.0-service` | `/system/lib64` ❌ |
| 14s | `android.hardware.graphics.allocator@2.0-service` | `/system/lib64` ❌ |
| 14s | `android.hardware.audio.service` | `/apex/…vndk.v30` ✅ |
| 14s | `drm`, `gnss`, `rild`, `netmgrd`, `display.color`, … | `/apex/…vndk.v30` ✅ |

Same binary, same arguments, same `ld.config.txt` (mtime unchanged), same mount
namespace, same environment — only the start time differs:

```
PID 1509 (started 13s)   -> /system/lib64/libbinder.so
killall vndservicemanager
PID 11312 (restarted)    -> /apex/com.android.vndk.v30/lib64/libbinder.so
```

### 5. Why that stops the UI

`vndservicemanager` is the context manager for `/dev/vndbinder`. Speaking `SYST` while
every vendor client speaks `VNDR` means **every** lookup on `/dev/vndbinder` is rejected:

```
1509  E Parcel        : Expecting header 0x53595354 but found 0x564e4452. Mixing copies of libbinder?
5849  I ServiceManager: Waiting for service 'display.qservice' on '/dev/vndbinder'...
5865  I ServiceManager: Waiting for service 'vendor.qcom.PeripheralManager' on '/dev/vndbinder'...
```

`android.hardware.graphics.composer@2.1-service` never resolves `display.qservice`,
so it exits(1) and init restarts it every ~5s forever:

```
droid-hal-init: Service 'vendor.hwcomposer-2-1' (pid 1781) exited with status 1
droid-hal-init: starting service 'vendor.hwcomposer-2-1'...
        ... repeats indefinitely ...
hwservicemanager: Since android.hardware.graphics.composer@2.1::IComposer/default is
                  not registered, trying to start it as a lazy HAL.
```

No composer → no display → `lipstick` never gets a UI.

**Killing `vndservicemanager` works** because init respawns it *after*
`post-fs-data`, so the respawn links the VNDK `libbinder`, starts speaking `VNDR`,
and the composer immediately succeeds.

---

## The fix

`karatep-patches/system/core/0042-hybris-finalise-linker-config-before-on-init-starts-.patch`
adds two commands to the end of `on early-init` in `system/core/rootdir/init.rc`:

```
    enter_default_mount_ns
    update_linker_config
```

APEXes are already activated by then (`SetupFlattenedApexes()` runs in `SecondStageMain`,
before any action is processed), so binding the default linker configuration and
regenerating it there is safe. The later `enter_default_mount_ns` / `perform_apex_config`
in `on post-fs-data` become harmless no-ops (`access("/linkerconfig/default")` fails
once `/linkerconfig` has been re-bound).

This fixes the whole class of early vendor services, not just `vndservicemanager` —
`sensors@1.0` and `graphics.allocator@2.0` were mislinked the same way.

### Verified result

| metric | before | after |
|---|---|---|
| `vndservicemanager` libbinder @13s | `/system/lib64` (SYST) | `/apex/…vndk.v30` (VNDR) |
| `vendor.hwcomposer-2-1` starts | 20+ (crash loop) | 1 |
| `Mixing copies of libbinder` in logcat | hundreds | 0 |
| `lipstick` | only after manual `killall` | 77s, unaided |

---

## How the fix is delivered

Nothing is edited directly in the Android source tree. The patch lives in
`Sailfish-on-karatep/karatep-patches`, which carries **only** the karatep patches;
`mer-hybris/hybris-patches` is synced normally and is not forked. A clean build on any machine
is:

```sh
repo sync
cd $ANDROID_ROOT
hybris-patches/apply-patches.sh --mb       # upstream series
karatep-patches/apply-patches.sh --mb      # karatep patches, on top
```

The second pass exists because these patches are written against the tree *after* upstream's
series. That is verified rather than assumed: applied to a pristine LineageOS `hybris-18.1`
tree, `system/core/0040` and `bionic/0009` both fail. Changes to repos that upstream's series
does *not* touch are carried as forks repinned in `local_manifests.xml` instead — that is how
the kernel, both device trees, the vendor blobs and `hybris-boot` are handled.

Three defects had to be fixed before that actually worked:

* `apply-patches.sh` runs `git am <dir>/*.patch`, so the **glob order is the apply order**. The
  karatep patches were numbered from `0001`, which both collided with upstream patches and
  sorted *before* the ones they depend on. They are now `bionic/0009` and
  `system/core/0040..0042`.
* The mountpoint patch lived in `hybris-boot/`, but the project path is `hybris/hybris-boot`,
  so `apply-patches.sh` would have `cd`'d into a non-existent directory.
* That same patch had no `From:`/`Subject:` header, so `git am` could not parse it at all, and
  it deleted the 2362 lines describing every other device. It is now a proper git-am patch and
  purely additive.

Together these meant the tree only ever built because the patches had been applied by hand —
the exact situation this section exists to prevent.

---

## Hypotheses considered and ruled out

* **`dev-binderfs.mount` failed** — expected. binderfs is Linux 5.0+; this kernel is 3.18
  and `/proc/filesystems` has no binder entry. The legacy nodes exist correctly
  (`/dev/binder` 10,57; `/dev/hwbinder` 10,56; `/dev/vndbinder` 10,55).
* **Binder driver lacks per-device context managers** — it does not. This 3.18 binder has
  `struct binder_context` embedded in `struct binder_device`, so each device has its own
  context manager.
* **`vndservicemanager` deadlocked/wedged** — no. It sits in `SyS_epoll_wait`,
  `S (sleeping)`, single-threaded: a perfectly healthy idle event loop.
* **Mount-namespace divergence** — no. Every process shares `mnt:[4026531840]`.
* **Environment difference (`LD_LIBRARY_PATH`, `LD_CONFIG_FILE`)** — no. `/proc/*/environ`
  of early vs late processes are equivalent.
* **`ld.config.txt` content wrong** — no. The `[vendor]` section correctly routes
  `libbinder.so` through the `vndk` namespace; only `libbinder_ndk.so` comes from `system`.
* **The IMS crash loop (`imsrcsservice`/`ims_rtp_daemon`/`imsdatadaemon`) is the cause** —
  no, it is a separate pre-existing fault. It matters only because its log spam wraps the
  ring buffers (see below).
* **Missing linkerconfig generation** — no. Patches `0001`/`0051` already ensure
  `/linkerconfig/ld.config.txt` is generated. That is *why* the previous fixes did not
  work: they fixed generation, not the **timing** of the bootstrap→default switch.

---

## Why the previous fixes did not work

Patches `0001` and `0051` are both correct and both are applied (confirmed in the
`system/core` git log). They make sure the linker configuration is *generated* on a
hybris system where `first_stage_init` never runs. Neither of them changes *when*
`/linkerconfig` stops being the bootstrap configuration — which is the actual defect.
In fact, once generation works, the split becomes observable: early services get the
bootstrap config and later ones get the APEX-aware config, and those two populations can
no longer talk to each other over `/dev/vndbinder`.

---

## Debugging notes for next time

The default journald config on this rootfs destroys boot evidence:

```
Storage=volatile
RuntimeMaxUse=1M
RateLimitBurst=300
```

With the IMS crash loop spamming, `journalctl -b` and `dmesg` both wrap within ~500s, so
nothing from the first two minutes of boot survives. Before debugging any boot problem:

```sh
cp /etc/systemd/journald.conf /etc/systemd/journald.conf.bak
printf '%s\n' '[Journal]' Storage=persistent SplitMode=none \
    RateLimitIntervalSec=0 RateLimitBurst=0 SystemMaxUse=300M RuntimeMaxUse=64M \
    > /etc/systemd/journald.conf
```

Also note `/system/bin/logcat` exists but is **not on `PATH`** in the debug shell — the
Android-side log (where the `Parcel` errors live) is invisible unless invoked by full
path. `logd` runs normally.

## Still outstanding (unrelated to this bug)

* `vendor.imsrcsservice` / `vendor.ims_rtp_daemon` / `vendor.imsdatadaemon` crash-loop
  continuously.
* `hwservicemanager` retries `android.hardware.bluetooth@1.0::IBluetoothHci` every ~61s.
* Failed units: `droid-bootctl.service`, `systemd-tmpfiles-setup.service`,
  `wlan-module-load.service` (and the benign `dev-binderfs.mount`).
* `karatep_defconfig:511` has `CONFIG_ANDROID_BINDER_DEVICES=y`, but that symbol is a
  **string** (`default "binder,hwbinder,vndbinder"`). The malformed value is discarded in
  favour of the default, so the three nodes still appear — but the line is wrong and
  should be `CONFIG_ANDROID_BINDER_DEVICES="binder,hwbinder,vndbinder"`.
