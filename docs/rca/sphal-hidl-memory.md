# The camera app aborts at the end of video playback: sphal cannot load the HIDL memory impl

## Symptom

Play a recorded video in the Sailfish camera app. It plays, and the app
disappears the moment playback ends. It is not a clean exit:

```
rich-core: arguments: --pid=3446 --signal=6 --name=codec_looper
```

Signal 6 is `SIGABRT`, in the `codec_looper` thread.

## Diagnosis

The full sequence from the journal:

```
01:16:18 jolla-camera: viewfinderbin-capsfilter: transform_caps returned caps which are not a real subset of the filter caps
01:16:20 invoker: library "/apex/com.android.vndk.v30/lib64/hw/android.hidl.memory@1.0-impl.so"
                  needed or dlopened by "/usr/libexec/droid-hybris/system/lib64/libvndksupport.so"
                  is not accessible for the namespace "sphal"
01:16:23 invoker: [swscaler] No accelerated colorspace conversion found from yuv420p to bgra
01:16:27 invoker: postPendingRepliesAndDeferredMessages: mReplyID == null,
                  from kWhatRelease:STOPPING following kWhatError:STOPPING
01:16:28 booster-silica-media: boosted process (pid=41) signal(Aborted)
```

The abort itself is a known AOSP behaviour, not the bug: `MediaCodec` raised
`kWhatError`, `release()` then arrived while it was `STOPPING`, `mReplyID` was
null, and MediaCodec calls `TRESPASS()` — which aborts the process rather than
returning an error. So *any* codec error becomes fatal to the whole app at
teardown. The question is what caused the codec error.

`android.hidl.memory@1.0-impl.so` is what HIDL uses for shared memory, which is
how MediaCodec passes buffers. `libvndksupport` loads it with
`android_load_sphal_library()`, i.e. `dlopen` inside the `sphal` linker
namespace. On this port that namespace cannot reach it:

```
namespace.sphal.isolated        = true
namespace.sphal.search.paths    = /odm/${LIB} : /vendor/${LIB} : /vendor/${LIB}/egl : /vendor/${LIB}/hw
namespace.sphal.permitted.paths = /odm/${LIB} : /vendor/${LIB} : /system/vendor/${LIB}
```

The library exists, just nowhere `sphal` may look:

```
/apex/com.android.vndk.v30/lib64/hw/android.hidl.memory@1.0-impl.so
/apex/com.android.vndk.v30/lib/hw/android.hidl.memory@1.0-impl.so
/system/apex/com.android.vndk.current/lib64/hw/android.hidl.memory@1.0-impl.so
/system/lib64/hw/android.hidl.memory@1.0-impl.so
```

and **not** in `/vendor/lib/hw` or `/vendor/lib64/hw`. Grepping the generated
config for a `sphal` path mentioning `apex` returns **zero** hits.

On stock Android 11 `linkerconfig` adds
`/apex/com.android.vndk.v${VNDK_VER}/${LIB}/hw` to `sphal`'s search paths
precisely so `android_load_sphal_library()` can find these. Ours does not, which
is consistent with the flattened-APEX handling this port already carries patches
for (`linkerconfig` / `mount_namespace.cpp` in `karatep-patches`).

`/linkerconfig/ld.config.txt` is **generated at boot**, so editing it on the
device is not a fix.

## Scope

This is not camera-specific. Every `sphal` consumer that needs HIDL shared
memory is affected, MediaCodec included — which makes it a candidate for other
media faults on this port, not just this abort. The `swscaler` line in the same
trace is the same story from the other side: playback fell back to software
`yuv420p -> bgra` conversion because the hardware path was not usable.

## Fix

Not yet written. It belongs in `karatep-patches`, extending the existing
`linkerconfig` patch to add the VNDK APEX `hw` directory to the `sphal`
namespace's search and permitted paths — which is what upstream does.

The cruder alternative is to place `android.hidl.memory@1.0-impl.so` into
`/vendor/lib/hw` and `/vendor/lib64/hw`, matching what a stock device ships.
That works with the config as generated, but means hand-installing onto the
vendor partition, the same awkward step the fingerprint service needs.

## See also

- `waydroid-camera-hal-name.md` — a different camera fault, in the container.
- `camera-dies-on-record.md` — the earlier recording fault, unrelated.
