# The installer zip's update-binary does not run

## Symptom

Installing `sailfishos-karatep-release-<version>.zip` from the LineageOS
recovery fails immediately after verification:

```
Installing update...
E:Error in /sideload/package.zip (killed by signal 11)
Install from ADB complete (status: 1).
```

The signal number is not stable — **`signal 7` (SIGBUS) and `signal 11` (SIGSEGV)
are both this fault**, depending on where the broken binary happens to fault.
Treat either as the same diagnosis.

The install aborts before it touches `/data`, so nothing is lost: the previous
`/data/.stowaways/sailfishos` survives and the device still boots.

`/cache/recovery/last_install` records `time_total: 26` — the whole 26 s is
signature verification. The install itself lasted 0.1 s.

## Diagnosis

The crash is not in the package, the script or the recovery. The binary is dead
on arrival — it faults with no arguments at all:

```
$ adb push META-INF/com/google/android/update-binary /tmp/ub-test
$ adb shell /tmp/ub-test
Bus error
exit=135
```

A working updater prints `unexpected number of arguments: 1` and exits 1.

## Root cause

`update-binary` is `updater` from `$ANDROID_ROOT/out`, i.e. from the
**hybris-patched** Android tree, and it is statically linked, so it carries that
bionic inside it. A stock LineageOS recovery cannot run it.

The path from build to zip:

| Step | Where |
|---|---|
| `updater` built into `out/target/product/karatep/system/bin/updater` | hybris-hal build |
| copied to `/boot/update-binary` in the rootfs | `rpm/dhd/droid-hal-device.inc:612` |
| packed as `META-INF/com/google/android/update-binary` | droid-config kickstart, `pack_package-droid-updater` |

Nothing in that chain is karatep-specific — it is stock mer-hybris packaging, so
every hybris port ships an updater built against patched bionic.

This is the same failure class as the recovery's `/init`: see
[los-recovery.md](../los-recovery.md). A binary built against mer-hybris'
bionic/system/core, executed outside a Sailfish rootfs, misbehaves.

## Prior art

The problem is a decade old. From the `#sailfishos-porters` archive, 2015-02-27
and 2015-02-28, vgrade:

```
<vgrade> sledges: unless update-binary has static bionic
<vgrade> MSameer: so updater (update-binary) got rebuilt as it uses static
         bionic and this fails to pick up ro.xx properties
<vgrade> if the update-binary is built with the __system_find patch that prop
         is not found and the install fails
<vgrade> sledges: did you do a full rebuild after sensor hack, including
         update-binary
```

Their symptom was `getprop` returning null — hybris patches
`__system_property_find`, so the updater's `assert(getprop("ro.product.device")
== ...)` failed. Ours is a `SIGBUS` at startup. Same mechanism, different
patches.

The community workaround was to replace the compiled updater with a shell
script (`mer-hybris/hybris-boot` issue 162; `vknecht`, 2019-08-01). A copy of
one such script is kept at `/opencloud/update-binary`.

## Fix

Build `updater` from the parked, unpatched tree — the same tree the recovery is
built from — and install it over `out/`'s copy before droid-hal packages it.

```sh
bin/build-los-recovery.sh        # builds recovery.img and update-binary
bin/install-clean-updater.sh     # overwrites out/'s updater
rpm/dhd/helpers/build_packages.sh -d
```

`bin/build-hal-packages.sh` calls `install-clean-updater.sh` itself, so the
normal build order handles it. The ordering constraint is real: the script must
run **after** the last Android build (any `make` regenerates the patched
binary) and **before** `-d`.

**This has recurred.** It comes back whenever someone rebuilds the kernel and
then reaches for `build_packages.sh -d` directly instead of
`bin/build-hal-packages.sh` — the `-d` is the obvious step to repeat and the
updater fix is invisible from there. Two defences are now in place:

- `bin/build-image.sh` refuses to build an image when
  `out/…/system/bin/updater` does not match `prebuilts/recovery/update-binary`.
- [`flashing.md`](../flashing.md#check-the-updater-before-you-sign-anything)
  leads with the check.

Do **not** compare the two binaries by size. They are both 2,002,480 bytes —
same sources, different toolchain — so only a checksum distinguishes them.

Verified on device: the pristine binary reports its usage error and the install
proceeds past `Installing update...`.

## Why not the shell-script updater

It works, but it drops the `assert(getprop("ro.product.device") == "karatep")`
device guard and the standard edify semantics. Building the real updater
correctly costs one extra make target.
