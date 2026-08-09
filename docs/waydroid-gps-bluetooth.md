# GPS and Bluetooth inside Waydroid

Both are missing from the container, and it is tempting to file them together as
"not bridged yet". They are not the same problem: **GPS is a missing HAL with
everything else in place, Bluetooth is a missing stack with the hardware already
spoken for.** Measured on karatep, 2026-08-09, with GPS and Bluetooth both
enabled in Sailfish settings.

## How the host owns the hardware

Both radios are driven by an Android HAL running on the **host's** hwbinder
(`/dev/hwbinder`), with a Sailfish-side client bridging into the native stack:

| Radio | Host HAL | Host client | Feeds |
|---|---|---|---|
| GNSS | `android.hardware.gnss@2.1-service-qti` (pid 1554, user `gps`) + `loc_launcher` | `geoclue-hybris` (links `libgbinder.so.1`, binds `android.hardware.gnss.IGnss/default`) | Sailfish positioning, and `org.freedesktop.Geoclue.Providers.Hybris` on D-Bus |
| Bluetooth | `android.hardware.bluetooth@1.0::IBluetoothHci` | `bluebinder` (binds that interface, writes to `/dev/vhci`) | `bluetoothd` (BlueZ) over the virtual HCI device |

The container has its own binder domain — `hwpuddlejumper` is mounted as its
`/dev/hwbinder` — so nothing the host publishes is visible to it. The host's
real hwbinder *is* exposed inside as `/dev/host_hwbinder`, but Android's
framework only ever looks at its own.

## GPS — a missing HAL, nothing else

Everything above the HAL is already present and working in the container:

```
feature:android.hardware.location
feature:android.hardware.location.gps
feature:android.hardware.location.network
96  location: [android.location.ILocationManager]
```

and `dumpsys location` shows the `gps`, `fused` and `passive` providers all
registered, `enabled=true`, `allowed=true`. They sit at `ProviderRequest[OFF]`
with `mStarted=false` for one reason: **no `android.hardware.gnss@*::IGnss` is
registered on the container's hwbinder.** `lshal` inside the container lists
none. Waydroid has no location support of any kind — `grep -rin "gnss\|location\|gps"`
across upstream's `tools/` and `data/` returns exactly one hit, in an AppStream
metadata file.

### The design that fits

Waydroid already has an established pattern for "the host owns the hardware,
bridge it into the container", and it is `waydroid-sensors`:

- a **host-side daemon** (`waydroid-sensord`, ~2100 lines) that
- registers `android.hardware.sensors@1.0::ISensors` / `default` as a gbinder
  local object on the *container's* hwbinder node, and
- implements it against **sensorfw** on the host, so Sailfish keeps owning the
  sensors and the container becomes a second consumer.

Waydroid launches it itself, handing it the right binder node, and falls back to
a stub when it is absent:

```python
# tools/actions/container_manager.py:170
if which("waydroid-sensord"):
    tools.helpers.run.user(args, ["waydroid-sensord", "/dev/" + args.HWBINDER_DRIVER],
                           output="background")
# tools/helpers/images.py:151
if which("waydroid-sensord") is None:
    props.append("waydroid.stub_sensors_hal=1")
```

A `waydroid-gnss` is the exact analogue: register `IGnss`/`default` on the
container's hwbinder, and source fixes from **geoclue over D-Bus** rather than
from the GNSS HAL directly. That matters — going to the HAL directly would make
the daemon a second `IGnss::setCallback()` client competing with
`geoclue-hybris`, and Sailfish would lose positioning whenever Waydroid asked
for it. Consuming geoclue instead means the host keeps sole ownership of the
HAL and both stacks are ordinary clients, which is the same shape as audio
(container → host PulseAudio) and sensors (container → host sensorfw).

`IGnss` is a wider interface than `ISensors`, but Android tolerates a minimal
implementation: `setCallback`, `start`, `stop`, `cleanup`, `setPositionMode`,
`injectTime`, `injectLocation`, `deleteAidingData`, returning null for the
optional sub-interfaces (`IGnssMeasurement`, `IGnssBatching`, `IAGnss`,
`IGnssConfiguration`, …). `GnssLocationProvider` works against that.

**Cost:** a new repo plus a small patch to the waydroid fork to launch it — the
same two-part shape as `waydroid-sensors`. Not a configuration change.

## Bluetooth — two independent blockers

### 1. The container has no Bluetooth stack

This is the decisive one, and it is not about HALs at all. The Waydroid
lineage-20 system image simply does not ship Bluetooth:

```
pm list packages | grep -i blue     -> (nothing, out of 152 packages)
ls /system/app/Bluetooth            -> No such file or directory
ls /system/priv-app/Bluetooth       -> No such file or directory
ls /apex/com.android.bluetooth*     -> No such file or directory
```

`feature:android.hardware.bluetooth` is declared and `bluetooth_manager` is
registered, but those are framework-side stubs with nothing behind them — which
is exactly what `dumpsys bluetooth_manager` reports:

```
enabled: false
state: OFF
address: null
name: null
```

There is nothing to enable. Publishing an `IBluetoothHci` into the container
would change none of this.

### 2. The transport is already exclusively owned

Even with a stack, HCI is a single transport and `bluebinder` holds it:
it binds `android.hardware.bluetooth@1.0::IBluetoothHci` on the host and pumps
it into `/dev/vhci` for BlueZ. `IBluetoothHci::initialize()` takes one callback.
Handing the container the HAL means taking the controller away from
`bluetoothd`, i.e. trading Sailfish Bluetooth for Waydroid Bluetooth.

The audio-style answer — proxy at a higher level, so both stacks are clients of
one owner — has no equivalent here: BlueZ does not re-export an HCI transport,
and Android's stack cannot be pointed at BlueZ's D-Bus API.

Note also that on karatep Bluetooth and WLAN share one WCNSS SoC and already
needed sequencing to stop them fighting at boot
([porting notes](porting-notes.md)); adding a third contender is not attractive.

**Conclusion:** container Bluetooth is not a porting task on our side. It needs
Waydroid's system image to ship the Bluetooth stack first, and then a way to
share the controller that does not currently exist. Recommend leaving it.

## No prior art

`bin/ircgrep.sh` returns **zero** hits for "waydroid gps", "waydroid bluetooth"
and "waydroid gnss" across eleven years of `#sailfishos-porters`. Nobody in that
channel has discussed either.

## Recommendation

- **GPS: worth doing**, as a `waydroid-gnss` host daemon sourcing from geoclue,
  mirroring `waydroid-sensors`. Everything else is already in place, so the HAL
  is the whole job.
- **Bluetooth: not worth doing**, and not fixable at this layer.
