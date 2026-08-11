# Touch stops responding for ~20 s: system_server ANRs while the device thrashes

## Symptom

Reported 2026-08-11: a Waydroid app was in the foreground, the Sailfish terminal
was opened by accident, and on switching back to Waydroid touch did nothing for
a while. It came back on its own, and the session kept running.

This is **not** [the unresolved unlock bug](waydroid-touch-after-unlock.md) —
that one only ever recovered by restarting the session — and it is not
[the xdg_shell regression](waydroid-touch-xdg-shell.md), which never delivered a
single touch event. Here the events were delivered, timestamped and dispatched;
Android just could not act on them in time.

## What the logs actually say

Two ANRs a minute apart, both `Input dispatching timed out`:

```
08-11 15:23:49  E/ActivityManager: ANR in com.android.systemui
                Reason: Input dispatching timed out (2ce521c NotificationShade
                        (server) is not responding. Waited 5068ms for MotionEvent)
08-11 15:24:53  E/ActivityManager: ANR in system
                Reason: Input dispatching timed out (PointerEventDispatcher0
                        (server) is not responding. Waited 5174ms for MotionEvent)
```

Three things in the second ANR record settle the cause between them:

```
Frozen: false
Load: 10.46 / 6.21 / 5.33
CPU usage from 124ms to 16753ms later:
  20%  276/system_server:        8% user + 12% kernel / faults: 26793 minor  814 major
  16%  1648/org.lineageos.jelly: 4.7% user + 11% kernel / faults: 35560 minor 1018 major
  11%  492/com.android.systemui: 6.2% user +  5% kernel / faults: 16267 minor  260 major
  8.9% 121/surfaceflinger:       4.5% user + 4.3% kernel / faults: 17924 minor   42 major
54% TOTAL: 18% user + 21% kernel + 13% iowait + 1.1% softirq
```

- **`Frozen: false`** — Waydroid's `suspend_action = freeze` did *not* fire. The
  obvious theory, that minimising the window froze the container's cgroup and the
  input dispatcher's 5 s timer ran out while it was stopped, is wrong. Corroborated
  independently: `logcat` has entries in every single second across the whole
  window, so the container was scheduled throughout.
- **Thousands of major faults per process.** A major fault is a page fetched back
  from swap or from disk. system_server took 814 and jelly 1018 in 16.6 s.
- **54 % total CPU with 13 % iowait.** The machine was not busy, it was waiting.

The Android-side symptoms follow from that: `Long monitor contention with owner
binder:276_10 at WindowManagerService.addWindow`, `Davey! duration=9352ms`,
`Skipped 71 frames`. `PointerEventDispatcher0` was blocked on the WindowManager
lock held by a thread that was itself faulting pages back in.

## Root cause: the device was out of memory, and nothing was reclaiming

On the host, during the same window:

```
$ free -m
              total   used   free  shared  buff/cache  available
Mem:           3604   2942     13      40         649         589
Swap:           512    512      0
```

**13 MB free and zram 100 % full.** `/sys/block/zram0/mm_stat` shows 476 MB of
anonymous pages compressed into 134 MB — the compressor is doing its job, there
is simply no more room. `vm.swappiness` is 100, so the kernel keeps trying.

Two memory killers exist and **neither one fires**:

1. **Android's `lmkd`, inside the container, is inert.** It is running (pid 25)
   and `sys.lmk.minfree_levels` is populated, but it has logged exactly one line
   since the container started — `Connection with lmkd established` — and has
   never killed anything in over two hours at 9 MB free. It cannot: there is no
   `/dev/memcg` in the container, `/sys/fs/cgroup/memory/lxc.payload.waydroid` is
   mounted **read-only**, and this 3.18 kernel has no `/proc/pressure` (no PSI).
   With no pressure source to wake on, lmkd never evaluates its minfree levels.
2. **The host kernel's `lowmemorykiller` does not trigger either**, even though
   its thresholds look right:

   ```
   minfree: 18432,23040,27648,32256,55296,80640   # pages: 72 MB .. 315 MB
   adj:     0,100,200,250,900,950
   ```

   The 3.18 driver compares `free + file-backed - shmem` against `minfree`, and
   `buff/cache` was 649 MB — comfortably above the 315 MB adj-950 threshold. The
   page cache looked plentiful while anonymous memory thrashed, so the driver
   stayed quiet. This is the well-known blind spot of minfree-based LMK on a
   swap-backed system.

So the working set simply exceeded RAM and the system degraded by thrashing
rather than by killing anything. The resident set at the time was Sailfish
(`sailfish-browser -prestart`, `voicecall-ui`, `jolla-messages`, lipstick) plus
roughly twenty Android processes — jelly, WhatsApp, etar, documentsui, settings,
launcher3, systemui, the media providers — none of which Android was free to
reclaim.

Opening the terminal did not cause this; it was the last straw on an already
saturated device, and switching back forced a burst of window and surface work
(`addWindow`, `removeWindow`, a full redraw) at exactly the moment there were no
free pages to do it in.

## Status

**Not fixed.** Nothing here is a defect in the port's own code — the touch path,
the input bridge and the container were all working correctly. Options, roughly
in order of value for effort:

- **Give lmkd a pressure source.** The read-only memcg mount is the specific
  blocker. Making `/sys/fs/cgroup/memory/lxc.payload.waydroid` writable inside
  the container, or exporting a `/dev/memcg` with `memory.pressure_level`, would
  let lmkd do the job Android expects it to do — this is the change that most
  closely restores intended behaviour, and it belongs in the LXC config
  (`lxc.mount.entry`) rather than in Android.
- **Enable PSI in `karatep_defconfig`** (`CONFIG_PSI`). Not available on 3.18
  without a backport, so this is a real piece of work, but it is what modern lmkd
  actually wants and it would also give the host something to act on.
- **Grow zram.** 512 MB on a 3.6 GB device, at the ~3.8x ratio measured here, is
  buying about 1.9 GB. Doubling it costs ~130 MB of RAM for the compressed store.
- **Retune the host `lowmemorykiller` minfree** so it counts free pages more and
  page cache less. Crude, and it fights the kernel's own heuristics.

Until then the mitigation is the ordinary one: keep fewer Android apps open on a
3 GB device.

## How to tell it is this bug again

```sh
# host, as root
free -m                       # free near zero AND swap fully used
# container
waydroid shell -- logcat -d | grep -E "ANR in|Input dispatching timed out"
ls ~/.local/share/waydroid/data/anr/          # ANR traces, newest last
```

An ANR record with `Frozen: false`, high `iowait` and four-digit **major** fault
counts is this. An ANR with a `logcat` gap spanning the stall would be a freeze
bug instead, and no ANR at all with nothing ever written to
`/dev/input/wl_touch_events` is the input-bridge family of bugs above.
