# FM radio: the Media app cannot open the SMD transport from inside sailjail

**Status: fixed in the kernel, verified on device.** `2f54418751cd` (merged as
`b5ea3d9bd494`) opens the APPS_FM channel from the v4l `->open` instead of from a
sysfs write the sandbox forbids.

This is the bug that actually kept FM from working in the Media app. Two other FM
write-ups exist and both were real, but neither was this:
[fm-radio-enablement.md](fm-radio-enablement.md) (permission on the sysfs switch) and
[fm-event-queue-overflow.md](fm-event-queue-overflow.md) (the event queue wedging).

## Symptom

The FM page in the Media app will not tune, and the app hangs on the way out and has
to be force-quit. Driving `/dev/radio0` by hand from a shell works perfectly, which
makes the driver look healthy and the app look broken.

## Root cause

`radio-iris-transport` has **no `module_init`**. The only thing that ever opens the
`APPS_FM` SMD channel is a write to
`/sys/module/radio_iris_transport/parameters/fmsmd_set`. The irisradio plugin does
that write itself — but guarded:

```cpp
void IrisWorkerThread::setFmInit(bool enable) {
    if (QFile::exists(fmInitSet)) { ... }        // no file -> silently does nothing
}
```

The Media app runs under **sailjail/firejail**. Inspected on the running app:

```
$ ls -l /proc/<pid>/root/sys/module/radio_iris_transport/parameters/fmsmd_set
  No such file or directory
$ ls -l /proc/<pid>/root/dev/radio0
  crw-rw----  1 audio audio 81, 20 /dev/radio0
$ grep ' /sys ' /proc/<pid>/mounts
  sysfs /sys sysfs rw,...
  sysfs /sys sysfs ro,nosuid,nodev,noexec,...
```

`/sys` is remounted read-only and `/sys/module` is not present at all, so
`QFile::exists()` is false and the write is skipped without a word. `/dev/radio0`
*is* reachable — the udev rule grants the `audio` group — so `open()` succeeds and
everything after it fails.

The failure chain, all observed:

1. channel never opened → `radio->fm_hdev` is NULL → every ioctl returns `ENODEV`;
2. journal: `jolla-mediaplayer[6266]: [C] unknown:0 - Failed to set control (id: 134217732)`
   — `134217732` is `0x08000004`, `V4L2_CID_PRIVATE_IRIS_STATE`;
3. no power-on means no `IRIS_EVT_RADIO_READY`, and the plugin gates its entire UI on
   that event — **it will not tune**;
4. the worker thread then blocks forever in `VIDIOC_DQBUF`, and `stop()` waits on it
   from the GUI thread — **the app hangs**.

Thread states of the hung app confirm exactly that:

| thread | wchan |
|---|---|
| `jolla-mediaplay` (GUI) | `futex_wait_queue_me` — `QThread::wait()` |
| `IrisWorkerThrea` | `iris_vidioc_dqbuf` |

This also explains a porters report that had no answer:

> `2021-02-18  <birdzhang>` I can only use FM radio via start jolla-mediaplayer in
> terminal

Launched from a terminal there is no sandbox, so the sysfs write lands.

## Fix

Opening `/dev/radio0` is a permission the app *does* have. So tie the channel to that:
`iris_fops_open()` calls `radio_hci_smd_init()` (made non-static and declared in
`include/media/radio-iris.h`). `iris_fops_release()` already closes the channel, so the
lifetime stays symmetric, and the existing `chan_opened` guard keeps it idempotent for
anyone still writing `fmsmd_set` by hand.

The same commit makes the open **synchronous**. `smd_named_open_on_edge()` returns
before the remote end signals `SMD_EVENT_OPEN`, which the transport was discarding:

```c
	case SMD_EVENT_OPEN:
		break;
```

That is why every caller needed a sleep of its own after flipping `fmsmd_set`, and why
a session started too soon after a previous teardown still failed with `ENODEV` —
reproduced here as back-to-back sessions needing ~300 ms of retry. The transport now
completes on that event and `radio_hci_smd_register_dev()` waits for it, bounded at one
second.

## Verification

Run as `defaultuser` on `3.18.124-perf-g2f54418751cd`, **never touching `fmsmd_set`** —
modelling exactly what the sandbox permits:

```
open(/dev/radio0) took 3 ms
S_CTRL FM_RECV OK
events [0]  RADIO_READY yes
  tuned  93.50 MHz  signal=168  rxsub=3  stereo
  tuned 106.40 MHz  signal=163  rxsub=3
  tuned 100.10 MHz  signal=152  rxsub=3
teardown woke, RADIO_DISABLED yes
```

Headset connected (`/sys/class/switch/h2w/state` = 3, 4-pole), which is also the
antenna.

> **Correction to earlier notes.** Signal figures reported before this write-up came
> from the wrong offsets in `struct v4l2_tuner`: `audmode` is at 56 and `signal` at 60,
> not 64/68 — those are `afc` and `reserved[0]`. Any earlier "signal=142, stereo" is
> meaningless. The numbers above use the correct offsets.

## Why the sandbox was not just given the switch instead

Whitelisting the parameter in the sailjail profile cannot work: firejail remounts
`/sys` read-only over the writable one, so the write fails even when the path is
visible. Making FM depend on loosening that mount for every app is a far worse trade
than letting the driver own its own channel.

Opening the channel at boot instead — jusa's
[`enable-fm-smd-channel-during-boot.patch`](https://github.com/mer-hybris/qt5-qtmultimedia-plugin-mediaservice-irisradio/pull/3)
— would also work, but it puts WCNSS traffic into early boot, where `bluebinder` and
`wlan-module-load` already contend over the same SoC and needed careful ordering to
stop racing. Lazy-opening on first use keeps FM out of that entirely.
