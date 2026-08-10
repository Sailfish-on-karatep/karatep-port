# Touch dead after unlocking the screen — unresolved

## Symptom

Reported once, 2026-08-10: the screen was unlocked with a Waydroid app already
in the foreground, the app was visible, and touch did not work. Closing the
Waydroid session and starting a new one restored it.

**Status: not reproduced, cause unknown.** This page exists so the two theories
already eliminated are not re-run.

## Why it looked like the resize bug

Touch had failed silently once before, after a display-size change, and the fix
was `do_hotplug()` — recreating the touch FIFO and raising a hotplug so Android's
EventHub re-enumerates the device
([rca](waydroid-video-decode-split-mode.md) for the shape of that work, and
`hwcomposer/wayland-hwc.cpp`). The unlock symptom looks identical from outside:
everything renders, nothing crashes, nothing is logged, touch is simply dead.

## Theory 1 — the seat drops its touch capability across a blank. Disproved.

`seat_handle_capabilities()` tears down the touch FIFO when the compositor
withdraws `WL_SEAT_CAPABILITY_TOUCH` and recreates it when the capability comes
back — but the re-add path never raised a hotplug. If a blank withdrew the
capability, EventHub would go on reading the *deleted* inode while the
hwcomposer wrote to the new one: dead touch, no log, exactly the reported
symptom.

Measured instead of assumed. A full lock/blank/unblank/unlock cycle driven with
`mcetool` — verified real, `mcetool` reported `Display state: off` — produced
**no capability transition at all**. Only the single `Seat gained touch
capability` logged at startup ever appears.

## Theory 2 — the blank disturbs the container's input path. Disproved.

Comparing the container either side of the same cycle shows nothing changes:

```
BEFORE / AFTER  (identical)
  4: wayland_touch, 2: wayland_keyboard, 3: wayland_pointer
  /dev/input/wl_touch_events   unchanged, still the FIFO created at startup
  gpstest window: inputConfig=0x0, touchableRegion=[0,0][1080,1920]
```

and no hwcomposer or Wayland log activity across the cycle whatsoever. From the
container's point of view an mcetool blank/unblank does not happen.

## What was kept anyway

The hotplug on capability return is still in the fork, and the two
`ALOGI("Seat gained/lost touch capability")` lines with it. Not as a fix for
this — the commit history says so explicitly — but because the gap is real: if
the capability ever *is* withdrawn and restored, touch dies silently for the
same reason a resize used to kill it. The logging is what made the above
measurable.

## Where to look next

- **Focus.** Lipstick routes touch to the focused surface, and the xdg-shell
  investigation ([rca](waydroid-touch-xdg-shell.md)) showed how easily a
  Waydroid surface ends up rendered but not focused. That would also explain why
  restarting the session fixes it, which the stale-FIFO theory explains equally
  well — so it is not a discriminator on its own. Instrument the `wl_shell`
  focus / `wl_touch` enter-leave path and reproduce.
- **Whether the real trigger is the unlock at all.** The one observation
  coincided with a period of repeated session restarts and package installs
  during testing, so the session may simply have been in a bad state. Worth
  confirming it recurs on an untouched session before hunting further, and
  noting whether the screen went off by timeout or by the power key.
