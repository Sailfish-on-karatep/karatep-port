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
`wl_shell` path that had worked for years. From 5.1, `xdg_wm_base` appears,
Waydroid switches to it, and lands on lipstick's brand-new xdg path — which
renders the surface but does not route touch to it.

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

## Fix

Not yet applied — see the options and the reasoning in the status report. The
shape of it is that lipstick must either stop advertising `xdg_wm_base` (which
restores the exact pre-5.1 behaviour) or route input on its xdg path. Both are
changes to lipstick, which is open source (`sailfishos/lipstick`), so this is
reportable upstream with a precise root cause and a one-line reproduction:

> A client that binds `xdg_wm_base` instead of `wl_shell` renders under lipstick
> 5.1 but receives no `wl_touch` events.

## See also

- `waydroid-devpts.md` — the kernel work that got the container starting.
- `waydroid-poisons-host-cgroups.md` — the host camera fault Waydroid causes.
