# FM radio: WCNSS never opens the APPS_FM SMD channel on a fresh boot

**Status: in progress — the Sailfish side is fixed and verified, one device-level
blocker remains and is not root-caused.**

## Summary

FM radio was never "unimplemented" here. The kernel driver, both userspace packages
and the whole audio policy chain were already present; what was missing was
permission to open the transport, which `droid-config` now grants. With that fixed
the full tune path was proven working on hardware.

But on a **fresh boot** the transport cannot be opened at all: the `APPS_FM` SMD
channel to WCNSS stays `CLOSED`, and the thread that writes the enable switch wedges
in uninterruptible sleep. The same write succeeded instantly on a long-running boot,
so the capability is real and something about early boot state prevents it.

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

## Proof the hardware works

On a boot that had been up ~37 h, as `defaultuser`, with only the packaged
permissions:

| step | result |
|---|---|
| write `fmsmd_set=1` | accepted immediately |
| `VIDIOC_QUERYCAP` | `driver=radio-iris`, `card=QTI FM Radio Transceiver` |
| set state `FM_RECV` | OK |
| `VIDIOC_G_TUNER` | band **87.5–108.0 MHz**, read back from the chip over HCI |
| tune 91.1 / 98.3 / 104.0 MHz | each confirmed by `VIDIOC_G_FREQUENCY` readback |

The band and frequency readbacks come from WCNSS, not from driver state, so the FM
core really was responding.

## The open blocker

On every **fresh boot** since, the same write wedges:

```
$ grep APPS_FM /sys/kernel/debug/smd/ch
11|APPS_FM            |P|APPS |CLOSED |0x02000|0x00000|0x00000|dcciwrsb|0x00000
```

The writing task sits in `D` state with `wchan = param_attr_store`, so `SIGALRM`
cannot even be delivered — it is stuck inside the kernel, in
`radio_hci_smd_register_dev()`'s `smd_named_open_on_edge("APPS_FM", SMD_APPS_WCNSS, …)`.
Watched for 60 s: the channel never leaves `CLOSED` and the writer never returns.
`radio-iris` logs nothing at all, because it never gets that far.

Note the channel **exists** (id 11) — this is not a missing channel returning
`-ENODEV`. The APPS side is waiting for WCNSS to open its end, and WCNSS never does.

Once a write has wedged it holds the driver's `fm_smd_enable` mutex, so every later
attempt blocks too. Only a reboot clears it.

### Ruled out

- **WCNSS not up** — `wcnss = ONLINE` in `/sys/bus/msm_subsys/devices/*/state`, and
  `wcnss: IRIS Reg: 91100004` appears at boot.
- **Bluetooth not powered** — `hci0` is registered, rfkill unblocked, `bluebinder`
  active and reporting "Successfully initialized vhci bluetooth". Worth noting that
  `bluebinder` bridges BT through **vhci/binder**, not the kernel SMD path, so BT
  never exercises the WCNSS SMD stack at all — which is also why mido's habit of
  hanging `droid-fm-up.service` off `bluetooth.service` buys nothing here.
- **Waydroid interference** — `waydroid-container` was active in both the working and
  the failing case.
- **Permissions** — the write reaches the driver and blocks inside it; `EACCES` is
  long gone.

### Not yet explained

Why the identical write succeeded immediately on a ~37 h uptime boot and wedges on a
fresh one. The obvious suspects (WCNSS state, BT power, Waydroid) are all ruled out
above, so the differentiator is still unknown — possibly a WCNSS subsystem restart
having occurred, or some FM-core power-up that Android's `libfm-hci` performs and
which nothing on Sailfish does. There is **no vendor FM HAL service binary** on this
device (only `vendor.qti.hardware.fm@1.0.so`, an interface library), so FM on
LineageOS 18.1 here is driven by the system-side FMRadio app, not a vendor service we
could start.

`APPS_FM` returns **zero hits** across eleven years of `#sailfishos-porters` logs, so
there is no prior art for this symptom at all.

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
