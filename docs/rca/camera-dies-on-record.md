# Root cause: the camera dies the instant video recording starts

Device: **lenovo/karatep** — Lenovo Vibe K6 Note / Plus, **MSM8937 / Snapdragon 430**, Adreno 505.
Base: LineageOS 18.1 (Android 11) / `hybris-18.1`, aarch64, Sailfish OS 5.1.0.11.

Status: **fixed**, both causes, by two changes in
[`droid-config-karatep`](https://github.com/Sailfish-on-karatep/droid-config-karatep):
`sparse/etc/dconf/db/vendor.d/jolla-camera-hw.txt` and a `Requires:` line in
`patterns/patterns-sailfish-device-adaptation-karatep.inc`. Verified on hardware.

---

## Symptom

Stills worked on both cameras. Pressing **record** killed the camera: the viewfinder went black,
the app stopped responding, and nothing short of a reboot brought the camera back — switching
cameras, leaving and re-entering the app, and killing `jolla-camera` all failed.

---

## Why this is one write-up and not two

There are **two independent defects** here, with different mechanisms, in different components,
needing different fixes. They are documented together because on this device they always fire
together and neither is diagnosable while the other is present: defect A is what fails, and
defect B is why the failure is unrecoverable. Fixing A alone gives you a camera that records
once and then wedges. Fixing B alone gives you a camera that recovers cleanly from a recording
that never works.

---

## Defect A — the resolution asked for cannot be encoded

### What happens

Sailfish's camera UI reads its capabilities from dconf, under `/apps/jolla-camera/`. Ports
supply those values in `/etc/dconf/db/vendor.d/jolla-camera-hw.txt`. **karatep had no such
file.** With nothing to constrain it, `jolla-camera` took the largest size each camera
advertises in its Android HAL parameters:

| | rear (`primary`) | front (`secondary`) |
|---|---|---|
| largest `video-size-values` | **3840x2160** | 3264x1836 |

Both sensors will happily *capture* at those sizes. Nothing on this device can *encode* them.

### The encoder limits, measured on the device

`/vendor/etc/media_codecs.xml` — the file `MediaCodec`/`ACodec` actually enforce:

```xml
<MediaCodec name="OMX.qcom.video.encoder.avc" type="video/avc" >
    <Limit name="size" min="96x96" max="1920x1088" />
    ...
    <Limit name="performance-point-1920x1088" value="30" />
</MediaCodec>
```

**1920x1088 is the hardware ceiling.** 3840x2160 is roughly four times the pixel count.

The Codec2 software fallback cannot take it either —
`/vendor/etc/media_codecs_google_c2_video.xml`:

```xml
<MediaCodec name="c2.android.avc.encoder" type="video/avc">
    <Limit name="size" min="16x16" max="2048x2048" />
    <Limit name="block-count" range="1-8192" />   <!-- max 2048x1024 -->
```

3840 alone exceeds `max="2048x2048"`, and 3840x2160 is 240x135 = **32400** macroblocks against a
budget of 8192. It returned `-EINVAL`.

### A trap in the vendor configuration

`/vendor/etc/media_profiles_V1_0.xml` **contradicts** `media_codecs.xml`:

```xml
<VideoEncoderCap name="h264" enabled="true"
    minFrameWidth="176" maxFrameWidth="3840"
    minFrameHeight="144" maxFrameHeight="2160"
    ...
    maxHFRFrameWidth="1920" maxHFRFrameHeight="1080" />
```

That claims 4K encode. It is wrong, and it is not what enforces anything — `VideoEncoderCap` is
advisory, consulted only by the legacy `CamcorderProfile` path, while `media_codecs.xml` is what
the codec actually honours. Note that the same file's `CamcorderProfiles` stop at
`quality="1080p"`: there is **no 2160p profile**, and even its own HFR ceiling is 1920x1080.
Do not read `VideoEncoderCap` as a statement of capability on this device.

### The failure chain

`droidcamsrc` encodes video internally, through `DroidMediaRecorder`, and hands the already
encoded stream out of its `vidsrc` pad. So the encoder is created inside the camera stack, not
downstream of it, and its failure takes the stack with it:

```
DroidMediaRecorder: Cannot create codec
DroidMediaBufferQueue: Client wasn't able to handle a received frame
DroidMediaBufferQueue: Client wasn't able to handle a received frame
        ... forever ...
```

Nothing is consuming the queue any more, so the viewfinder freezes and then goes black. That is
the visible part of the bug.

### Fix

`sparse/etc/dconf/db/vendor.d/jolla-camera-hw.txt`, in its entirety:

```
[apps/jolla-camera]
maxVideoResolution='1920x1080'
```

`CameraConfigs` reads it in `cameraconfigs.cpp` and uses it to filter the resolution list the app
will consider:

```cpp
QVariant value(MDConfItem("/apps/jolla-camera/maxVideoResolution").value());
if (!value.isNull()) {
    QStringList values = value.toString().split('x');
    ...
}
for (const QSize resolution : recorder->supportedResolutions()) {
    if (!maxVideoResolution.isValid() || (resolution.height() <= maxVideoResolution.height()
                                          && resolution.width() <= maxVideoResolution.width())) {
```

— which is why the value is a `'WIDTHxHEIGHT'` string rather than a list.

Stills are untouched and stay at each sensor's full resolution (rear 4632x3474, front
3264x2448). They were already there before this file existed, which is the point of the next
section.

### One key, and no more than one — this was checked, not assumed

The obvious thing to write here is the large `jolla-camera-hw.txt` that most ports carry, with
`[apps/jolla-camera/primary/image]` sections setting `imageResolution`, `viewfinderResolution`,
`flashValues`, `whiteBalanceValues`, `focusDistanceValues`, `isoValues` and so on, transcribed
from the HAL dump. **That file would have been almost entirely inert**, and it is worth recording
exactly why, because it looks correct and fails silently:

* `src/settings.qml` in the installed jolla-camera builds the per-mode `ConfigurationGroup` path
  as `position + "/" + captureMode`, where position is **`back`** or **`front`**. So
  `primary`/`secondary` are dead section names regardless of their contents. Jolla's own stock
  `/etc/dconf/db/vendor.d/jolla-camera.txt` still ships `primary`/`secondary` sections — they are
  equally inert, which is exactly what makes copying the pattern so plausible.
* That group declares only `iso`, `flash`, `exposureMode`, `meteringMode`, `timer` and
  `aspectRatio`. Grepping every installed `.qml` for the resolution and `*Values` names returns
  nothing at all.
* Live dconf settles it: the running app reads and writes `[back/image]` and `[back/video]` and
  never touches `[primary/*]`.
* `exposureCompensationValues` *is* real, but it belongs to the **global** `[apps/jolla-camera]`
  group (`settings.qml:83`, default `[4, 3, 2, 1, 0, -1, -2, -3, -4]`), not the per-mode ones.
* `maxImageResolution` is read by upstream `cameraconfigs.cpp` but **does not exist in the build
  installed here** — no such string in `libjollacameraplugin.so`. Stills cannot be capped this
  way on 5.1.0.11.

Everything else — resolutions, flash modes, focus modes, white balance — is derived at runtime by
`CameraConfigs` from what QtMultimedia reports and chosen per aspect ratio in `CaptureView.qml`:

```qml
resolution: _pickResolution(CameraConfigs.supportedImageResolutions, Settings.aspectRatio)
resolution: _pickResolution(CameraConfigs.supportedVideoResolutions, CameraConfigs.AspectRatio_16_9)
```

The independent check: photos taken **before** any `jolla-camera-hw.txt` existed on this device
are already 4632x3474 (rear) and 3264x2448 (front) — full sensor size on a 16 MP and an 8 MP
sensor. Nothing was being configured; the app was reading the hardware all along. The single
thing it could not work out for itself is that the encoder is narrower than the ISP, and that is
the one key this file supplies.

### Verified

```
QCamera <HAL><INFO> configureStreamsPerfLocked: stream[0] ... width = 1920, height = 1080
QCamera <HAL><INFO> configureStreamsPerfLocked: stream[1] ... width = 1920, height = 1080
ACodec  setupAVCEncoderParameters with [profile: Baseline] [level: Level4]
ACodec  setupVideoEncoder succeeded
```

---

## Defect B — `minimediaservice` waits forever for AudioFlinger, taking CameraService with it

### What happens

With defect A fixed, the encoder starts. **Four milliseconds later** the camera stack stops
answering, permanently:

```
20:43:27.518  I/ACodec        (25939): setupVideoEncoder succeeded
20:43:27.572  I/MediaCodecSource(25939): MediaCodecSource (video) starting
20:43:27.572  I/CameraSource  (25939): Using encoder format: 0x22
20:43:27.576  I/ServiceManager(25954): Waiting for service 'media.audio_flinger' on '/dev/binder'...
20:43:28.577  I/ServiceManager(25954): Waiting for service 'media.audio_flinger' on '/dev/binder'...
        ... once a second, 279 times, until logging was stopped four minutes later ...
```

It can never succeed. **Sailfish has no AudioFlinger** — audio goes through PulseAudio, and
nothing ever registers `media.audio_flinger` on `/dev/binder`. The wait is unbounded.

### Why that kills the camera and not just the audio track

The two PIDs above are the whole story:

| PID | process | role |
|---|---|---|
| 25939 | `jolla-camera` | `ACodec`, `MediaCodecSource`, `CameraSource` |
| 25954 | `minimediaservice` | hosts **`CameraService`** *and* the media services |

Confirmed in the same log — every camera transaction is served by 25954:

```
I/CameraService(25954): CameraService::connect call (PID -1 "droidmedia", camera ID 0) ...
I/CameraService(25954): disconnect: Disconnected client for camera 0 for PID 25939
```

`minimediaservice` is a single process hosting both. When a recording starts, its binder threads
block in `waitForService("media.audio_flinger")` — and every subsequent camera call queues behind
them. The camera stack is not crashed; it is **blocked**, in a wait with no timeout. That, not
the encoder, is why the camera never came back and why nothing at the app layer could recover it.

### Fix

`audiosystem-passthrough-dummy-af` provides a stub that registers the service so the lookup
returns immediately. The base `audiosystem-passthrough` package was already installed as a
dependency; only this subpackage was missing, and nothing pulled it in. It is now required by
the adaptation pattern:

```
Requires: audiosystem-passthrough-dummy-af
```

The stock comment shipped with the generated pattern says `-dummy-af` is for devices running the
main passthrough in **qti mode**. That is not karatep: there is no `AUDIOSYSTEM_PASSTHROUGH_TYPE`
in `/etc/sysconfig/pulseaudio`, no `audiosystem-passthrough.service`, and the main passthrough
runs as `audiosystem-passthrough --module`. It is needed here regardless — the trigger is
`minimediaservice` hosting `CameraService`, not the passthrough mode.

---

## Verified result

Two recordings made through the normal camera UI, pulled off the device and decoded on a host:

| | rear | front |
|---|---|---|
| video | H.264 **Baseline, 1920x1080** | H.264 Baseline, 1920x1080 |
| audio | AAC-LC, 48 kHz, stereo | AAC-LC, 48 kHz, stereo |
| decode | full file, **zero errors** | full file, **zero errors** |
| audio level | mean −40.4 dB (real content, not silence) | mean −38.3 dB |
| rotation tag | −90 | +90 |
| picture | verified by extracting a frame | — |

The camera also survives recording now: start, stop, play back, record again, switch cameras —
no wedge.

---

## Two things that are still not right

Neither blocks recording, and neither is a dconf matter, so both were left alone deliberately.

**1. The frame rate floats with exposure.** The HAL advertises
`preview-fps-range-values=(7000,30000),(30000,30000),...` and nothing pins it, so AE is free to
drop the rate in low light. Measured frame durations:

| clip | conditions | frame durations | effective |
|---|---|---|---|
| rear | dim | 45 frames @ 60 ms, 23 @ 80 ms | 12.5–16.7 fps |
| front | bright | 90 frames @ 33 ms | 30.1 fps |

The file is still correct — the timestamps are honest and playback is smooth — but a clip shot
indoors is genuinely 15 fps. Pinning `(30000,30000)` would be a `gstdroidcamsrcquirks.conf` or
gst-droid change, not a dconf one.

**2. The video track ends before the audio track.** 5.095 s vs 5.760 s, and 3.122 s vs 3.371 s —
roughly 0.3–0.7 s of audio past the last frame, in both clips. Something in the video EOS path
truncates the tail.

Also noted: `/etc/gst-droid/gstdroidcamsrcquirks.conf` does not exist on this port. gst-droid
logs a warning and carries on with its defaults; harmless today, but that is the file any
frame-rate pinning would go into.

---

## Recovering a wedged camera without rebooting

The README used to say a reboot was the only way out. It is not — restarting the two services
that hold the camera is enough, and it takes a couple of seconds:

```sh
setprop ctl.restart qcamerasvr    # mm-qcamera-daemon
setprop ctl.restart minimedia     # minimediaservice (CameraService lives here)
```

This is worth knowing beyond this bug: anything that leaves `mm-qcamera-daemon` spinning or
`CameraService` refusing connections with

```
CameraService::connect (PID -1) rejected (too many other clients connecting)
```

clears the same way. That state is easy to create by hand — killing a `gst-launch` probe with
`SIGKILL` mid-stream does it, because the client never disconnects from `CameraService`.

---

## Hypotheses considered and ruled out

* **Camera HAL or `mm-qcamera-daemon` crash** — no. Neither ever died; `qcamerasvr` stayed up
  across every failure. Stills kept working right up to the moment record was pressed.
* **`droidmedia` / `minimediaservice` crash** — no. `minimediaservice` stayed alive the entire
  time. It was blocked, not dead, which is exactly why nothing restarted it.
* **A missing `gstdroidcamsrcquirks.conf`** — no. It is absent, and gst-droid warns about it,
  but the defaults are fine and supplying one changes nothing here.
* **The `vidsrc` pad needing a downstream encoder** — no, and this misled the first attempt at a
  headless reproduction. `droidcamsrc`'s `vidsrc` emits **already-encoded** `video/x-h264` /
  `video/mpeg`; only `vfsrc` is `video/x-raw`. A `camerabin` pipeline with
  `video-capture-caps=video/x-raw,...` fails with *"Failed to link camera source's vidsrc pad to
  video bin capsfilter"* for that reason alone, which is a property of the test, not the bug.
* **Audio routing / PulseAudio** — no. Audio is captured correctly at 48 kHz stereo in the
  working recordings. Defect B is about a *binder service name* never being registered, not
  about audio hardware.
* **A `jolla-camera` or QtMultimedia bug** — no. The app asks for a resolution the HAL
  advertises; the HAL advertises a resolution the encoder cannot take. The missing constraint is
  the port's, and supplying it is the sanctioned mechanism.

---

## Debugging notes for next time

**Read the HAL's parameters instead of guessing them.** Every Android camera parameter — every
resolution list, every mode list — can be dumped straight out of `droidcamsrc`, which logs each
one with a `GST_LOG ("param %s = %s", key, value)` in `gstdroidcamsrcparams.c`:

```sh
GST_DEBUG=droidcamsrc:9 <pipeline> 2>&1 | grep "param .* = "
```

Note **`:9`, not `:5`** — the parameter dump is `GST_LOG`, so anything below 9 shows nothing and
looks like the mechanism does not exist. It is the right way to learn what the hardware can do —
just don't assume the answer belongs in dconf, per the section above.

**Verify that a config key is actually read before believing it works.** Nothing warns you: dconf
accepts any key you write, `dconf read` hands it back, and an app that never looks at it behaves
exactly as if the value were wrong. The checks that settle it are cheap — grep the installed QML
and `strings` the plugin `.so` for the key name, and diff `dconf dump /apps/<app>/` before and
after using the feature to see which groups the app really writes.

**`droid-camres` does not work.** It is the tool the HADK ecosystem points at for exactly this
job, and it is broken against current gst-droid. `droid-camres` 1.2.3 checks the `camera-device`
property with `G_IS_PARAM_SPEC_ENUM` (`camres.cpp:40`), but gst-droid now registers it with
`g_param_spec_int` (`gstdroidcamsrc.c:1103`), so it exits with:

```
Camres error: Property camera-device is not an enum.
```

Four porters have hit this in the `#sailfishos-porters` archive; `mlehtima/droid-camres` has a
`multi-cam` branch that addresses it. It also needs `QT_QPA_PLATFORM=minimal` to run headless,
or it dies on the missing `xcb` plugin. Use `GST_DEBUG=droidcamsrc:9` instead — it is the same
data, from the source of truth, with nothing to install.

**`logcat`'s ring buffer will destroy the evidence.** `mm-camera` emits thousands of lines a
second, so the default buffer wraps long before you can read it. Before reproducing:

```sh
/system/bin/logcat -G 16M
/system/bin/logcat -c
/system/bin/logcat -v time mm-camera:S chatty:S "*:V" > /tmp/cap.log &
```

**The debug shell on port 2323 is a pty that echoes and wraps.** Anything longer than the
terminal width comes back corrupted, which silently mangles long parameter values and any file
pushed by `cat > file`. Move files in both directions as base64 in fixed-width chunks. The
recovery shell also lacks `timeout` and `find -ls`, and `find`/`ls` are busybox.

**The device has no `gst-discoverer-1.0` or `ffprobe`.** To check a recording properly, serve it
off the device (`python3 -m http.server`) over the USB link and analyse it on the host. Verifying
by file size proves nothing — a truncated or all-black clip is still megabytes.
