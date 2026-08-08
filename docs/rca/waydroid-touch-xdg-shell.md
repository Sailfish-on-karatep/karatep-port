# Waydroid touch is dead under lipstick: the 5.1 xdg_shell regression

## Symptom

Waydroid's Android UI renders correctly under lipstick and is completely
unresponsive to touch — single-window and multi-window alike. The Android side
is provably healthy:

```
InputReader: Device added: id=4, name='wayland_touch', sources=TOUCHSCREEN
InputReader: Device reconfigured: id=4, name='wayland_touch', size 1080x1920, orientation 0, mode 1, display id 0
TouchInteractionService: Touch service connected: user=0
```

`dumpsys input` shows the device enabled, the launcher focused. Nothing ever
writes `/dev/input/wl_touch_events`.

The same container, on the same device, with the same seat, **has working touch
under `waydroid-runner`** — the nested-compositor app. That is the clue the whole
diagnosis turns on.

## Root cause

Waydroid's Android-side Wayland client
([`hwcomposer/wayland-hwc.cpp`](https://github.com/waydroid/android_hardware_waydroid))
binds both shells and **prefers xdg_shell whenever the compositor advertises it**:

```c
if (display->wm_base) {
    window->xdg_surface = xdg_wm_base_get_xdg_surface(display->wm_base, window->surface);
    ...
} else if (display->shell) {
    window->shell_surface = wl_shell_get_shell_surface(display->shell, window->surface);
    ...
} else {
    abort();
}
```

Lipstick gained xdg_shell in a single commit —
[`4b9745ef` "[compositor] Add XDG shell support", 2026-03-30](https://github.com/sailfishos/lipstick/commit/4b9745ef) —
a hand-rolled implementation under `src/compositor/xdgshell/`, registered
unconditionally in `LipstickCompositor`'s constructor:

```cpp
addGlobalInterface(new XdgShellGlobal);
```

There is no environment variable, setting, or feature gate that disables it.

That commit shipped in **Sailfish OS 5.1** — the exact release where Waydroid
touch stopped working (adampigg, IRC, 2026-05-27: *"5,1 appears to break
waydroid"*, and the next day *"waydroid issue seems to be some protocol issue in
new lipstick"*).

So: before 5.1, lipstick advertised only `wl_shell`, and Waydroid took the
`wl_shell` path that had worked for years. From 5.1, `xdg_wm_base` appears and
Waydroid switches to it.

**What is proven is that the shell choice is the variable. Which side is at
fault is not yet proven.** Lipstick's xdg implementation is deliberately partial
— the commit says so: *"Implement only the basic parts needed to show maximized
toplevel windows and position popups on the screen."* But it is not inert on
input: measured against lipstick, an xdg toplevel is configured **and
activated**:

```
xdg_toplevel.configure 1080x1920 states=[1, 4]     # 1 = MAXIMIZED, 4 = ACTIVATED
```

and the PR author notes that xdg popups "will receive focus via `focusOnTouch`",
which means touch does reach xdg surfaces in their testing. Equally, Waydroid's
own touch handler does not drop events: `display->layers` is a `std::map`
indexed by `wl_surface*` and read with `operator[]`, so a missing entry
default-constructs to offset 0,0 rather than causing an early return. The only
early return is on a NULL surface.

So the fault is somewhere in the interaction, and locating it needs a protocol
trace of the real client, not more inference.

## Evidence

Measured on device with a purpose-written Wayland client
(`prebuilts/waydroid/wlinfo.py` — raw wire protocol, since the device has no
Wayland bindings). Against lipstick:

```
=== globals advertised by the compositor ===
  wl_seat                  v3
  wl_shell                 v1
  xdg_wm_base              v6            <-- new in 5.1
  qt_surface_extension     v1
  ...
=== wl_seat (v3) capabilities ===
  raw = 0x7   pointer: True   keyboard: True   touch: True
```

The seat *does* advertise touch, so the client definitely creates its `wl_touch`
and the FIFO. The seat is not the problem.

Now the natural experiment. `waydroid-runner`'s nested compositor is built on
Sailfish's `libQt5Compositor.so.5`, and that library contains no xdg_shell at
all:

```
-- /usr/lib64/libQt5Compositor.so.5
   xdg_wm_base            0        <-- not supported, cannot be advertised
   zxdg_shell_v6          0
   wl_shell               2
   qt_surface_extension   2
```

| Compositor | Advertises `xdg_wm_base`? | Path Waydroid takes | Touch |
|---|---|---|---|
| lipstick (SFOS 5.1) | yes, v6 | xdg_shell | **dead** |
| waydroid-runner (`libQt5Compositor`) | no | wl_shell | **works** |

Same client, same device, same seat, same container. The only variable is which
shell protocol the compositor offers. That isolates the fault to lipstick's
xdg_shell path.

## Why this was hard to see

`waydroid-runner` looks like it "works because it is a nested compositor", which
suggests the problem is something about direct lipstick rendering. It is not.
The nesting is incidental — what matters is that the nested compositor is too
old to speak xdg_shell, so it forces Waydroid down the good path. Upstream's own
Sailfish packaging recommends `waydroid-runner` and notes vaguely that closing
the UI in lipstick means it "will not open again due to some issue in
interaction between Lipstick and Waydroid", without naming the cause.

## Dead ends worth recording

- **`kill -9` on a HAL service proves nothing here.** See
  `waydroid-poisons-host-cgroups.md`.
- **Probing with a synthetic Wayland client that maps its own surface does not
  work on Sailfish.** A bare `wl_shell`/xdg client is not a Sailfish
  application; lipstick's homescreen never shows or focuses it, so it receives
  no touch by either route and the test says nothing. Injected taps via a
  virtual uinput touchscreen (`prebuilts/waydroid/inject_touch.py`) reach
  lipstick but not an unshown surface.
- **libwayland requires client-allocated object ids to be dense.** A hand-written
  client that jumps from id 3 to id 10 gets
  `invalid arguments for wl_registry#2.bind`, which looks like a wire-format bug
  and is not one.

## Why xdg_shell was added, and why it must not simply be removed

From the original PR ([#68](https://github.com/sailfishos/lipstick/pull/68), by
affenull2345, superseded by [#69](https://github.com/sailfishos/lipstick/pull/69)):

> *"Tested with `weston-demo` … and a few GTK-based apps via Flatpak."*

xdg_shell exists in lipstick to run **foreign, non-Qt Wayland apps — GTK via
Flatpak**. It is a deliberate feature with real users, not an accident. The
porter archive shows this had been wanted for years, and that Qt could not
provide it:

| Date | Who | What |
|---|---|---|
| 2019-12-30 | r0kk3rz | *"i was thinking some kind of shim for the xdg-shell calls"* |
| 2020-01-06 | r0kk3rz | *"i do wonder if we can hack in xdg-shell support into the flatpak-runner thing"* |
| 2021-09-06 | deathmist | piggz *"tried but gave up … the OS Qt version is just too old … namely XDG-shell … it needs Qt 5.12"* |

That last one also explains why `libQt5Compositor.so.5` has no xdg_shell at all,
and therefore why lipstick had to hand-roll its own rather than use Qt's.

**So "make lipstick stop advertising xdg_wm_base" is not a safe rollback.** It
would restore Waydroid at the cost of every GTK/Flatpak app on 5.1. Rejected.

The fix has to make Waydroid work *with* xdg_shell — either by correcting
lipstick's xdg input path, or by adapting the Waydroid side, depending on where
the trace lands.

## Next measurement

Everything above narrows the fault to the lipstick↔Waydroid xdg interaction
without saying which side drops the touch. The decisive test is a Wayland
protocol trace of the real client while the screen is touched: if lipstick never
sends `wl_touch.down`, the fault is lipstick's; if it sends and Waydroid does not
write the FIFO, the fault is Waydroid's.

`mount_overlays = True` on this port, so the container's Android side can be
instrumented through `/var/lib/waydroid/overlay/` without rebuilding any image.

## See also

- `waydroid-devpts.md` — the kernel work that got the container starting.
- `waydroid-poisons-host-cgroups.md` — the host camera fault Waydroid causes.
