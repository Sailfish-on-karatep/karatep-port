# FM radio: enabling the iris tuner

**Status: done — tuner verified end to end on a fresh boot, on the patched kernel.
Audio and reception still need a headset.** An unrelated kernel deadlock used to block
this intermittently; it is fixed and tracked separately in
[msm-thermal-param-lock-deadlock.md](msm-thermal-param-lock-deadlock.md).

> **Correction.** The first version of this document concluded that "WCNSS never opens
> the `APPS_FM` SMD channel on a fresh boot". That was wrong. The write never reached
> the FM transport at all: it was stuck on the kernel's global module-parameter mutex,
> held by an unrelated `msm_thermal` deadlock. `APPS_FM` stayed `CLOSED` simply because
> nothing ever asked it to open. With that deadlock absent, FM works on a fresh boot.
> The section below has been rewritten; the mistaken reasoning is called out at the end.

## Summary

FM radio was never "unimplemented" here. The kernel driver, both userspace packages
and the whole audio policy chain were already present; what was missing was
permission to open the transport, which `droid-config` now grants.

With that fixed the tuner works, verified as `defaultuser` on a fresh boot. What made
this take a while to establish is that on *some* boots every module-parameter write on
the system is deadlocked by `msm_thermal` before FM is ever reached, which made the fix
look like it had not worked. That is a separate, system-wide kernel bug with its own
write-up.

## How FM is wired on this device

`radio-iris` reaches the WCNSS FM core over an SMD channel opened by
`radio-iris-transport`. That driver has **no `module_init` at all** — the whole thing
is a single `module_param_call` (`drivers/media/radio/radio-iris-transport.c:45`):

```c
module_param_call(fmsmd_set, hcismd_fm_set_enable, NULL, &fmsmd_set, 0644);
```

so the channel opens only when something writes `1` to
`/sys/module/radio_iris_transport/parameters/fmsmd_set`, and closes on `0`.
`radio_hci_smd_deregister()` also resets the parameter to `0` itself, making this a
per-session cycle rather than a one-off at boot.

This matters because hadk-faq's FM section suggests building
`CONFIG_RADIO_IRIS_TRANSPORT=m` and modprobing it from `droid-fm-up.service`. **That
advice does not transfer to this kernel** — with no `module_init`, loading the module
does nothing at all. Only the parameter write opens anything.

Android drives the write from `init.qcom.rc`:

```
on property:hw.fm.init=1
    write /sys/module/radio_iris_transport/parameters/fmsmd_set 1
```

with `libfm_jni` setting `hw.fm.init`. Nothing on Sailfish sets that property, and
nothing needs to: `qt5-qtmultimedia-plugin-mediaservice-irisradio` has done the write
itself since 0.6.0 ("Open v4l fd after smd is initialized", JB#48080).
`fmradioiriscontrol.cpp:37` hardcodes the same path.

## What was actually broken (fixed)

The plugin runs as `defaultuser`; `init.qcom.rc`'s `on boot` leaves the parameter
`system:system 0660`. `defaultuser` is not in group `system`, so the write failed
with `EACCES` — silently, inside an unchecked `if (f.open(QFile::WriteOnly))`. The
plugin then opened `/dev/radio0` anyway and every ioctl returned `ENODEV`:

```
iris_radio: __radio_hci_request, hci dev is null
iris_radio: Error while setting the frequency : -22
```

`droid-config-karatep` now ships `droid-fm-up.service`, which hands the parameter to
the `audio` group, plus `999-droid-fm.rules` for `/dev/radio0` and the `fmradio.conf`
xpolicy symlink. See the "Enable FM radio" and "Wait for droid-hal-init" commits.

### The ordering trap

`After=droid-hal-init.service` is **not** sufficient. `droid-hal-init.service` is
`Type=simple`, so systemd calls it started at fork while Android init works through
`on boot` asynchronously. Measured: the unit ran and exited 0 at 13 s into boot, and
init's chown still landed afterwards, putting the group back to `system`.

The script therefore waits for the chown rather than racing it. The kernel creates
module parameters `root:root` — 532 of the 533 on this device still are — and the
sole exception is this one, precisely because `init.qcom.rc` chowns it. That makes
`system:system` an unambiguous "`on boot` has been processed" signal for exactly the
file in question. Verified across reboots: the parameter now comes up `system:audio
0660` and `/dev/radio0` `audio:audio 0660`.

## Proof it works

Verified as `defaultuser`, on a **fresh boot**, with only what the packages install
and no manual intervention:

```
uptime: 53.99
param_lock free? enabled=N
running as uid=100000
OK  wrote fmsmd_set=1  (exactly what the Media app's plugin does)
OK  FM receiver on
OK  tuned 91.1  -> chip reports 91.10 MHz  (band 87.5-108.0)
OK  tuned 98.3  -> chip reports 98.30 MHz  (band 87.5-108.0)
OK  tuned 104.0 -> chip reports 104.00 MHz (band 87.5-108.0)
OK  FM off, transport released
EXIT=0
```

The band and frequency readbacks come from WCNSS over HCI, not from driver state, so
the FM core really is responding. The same sequence had also succeeded earlier on a
boot that had been up ~37 h.

## The intermittent blocker (not an FM bug) — since fixed

On *some* boots the very first step above used to hang: the write to `fmsmd_set` never
returned and the task sat unkillable in `D` state. This was **not** an FM fault and
nothing in the FM stack could have fixed it.

`kernel/params.c` guards all module parameters with a single global mutex, and
`msm_thermal`'s `enabled` parameter has a `->set()` that can deadlock while holding
it — `kthread_stop()` on a thread that sleeps in `wait_for_completion_interruptible()`
and therefore never observes `kthread_should_stop()`. Once that happens, every
module-parameter read and write on the system blocks forever, FM's among them.

Full analysis, including how to tell the lock holder from the waiters by the
`param_attr_store` offset: [msm-thermal-param-lock-deadlock.md](msm-thermal-param-lock-deadlock.md).

Fixed in the kernel by `07b2c9ff34ff` (merged to `hybris-18.1` as `ce665cd37eae`), and
FM verified working on the patched kernel. On any older kernel the symptom is still
worth recognising — a boot is affected if this hangs instead of printing `N`:

```sh
cat /sys/module/msm_thermal/parameters/enabled
```

### What this corrects

The first pass at this investigation concluded WCNSS was never opening the `APPS_FM`
SMD channel, on the strength of the channel showing `CLOSED` in
`/sys/kernel/debug/smd/ch` while the writer was wedged. That reasoning was backwards:
the channel was `CLOSED` because nothing ever reached the transport to open it. Two
things should have given it away sooner —

- `radio-iris` logged **nothing at all**, not even an error, which is impossible if
  the driver's `->set()` had actually been entered;
- `smd_named_open_on_edge()` cannot block indefinitely in the first place — its worst
  case is a single `msleep(250)` before it returns 0.

WCNSS, Bluetooth and Waydroid were all correctly ruled out as differentiators; the
mistake was assuming the remaining suspect had to be inside the FM path at all.

## Still untested: audio and reception

Both need a headset, which doubles as the FM antenna, and none was attached:

- Every device in `fmradio.conf` is gated on `droid.input.external@equals:true`, and
  the HAL's `input-fm_tuner` source port reports `not available` with nothing plugged
  in — exactly like `input-wired_headset`.
- RSSI read a flat `142` at every frequency with `audmode=0` (mono), which is what an
  antenna-less tuner looks like.

What *is* confirmed on the audio side: the HAL exposes `input-fm_tuner` at priority
200, `xpolicy.conf` maps `$droid_source_input_fmradio` to it, the ohm policy rules in
`/usr/share/policy/rules/basic/` already know `fmradio`, and PulseAudio restarts
clean with the snippet enabled, `module-policy-enforcement` still loaded.

## Footnote: the "bogus stub" was a flattened symlink

`sparse/etc/pulse/xpolicy.conf.d/fmradio.conf` was added in `2125abf` as a 21-byte
regular file containing the text `fmradio.conf.disabled`, took all audio down with
it, and was removed in `8ccde43` as a "bogus stub". It was not a stub, and not a
botched attempt to *disable* the snippet: fp2-sibon and mido both ship the **enabled**
form as a symlink to `fmradio.conf.disabled`, and git records the old blob
(`3f2536f8`) as byte-identical to the symlink restored here — same content, mode
`100644` instead of `120000`. It was this symlink, flattened on copy, and
`module-policy-enforcement` choked parsing the target name as config. It is committed
`120000` now, so it cannot flatten again.
