# GPS and Bluetooth inside Waydroid

It is tempting to file these together as "not bridged yet". They are not the
same problem, and they did not end the same way: **GPS is solved** — the
container had it all along, but by seizing the host's HAL, and it now goes
through a bridge so both stacks can hold positioning at once. **Bluetooth is
not fixable at this layer**: the container ships no Bluetooth stack, and the
controller is already spoken for. Measured on karatep, 2026-08-09/10.

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

## GPS — solved, via a bridge to the host's positioning stack

**Status: working.** An Android app in the container requests location, gets it
from Sailfish's own positioning stack, and Sailfish keeps ownership of the GNSS
HAL throughout.

### What was actually wrong

Not "Waydroid has no location support" — an earlier version of this document
said that, and it was wrong. Waydroid *does* reach GPS on a Halium device, by
the worst possible route.

`/system/etc/hosthals.xml` in the container lists `android.hardware.gnss`, and
Waydroid's patched `libhidlbase` consults that list to redirect HIDL
`getService()` at the **host's** hwbinder. So an Android app asking for location
drove the host's `android.hardware.gnss@2.1-service-qti` directly. `IGnss`
carries a single callback, so that takes the engine away from
`geoclue-providers-hybris` — which is
[waydroid#299, "GPS broken on Ubuntu Touch after Waydroid was launched"](https://github.com/waydroid/waydroid/issues/299).

This was easy to miss because `lshal --neat -i` inside the container does **not**
enumerate host-proxied HALs — it listed no GNSS at all. What settles it is which
values the framework reports: with the redirect active it logged
`name=qcom;MPSS.JO.2.0.C1-102262`, `yearOfHw=2015`, `capabilities=2083`, i.e. the
real modem.

### The fix

Three parts, all now in the packaging:

1. **[`waydroid-gnss`](https://github.com/Sailfish-on-karatep/waydroid-gnss)** —
   a host daemon registering `android.hardware.gnss@1.0::IGnss/default` on the
   *container's* hwbinder and answering it from **Geoclue** over D-Bus. Because
   it consumes geoclue rather than binding the HAL, the host keeps sole
   ownership and both stacks are ordinary clients. `start()` takes a geoclue
   reference and `stop()` drops it, so the GPS is only powered while Android is
   actually navigating.
2. **Launched from `session_manager`** (waydroid patch 0005). Geoclue is a
   *session* bus service, so unlike `waydroid-sensord` — which runs as root from
   the container manager because sensorfw is on the system bus — this has to run
   as the user. The container's hwbinder node comes from waydroid's own config.
3. **Overlay setup** (waydroid patch 0006): strip `android.hardware.gnss` from
   `hosthals.xml` so the host redirect stops winning, and declare the HIDL
   service in a VINTF manifest fragment. The second is not optional — with the
   redirect gone and no VINTF entry the framework binds *nothing*, and GNSS
   disappears from logcat entirely.

HIDL 1.0 is deliberate: Android 13 falls back AIDL → 2.1 → 2.0 → 1.1 → 1.0, so
it is the smallest surface the framework still reaches.

### Verified on hardware

Real fixes reach Android, indoors:

```
last location=Location[gps 8.806013,78.129759 hAcc=9.648 alt=-81.0 bear=317.5
                       {Bundle[{satellites=8, maxCn0=29, meanCn0=22}]}]
Number of location reports: 278
```

matching the host's geoclue exactly (`8.80601 78.1298 ... 9.648`). The altitude
is not wrong: NMEA reports 13.0 m MSL with -94.0 m geoid separation, and Android
wants height above the WGS84 ellipsoid, so 13.0 + (-94.0) = -81.0.

An early check that suggested "no fix, needs sky view" was simply taken while the
engine was still acquiring — `GetStatus = 2` a minute in means nothing.

### Satellites

`gnssSvStatusCb` is bridged too, so the container gets a real sky view rather
than `satellites=0, maxCn0=0` — which is also what made an app report that SBAS
was unavailable.

Geoclue folds the constellation into the PRN with the usual NMEA offsets
(GLONASS +64, BeiDou +200, Galileo +300, SBAS -87), the inverse of what
`binderlocationbackend_hidl.cpp` does on the way in, so inverting it recovers
both the constellation and the in-constellation svid. Without that every
satellite arrives claiming to be GPS.

`GnssSvStatus` is a fixed 1540-byte struct copied straight into the parcel, so
its layout is pinned with `G_STATIC_ASSERT` rather than trusted.

### A trap worth recording

The upper layer for the system overlay is `overlay_rw/system`, and its contents
mirror the **container root** — so a file destined for `/system/etc/x` lives at
`overlay_rw/system/system/etc/x`. Writing it at `overlay_rw/system/etc/x`
instead puts a real directory over Android's `/etc -> /system/etc` symlink. The
container then starts `init` and `zygote` and never reaches `system_server`:
56 processes, `zygote64` spinning as `nobody`, `sys.boot_completed` never set,
empty logcat.

## Prior art

Checked after the fact, and it exists — an earlier version of this document
claimed there was none, on the strength of an IRC search alone.

- [waydroid#2208](https://github.com/waydroid/waydroid/issues/2208), open since
  Jan 2026: implements a GNSS **AIDL** HAL inside waydroid itself, with the same
  stated motivation — *"prevent Waydroid from taking control of host GNSS HAL"*.
  Its instructions to remove the `hosthals.xml` entry and add a VINTF manifest
  are what put us onto both requirements.
- [sssemil/waydroid_geoclue_bridge](https://github.com/sssemil/waydroid_geoclue_bridge)
  — Rust, "Heavy WIP", 7 commits. Not usable here: it targets **GeoClue2**,
  while Sailfish uses the geoclue 0.12 API, and it implements no HAL at all —
  it writes JSON to a file inside the container that nothing reads.
- Open and unresolved: [#226](https://github.com/waydroid/waydroid/issues/226),
  [#275](https://github.com/waydroid/waydroid/issues/275).

The `#sailfishos-porters` archive still has **zero** hits for "waydroid gps",
"waydroid bluetooth" or "waydroid gnss".

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

## Recommendation

- **GPS: done.** `waydroid-gnss` sources from geoclue, mirroring
  `waydroid-sensors`. Both stacks can hold positioning at once because neither
  owns the HAL.
- **Bluetooth: not worth doing**, and not fixable at this layer.
