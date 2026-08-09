# No video plays inside Waydroid: the container loses the vendor's video tuning properties

## Symptom

Every video fails to play inside the Waydroid container. The player opens, audio
plays, and the picture never appears; the gallery reports the clip as zero
frames and immediately signals completion:

```
D/CodeauroraVideoView: OnCompletion:
D/CodeauroraVideoView: CODEC_VIDEO: null
D/CodeauroraVideoView: FRAMES: 0 DROP: 0 DropPecentage: 0.0%
D/CodeauroraVideoView: RESOLUTION: 0x0
```

It was first noticed as "video recorded in Waydroid does not play back", which
made it look like a recording fault. It is not: **recording is fine**. The
recorded file pulled off the device is a valid 1920x1080 H.264 Baseline / AAC
MP4 that decodes cleanly and shows real picture content elsewhere. Playback is
the only broken half, and it is broken for *every* video, not just recordings.

## Diagnosis

### The decoder never starts

```
I/OMX-VDEC-1080P: component_init: OMX.qcom.video.decoder.avc : fd=13
I/OMX-VDEC-1080P: omx_vdec::component_init() success : fd=13
E/OMX-VDEC-1080P: HW Unsupported received
E/OMX-VDEC-1080P: OMX_COMPONENT_GENERATE_UNSUPPORTED_SETTING
E/OMX-VDEC-1080P: Failed to prepare bufs
E/OMXNodeInstance: allocateBuffer(qcom.decoder.avc, Input:0 ...) ERROR: InsufficientResources
E/ACodec:  Failed to allocate buffers after transitioning to IDLE state (error 0xfffffff4)
E/MediaCodec: Codec reported err 0xfffffff4/NO_MEMORY, actionCode 0, while in state 5/STARTING
E/NuPlayerDecoder: Failed to start [OMX.qcom.video.decoder.avc] decoder (err=-12)
```

`NO_MEMORY` is a red herring — buffer allocation fails because the Venus session
is already dead by then. The kernel says why:

```
msm_vidc: info: Opening video instance: ..., 1
subsys-pil-tz 1de0000.qcom,venus: venus: Brought out of reset
msm_vidc:  err: HFI_EVENT_SESSION_ERROR
msm_vidc: warn: Session error received for session ...
msm_vidc: warn: Unsupported bitstream in ...
```

The firmware loads fine and then rejects the session immediately, before a
single byte of bitstream has been fed. **It is not resolution-dependent** — a
640x480 control clip generated on the build host fails identically to the 1080p
recording, which rules out level/size limits.

### "Unsupported bitstream" is a misnomer

`msm_comm_generate_session_error()` prints that string for
`VIDC_ERR_NOT_SUPPORTED`, and `hfi_response_handler.c` folds seven distinct HFI
errors into that one value — including `UNSUPPORTED_PROPERTY`,
`UNSUPPORTED_SETTING` and `INSUFFICIENT_RESOURCES`. The message says nothing
about the stream. The packet trace
(`echo 0x3f > /sys/kernel/debug/msm_vidc/debug_level`) gives the real code:

```
pkt: 0000001c 00011001 90d5c0c0 00000001 01003001 00000003 00000001
pkt: 0000001c 00011001 90d5c0c0 00000001 01003001 00000002 00000000
pkt: 0000001c 00021001 90d5c0c0 00000002 00001007 00000000 00030980
```

- `0x00011001` = `HFI_CMD_SESSION_SET_PROPERTY`, property **`0x01003001`** =
  `HFI_PROPERTY_PARAM_VDEC_MULTI_STREAM`. First packet enables buffer type 3
  (`HAL_BUFFER_OUTPUT2`), second disables buffer type 2 (`HAL_BUFFER_OUTPUT`) —
  verbatim the `V4L2_CID_MPEG_VIDC_VIDEO_STREAM_OUTPUT_SECONDARY` branch of
  `msm_vdec_s_ctrl()`.
- `0x00021001` = `HFI_MSG_EVENT_NOTIFY`, error **`0x1007`** =
  **`HFI_ERR_SESSION_UNSUPPORTED_PROPERTY`**.

So: userspace asked for split DPB/OPB output, and this Venus firmware does not
implement the property. Note the driver's own guard
(`inst->capability.pixelprocess_capabilities & HAL_VIDEO_DECODER_MULTI_STREAM_CAPABILITY`)
*passes* — the driver believes multi-stream is available, so it forwards the
property and only the firmware objects.

### Why userspace asks for it

`omx_vdec` picks split mode (DPB in UBWC, OPB linear) unless told not to:

```c
bool eligible_for_split_dpb_ubwc =
    m_progressive == MSM_VIDC_PIC_STRUCT_PROGRESSIVE &&
    is_not_vp9                                       &&
    !drv_ctx.idr_only_decoding                       &&
    !m_disable_split_mode;          //@ Set prop to disable split mode
...
property_get("vendor.vidc.disable.split.mode", property_value, "0");
m_disable_split_mode = atoi(property_value);
DEBUG_PRINT_HIGH("split mode is %s", m_disable_split_mode ? "disabled" : "enabled");
```

karatep's `/vendor` sets `vendor.vidc.disable.split.mode=1` precisely because
msm8937's Venus cannot do it. That is why video plays on the Sailfish host and
not in the container: **the container has its own `/vendor`.** Waydroid mounts
the generic HALIUM_11 vendor image over `/vendor` and leaves the device's real
vendor partition at `/vendor_extra`, so the device's vendor *libraries* are
still used (`md5sum` of `/vendor_extra/lib64/libOmxVdec.so` in the container
equals the host's `/vendor/lib64/libOmxVdec.so`) but its vendor *properties* are
not. `getprop | grep vidc` returns nothing inside the container and eleven
entries on the host:

```
vendor.vidc.disable.split.mode = 1
vendor.vidc.dec.downscalar_width = 1920
vendor.vidc.dec.downscalar_height = 1088
vendor.video.disable.ubwc = 1
vendor.gralloc.enable_fb_ubwc = 1
...
```

The same class of bug will bite any Halium port that reuses the host's video
blobs: the blob is the device's, the tuning that makes the blob match the
device's firmware is not.

This is unrelated to the Android 13-system / Android 11-vendor generation
mismatch that causes the camera `configureStreams` failure — it reproduces with
the vendor's own component and would happen on a matched pair too.

## Fix

Feed the properties back into the container, split across two repos so that
neither holds the other's knowledge.

**Generic half** — `rpm/0004-propagate-host-vendor-video-props.patch` in
[`Sailfish-on-karatep/waydroid`](https://github.com/Sailfish-on-karatep/waydroid).
`make_base_props()` already copies a handful of host properties the container
needs (`ro.hardware.gralloc`, `ro.opengles.version`, `ro.product.vendor.*`,
`ro.vendor.build.fingerprint`); the patch adds "every host property under a
declared namespace" to that list, via a new `props.host_get_prefixed()` helper.
It contains **no SoC knowledge**: with no declaration present the list is empty
and nothing is propagated, so it is inert until opted into. Skipped for
`MAINLINE`, which has no host vendor partition to copy from.

**Device half** — `sparse/etc/waydroid-extra/vendor-props.cfg` in
[`droid-config-karatep`](https://github.com/Sailfish-on-karatep/droid-config-karatep):

```ini
[host_props]
propagate_prefixes = vendor.vidc., vidc.
```

`/etc/waydroid-extra/` (falling back to `/usr/share/waydroid-extra/`) is
waydroid's own convention for things the distribution or device adaptation
supplies — it is already where `preinstalled_images_paths` and `channels.cfg`
live — so this is waydroid's native extension point rather than a workaround.

Only *namespaces* are named there. Values are read from the running host at
`waydroid init` / `upgrade` time, so the file never needs touching when
`vendor.prop` changes, and the same file works on any msm8937-era Qualcomm port.

On karatep it brings nine properties across, the decoder fix plus the encoder
tuning that was equally missing:

```
vendor.vidc.disable.split.mode=1        <- the one that unbreaks decode
vendor.vidc.dec.downscalar_width=1920
vendor.vidc.dec.downscalar_height=1088
vendor.vidc.enc.narrow.searchrange=1
vendor.vidc.enc.disable.pq=true
vendor.vidc.enc.disable_bframes=1
vendor.vidc.enc.disable_pframes=1
vidc.enc.dcvs.extra-buff-count=2
vidc.enc.disable.pq=true
```

`make_base_props()` runs at `waydroid init` and `waydroid upgrade`, **not** at
session start, so an existing install needs

```sh
waydroid upgrade -o        # offline: regenerates waydroid_base.prop, downloads nothing
```

once after installing the package. `images.py:make_prop()` then copies
`waydroid_base.prop` into `waydroid.prop` at every session start, and it is
bind-mounted onto `/vendor/waydroid.prop`, which Android init imports.

Waydroid's own `[properties]` section in `waydroid.cfg` is applied *after* this,
so a device that needs to override one of the propagated values still can.

### Which half is device-specific?

Three separable layers, and they do not have the same answer. This is what
decides where each piece lives, and what could honestly be offered upstream.

- **The mechanism is generic, and not `hybris-18.1`-specific.**
  `generate_nodes_lxc_config()` does `make_entry("/vendor", "vendor_extra", ...)`
  under `if args.vendor_type != "MAINLINE"`, so *every* Halium vendor type — not
  just `HALIUM_11` — replaces `/vendor` and loses the host's vendor properties
  while still using the host's vendor libraries. Nothing is tied to Android 11.
  This half is upstreamable as-is.
- **The values were already device-specific and already in the right place.**
  All nine live in `device/lenovo/karate-common/vendor.prop` (lines 94–102), in
  `android_device_lenovo_karate-common`. Nothing had to move: the patch reads
  them from the running host, which is that file's own output.
- **The prefix list is SoC-specific**, so it is *not* in the waydroid fork.
  `vendor.vidc.` / `vidc.` are Qualcomm names; a Mediatek or Exynos port needs
  different ones. Hardcoding them in waydroid would mean a table growing one
  entry per SoC family forever, and every adaptation waiting on a waydroid
  release to add its own. It lives in `droid-config-karatep` instead.

Worth knowing for context: waydroid upstream *does* already carry SoC-specific
knowledge — `generate_nodes_lxc_config()` has `/dev/kgsl-3d0` (Qualcomm),
`/dev/mali0` (Mali), `/dev/pvr_sync` (PowerVR) and a block commented
"Media dev nodes (for Mediatek)". A hardcoded prefix table would not have been
out of keeping with the surrounding code. Keeping it out is a deliberate choice
to hold the line between the two repos, not a claim that upstream would refuse it.

Deliberately **not** propagated: `vendor.video.disable.ubwc` and
`vendor.gralloc.enable_fb_ubwc`, which the host also sets. Those steer the
display/gralloc path, and the container has its own hwcomposer rendering to
Wayland rather than to a framebuffer. Untested there, and decode works without
them.

## Verified on hardware

With the packaged patch installed and **no device-local config at all**
(`waydroid.cfg`'s `[properties]` section emptied first, to prove the package is
doing the work), `waydroid upgrade -o` logs:

```
Propagating host vendor property: vendor.vidc.disable.split.mode=1
...
```

Then, on a fresh session, the same 1080p recording that previously produced
`FRAMES: 0`:

```
D/OMX-VDEC-1080P: split mode is disabled
D/CodeauroraVideoView: CODEC_VIDEO: OMX.qcom.video.decoder.avc
D/CodeauroraVideoView: FRAMES: 60 DROP: 7 DropPecentage: 11.666667%
D/CodeauroraVideoView: RESOLUTION: 1920x1088
```

and the kernel log for the whole playback is two clean lines, no session error:

```
msm_vidc: info: Opening video instance: ..., 1
msm_vidc: info: Closed video instance: ...
```

Hardware decode, not a software fallback — the component is
`OMX.qcom.video.decoder.avc`.

## Open

Nothing on the decode path. Two adjacent things are worth knowing:

- **Dropped frames.** 1080p30 decodes at roughly 5–12 % frame drop on this SoC.
  That is a Snapdragon 430 compositing an Android 13 system image through a
  Wayland compositor, not a fault — but it has not been profiled, so it is not
  established that the ceiling is the hardware rather than the hwcomposer path.
- **Encoder tuning arrived with the same patch** (`disable_bframes`,
  `disable_pframes`, `narrow.searchrange`, `disable.pq`) and was equally missing
  before. Recording already worked without it, so nothing was visibly broken;
  whether output quality or CPU cost changes has not been measured.

## No prior art

`bin/ircgrep.sh "Unsupported bitstream"` returns **zero** hits across eleven
years of `#sailfishos-porters`. Searches for `msm_vidc` turn up only unrelated
device-node and permission problems. Nobody appears to have hit this before, or
at least nobody diagnosed it in that channel.

## See also

- [`waydroid-camera-hal-name.md`](waydroid-camera-hal-name.md) — the container's
  camera provider, a different fault with the same shape: the container's
  `/vendor` is not the device's.
- [`sphal-hidl-memory.md`](sphal-hidl-memory.md) — the host-side media fault,
  unrelated to this one.
