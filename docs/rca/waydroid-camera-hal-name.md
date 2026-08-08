# Waydroid's camera provider crash-loops: the HAL is looked up under a name nobody provides

## Symptom

Inside the container, `android.hardware.camera.provider@2.4-service` fails every
start and init respawns it every five seconds:

```
E CamPrvdr@2.4-legacy: Could not load camera HAL module: -2 (No such file or directory)
E android.hardware.camera.provider@2.4-service: getProviderImpl: camera provider init failed!
E LegacySupport: Could not get passthrough implementation for android.hardware.camera.provider@2.4::ICameraProvider/legacy/0.
```

Roughly **294 of 439** logcat lines were this. The flood rolls the whole ring
buffer in about four minutes, which destroys the evidence for any *other*
problem being investigated at the time — that is how it was first noticed.

## Root cause

The container has no camera HAL module of its own. The real one belongs to the
host and is visible inside the container only through the `/vendor_extra` mount
that Waydroid sets up for HALIUM devices:

```
/vendor_extra/lib/hw/camera.msm8937.so      # 32-bit, 850328 bytes
```

`hw_get_module()` builds **absolute** paths, so the module has to exist under
`/vendor/lib/hw/`, and it only tries a fixed set of names derived from
properties. Placing the library there is necessary but not sufficient: the name
has to be one the process actually probes.

**It does not probe `camera.qcom.so`.** `getprop ro.hardware` returns `qcom` from
a shell, which makes that name look obviously right, and it is wrong. Traced
with `strace -f` on the container's init, the complete set of candidates is:

```
/odm/lib/hw/camera.default.so
/odm/lib/hw/camera.waydroid.so
/vendor/lib/hw/camera.default.so
/vendor/lib/hw/camera.waydroid.so
```

Only `ro.board.platform` resolves inside the container, and Waydroid sets it to
`waydroid` — the device's own value, `msm8937`, is only on the host. So the
names searched are `camera.waydroid.so` and the `camera.default.so` fallback.

## Fix

Install the host's camera HAL into the container under the name it looks for,
through Waydroid's overlay:

```sh
cp /vendor/lib/hw/camera.msm8937.so \
   /var/lib/waydroid/overlay/vendor/lib/hw/camera.waydroid.so
```

It must be a **real file, not a symlink** into `/vendor_extra`, and the overlay's
merged copy in `overlay_rw` must be cleared first — patch 0003 skips files that
already exist in the upper layer, so a stale copy shadows the new one forever.

Result, measured:

| | before | after |
|---|---|---|
| `Could not load camera HAL module` per boot | 48–92 | **0** |
| `ICameraProvider/legacy/0` registered | absent | `DM,FC Y ... pid 75` |
| Cameras visible to the framework | stale/none | **2** |

Dependencies resolve without further work: the trace shows `/vendor_extra/lib`
is already on the container's linker search path, which is how the
`camera.device@3.x` libraries load.

## Why the earlier attempts failed

Four theories, each plausible, each wrong, and each cheap to disprove *only*
with a trace:

| Theory | Why it was wrong |
|---|---|
| Wrong bitness | The provider is 32-bit ARM (`ELFCLASS32`, `e_machine=0x28`), matching the 32-bit HAL |
| Library unreachable | `/vendor_extra/...` is readable from inside the container; `head -c 4` shows a valid ELF |
| SELinux | Permissive (`enforce=0`), zero denials |
| `path_in_path()` rejecting an out-of-tree symlink | Plausible — Android 8+ does `realpath()` candidates — but a **real file** failed identically |

The lesson is narrow and worth keeping: when a lookup fails and every input
checks out, trace the syscalls instead of reasoning about them. The provider
lives about 40 ms and respawns every 5 s, so attaching to it directly always
loses the race — follow the container's init with `strace -f` and let it catch
the child at fork. `prebuilts/waydroid/strace-camera.sh` does this.

## Open

The host's Sailfish camera provider and the container's now both hold the same
camera HAL. Both are stable at rest (no respawns), but *simultaneous* use is
untested and is the obvious next hazard.

This fix is currently a hand-placed file in the overlay. Its proper home is the
port's own packaging, so it survives a `waydroid init` and a reflash.
