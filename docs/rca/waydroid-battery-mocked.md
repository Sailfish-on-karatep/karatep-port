# Android always saw 85% and charging: Waydroid's health HAL fakes the battery

## Symptom

Inside the container the battery never moved:

```
$ waydroid shell -- dumpsys battery
  AC powered: true
  USB powered: true
  status: 2          <- charging
  level: 85
  voltage: 3600
  temperature: 350
  Charge counter: 1900000
```

The same numbers on a full battery, a flat one, plugged in or not. Anything
that reads `BatteryManager` was wrong: charging animations, low-battery
warnings, battery-saver decisions, and any app that shows a battery figure.

## Not a plumbing problem

The obvious guess -- that the container cannot see the host's power supplies --
is wrong. Waydroid's container shares the host's sysfs, and the real thing is
right there:

```
$ waydroid shell -- ls /sys/class/power_supply/
battery  bcl  bms  fg_adc  usb
$ waydroid shell -- cat /sys/class/power_supply/battery/capacity
100
```

`android.hardware.health` is not in `hosthals.xml` either, so nothing is being
redirected at the host. The container runs its own HAL,
`android.hardware.health@2.0-service.waydroid`, and that HAL reads sysfs
correctly -- `BatteryMonitor::init()` finds every node it wants.

## Cause

`hardware/waydroid/health/health_service.cpp` throws the result away.
`Health::update()` in the 2.0 default implementation does:

```cpp
    battery_monitor_->updateValues();                 // reads sysfs
    convertFromHealthInfo(health_info, &props);
    bool log = (healthd_board_battery_update(&props) == 0);   // board hook
    healthd_mode_ops->battery_update(&props);         // what the framework sees
```

and waydroid's board hook overwrote every field with constants -- 85%, 3600 mV,
350 (35.0 °C), charging, AC and USB online. Written for a desktop with no
battery at all, applied unconditionally to every host.

`healthd_board_init()` compounded it by pinning both periodic chore intervals to
-1, which disables healthd's polling entirely.

A side effect worth knowing: the HIDL calls that read the monitor directly --
`getCapacity()`, `getChargeStatus()`, `getChargeCounter()` -- bypass the board
hook, so they were returning the *real* values the whole time. Only the
`IHealthInfoCallback` path the framework actually uses was faked, which is why
`dumpsys battery` and a `BatteryManager` query could disagree.

## Fix

Fake it only when there is nothing to read
([commit](https://github.com/Sailfish-on-karatep/android_hardware_waydroid)):

```cpp
int healthd_board_battery_update(struct android::BatteryProperties* p) {
  if (p->batteryPresent) return 0;   // real values from sysfs, leave them alone
  ...
}
```

`batteryPresent` comes from the battery's own `present` node, or from whether
`BatteryMonitor` found a `POWER_SUPPLY_TYPE_BATTERY` supply at all -- exactly
the "this host has no battery" test the mock was written for.

The `healthd_board_init()` override goes with it. `BatteryMonitor::init()`
already sets both intervals to -1 itself when it finds no battery:

```cpp
    if (!mBatteryDevicePresent) {
        KLOG_WARNING(LOG_TAG, "No battery devices found\n");
        hc->periodic_chores_interval_fast = -1;
        hc->periodic_chores_interval_slow = -1;
    }
```

so forcing them had no effect in the batteryless case and stopped healthd
polling in the other. Removing it restores the AOSP defaults -- 60 s while
charging, 600 s on battery -- which means updates do not depend on kernel
uevents reaching the container's network namespace.

The change is generic: any Halium device gets its real battery, and a desktop
without one still gets the mock.

## Verified on hardware

Same instant, both sides:

| | host `/sys/class/power_supply/battery` | container `dumpsys battery` |
|---|---|---|
| capacity | 100 | level 100 |
| voltage | 4398930 µV | 4398 mV |
| temperature | 352 | 355 (read seconds apart) |
| status | Charging | status 2 |
| health | Good | health 2 |
| charge counter | 4119007 | 4114302 |
| USB online | 1 | USB powered: true, AC powered: false |

`AC powered` going from `true` to `false` is the fix working, not a regression:
the device is on USB, and the mock used to claim both.

## Building and installing

The binary is a vendor one (`proprietary: true` in `Android.bp`, installed to
`/vendor/bin/hw/`), so it builds against this tree -- the Waydroid vendor image
is HALIUM_11, the same API level as LineageOS 18.1 -- exactly like
`hwcomposer.waydroid.so`:

```sh
/opencloud/bin/habuild /parentroot/parentroot/opencloud/bin/build-waydroid-health.sh
# then, on the device, as root:
install -m755 android.hardware.health@2.0-service.waydroid \
        /var/lib/waydroid/overlay/vendor/bin/hw/
waydroid session stop && waydroid session start
```

**This is not packaged.** `/var/lib/waydroid/overlay/` currently holds three
hand-installed binaries -- `hwcomposer.waydroid.so`, `camera.waydroid.so` and
now the health service -- and `rpm -qf` says none of them is owned by a package.
They survive a rootfs reflash only because the overlay lives under
`/home/waydroid`. Worth fixing properly.

## See also

- [`waydroid.md`](../waydroid.md) — feature status
- [`waydroid-camera-hal-name.md`](waydroid-camera-hal-name.md) — the same
  build-and-drop-into-the-overlay route, for the camera HAL
