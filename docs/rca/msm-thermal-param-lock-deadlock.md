# msm_thermal deadlocks the global module-parameter lock on some boots

**Status: fixed, twice over.** `07b2c9ff34ff` (merged as `ce665cd37eae`) makes the
deadlock impossible; `3a06ccbcfede` (merged as `b6da8267f4ed`) removes the TSENS
failure that armed it, so the trigger no longer occurs at all. Verified across eight
reboots between them.

## Symptom

On *some* boots, every write **and read** of any `/sys/module/*/parameters/*` on the
whole system blocks forever in `D` state. The tasks are unkillable. Only a reboot
clears it, and whether it happens is decided within the first ~15 s of boot.

Observed victims on one such boot:

| pid | task | blocked writing |
|---|---|---|
| 1213 | `droid-hal-init` | `/sys/module/g_android/parameters/mtp_rx_req_len` |
| 3703 | `init.qcom.post_boot.sh` | `/sys/module/lpm_levels/parameters/sleep_disabled` |
| 5028 | (FM test) | `/sys/module/radio_iris_transport/parameters/fmsmd_set` |

A plain `cat /sys/module/msm_thermal/parameters/enabled` hangs too, because
`param_attr_show()` takes the same lock as `param_attr_store()`.

## Root cause

`kernel/params.c` guards module parameters with **one global mutex** for the entire
kernel:

```c
static DEFINE_MUTEX(param_lock);        /* line 29 */
```

taken by `param_attr_store()` (line 566) and `param_attr_show()` (line 545) around
the parameter's `->set()` / `->get()` callback. So any `->set()` that blocks
indefinitely takes down module parameters **system-wide**, not just its own module's.

`msm_thermal` has such a `->set()`. Android's `thermal-engine` daemon writes `0` to
`/sys/module/msm_thermal/parameters/enabled` at startup — normal behaviour, it is
taking over thermal management from the kernel — which runs:

```
set_enabled()                     drivers/thermal/msm_thermal.c:5026
  interrupt_mode_init()                                      :5009
    hotplug_init()                                           :3878
      hotplug_init_cpu_offlined()                            :3808
      if (ret) kthread_stop(hotplug_task);                   :3889
```

`hotplug_init_cpu_offlined()` has an **unbalanced error path**:

```c
	if (therm_get_temp(cpus[cpu].sensor_id, cpus[cpu].id_type, &temp)) {
		pr_err("Unable to read TSENS sensor:%d.\n", cpus[cpu].sensor_id);
		mutex_unlock(&core_control_mutex);
		return -EINVAL;                     /* <-- no complete() */
	}
	...
	if (hotplug_task)
		complete(&hotplug_notify_complete); /* only on the success path */
```

and `hotplug_init()` calls `kthread_stop(hotplug_task)` **only** when that returns
non-zero — i.e. only on this failure path, the one that skipped the `complete()`.

The thread being stopped sleeps like this:

```c
	while (!kthread_should_stop()) {
		while (wait_for_completion_interruptible(
			&hotplug_notify_complete) != 0)
			;
```

`kthread_stop()` sets `KTHREAD_SHOULD_STOP` and calls `wake_up_process()`, but a
`wake_up_process()` does **not** satisfy a completion: `wait_for_completion_interruptible()`
re-checks `done` (still 0) and `signal_pending` (no signal), and goes straight back to
sleep. The loop condition `kthread_should_stop()` is never re-evaluated, so the thread
never exits and `kthread_stop()` never returns — while holding `param_lock`.

### Confirmed on device, both sides of the deadlock

```
tid=1844 comm=thermal-engine  state=D
  kthread_stop+0xe0/0x1a4
  set_enabled+0x364/0xf80
  param_attr_store+0x74/0xa8      <- past the mutex_lock: HOLDS param_lock

pid=1855 comm=msm_thermal:hot  state=S
  do_hotplug+0x9c/0x36c           <- asleep in wait_for_completion_interruptible
```

Every other victim sits at `param_attr_store+**0x38**` — the `mutex_lock()` itself —
which is how the holder is told apart from the waiters.

The trigger is visible in dmesg:

```
[   14.144876] msm_thermal:hotplug_init_cpu_offlined Unable to read TSENS sensor:-19.
```

Note the sensor id is `-19` (`-ENODEV`), i.e. the sensor was never resolved in the
first place, so `therm_get_temp()` cannot succeed.

## Why it is intermittent

The TSENS error appears on boots that deadlock *and* on boots that do not — it is
necessary but not sufficient. The remaining variable is a race with the freshly
created `do_hotplug` thread, which bails out immediately if core control is off:

```c
	if (!core_control_enabled) {
		pr_debug("Core control disabled\n");
		return -EINVAL;                 /* thread exits before ever waiting */
	}
```

`hotplug_init()` does `kthread_run(do_hotplug, …)` and then immediately calls
`hotplug_init_cpu_offlined()`. If the thread has already exited by the time
`kthread_stop()` runs, `kthread_stop()` returns normally and the boot is fine. If it
has reached `wait_for_completion_interruptible()`, the boot deadlocks. Both outcomes
were observed on this device across consecutive reboots of the same image.

## What it breaks

Anything that writes a module parameter after `thermal-engine` starts, which on this
port includes:

- **FM radio** — the plugin's `fmsmd_set` write hangs, so the tuner never turns on.
  This is what led to the discovery → [fm-radio-enablement.md](fm-radio-enablement.md).
- **`init.qcom.post_boot.sh` never finishes.** It dies on the `lpm_levels`
  `sleep_disabled` write, so every tuning step after that line — CPU governor
  settings, scheduler and power tunables — is silently never applied. This is a
  plausible lead for the untested/poor suspend and idle-drain behaviour.
- **MTP** — `mtp_rx_req_len` is never applied.
- **`droid-hal-init` is left unkillable in `D` state**, which is a strong candidate
  for the documented "a graceful `reboot` always hangs — `droid-hal-init.service`
  never stops" behaviour → [shutdown-hang.md](shutdown-hang.md). **Not proven**: the
  shutdown investigation points at the modem failing to halt, and these may be two
  separate faults. Worth re-testing a graceful reboot on a boot where this deadlock
  did *not* fire.

All of the above are resolved by the fix, confirmed on the patched kernel — see
Verification.

An earlier draft also blamed `systemctl is-system-running` staying at `starting`.
**That was wrong** — it still reports `starting` with the deadlock fixed. The unit
actually holding boot open is `init-done.service`, which is unrelated and pre-existing.

## The fix

Kernel patch `07b2c9ff34ff` on `Sailfish-on-karatep/android_kernel_lenovo_msm8937`
(no fork needed — the kernel repo is already ours and pinned in
`local_manifests/karatep.xml`), merged to `hybris-18.1` as `ce665cd37eae`.

Completing the completion on the error path is **not** sufficient on its own: the
thread could consume it, run the loop body, and be asleep again before
`kthread_stop()` sets the stop flag. The wake has to be ordered so the thread cannot
miss it, so a flag is set *before* the `complete()`:

```c
	if (hotplug_init_cpu_offlined()) {
		hotplug_task_stop = true;
		complete(&hotplug_notify_complete);
		kthread_stop(hotplug_task);
		hotplug_task = NULL;
	}
```

and `do_hotplug()` breaks on that flag immediately after its wait returns. Setting the
flag first means the thread cannot miss it whether it is still waiting, already in the
loop body, or exited early on `!core_control_enabled`.

`hotplug_task` is cleared afterwards because every other user of it is an
`if (hotplug_task)` guard whose work is pointless once the thread is stopped — and
none of the twenty use sites dereferences it, so `NULL` is safe.

A timeout-based wait (`wait_for_completion_interruptible_timeout`) was considered and
rejected: it would wake an RT thread on a fixed period forever to fix a one-shot
init-time race.

`do_freq_mitigation()` and `do_thermal_monitor()` wait the same way, but `hotplug_task`
is the **only** thread this driver ever passes to `kthread_stop()` — there is exactly
one `kthread_stop` call in the file — so they are not exposed to this. (An earlier
draft of this document said they should be fixed too; that was wrong.)

### The trigger, fixed separately

`sensor_id == -19` turned out to be a second, independent bug — an inherited
regression, and a textbook one. `fetch_cpu_mitigaiton_info()` allocates the per-CPU
sensor name correctly and then copies into it with the wrong bound:

```c
	cpus[_cpu].sensor_type = devm_kzalloc(&pdev->dev,
				strlen(sensor_name) + 1, GFP_KERNEL);
	...
	strscpy((char *)cpus[_cpu].sensor_type, sensor_name,
		sizeof(cpus[_cpu].sensor_type));
```

`sensor_type` is a `const char *`, so `sizeof()` is **8** on arm64 — the size of the
pointer, not of the buffer. Every per-CPU sensor name is truncated to seven characters
plus the NUL.

`git log -L` pins the provenance exactly. Commit `0498e1415d62`, *"msm: thermal: Fix
strlcpy usage in fetch_cpu_mitigaiton_info"*, replaced a correct `strlcpy(...,
strlen(sensor_name) + 1)` with the `sizeof()` form. The hardening sweep introduced the
bug it claimed to fix. (Same species as the TS3A227E `-Wmisleading-indentation`
regression already documented in this port.)

karatep's names come from `msm8937.dtsi` as `tsens_tz_sensor0..7`, so all eight CPUs
ended up asking for `tsens_t`, which matches no registered thermal zone. Straight from
the driver's own debugfs on the running device, before:

```
tsens sensor:tsens_tz_sensor5      <- a different field, stored in full
cpu0 sensor:tsens_t
cpu1 sensor:tsens_t
...
```

and after `3a06ccbcfede`:

```
cpu0 sensor:tsens_tz_sensor5
cpu1 sensor:tsens_tz_sensor6
cpu2 sensor:tsens_tz_sensor7
cpu3 sensor:tsens_tz_sensor8
cpu4..7 sensor:tsens_tz_sensor9
```

`Unable to read TSENS` is now absent from dmesg entirely (it appeared on every boot
before), and the CPU thermal zones report real temperatures for the first time —
48/48/48/47/49 °C — with no spurious core offlining, 8 of 8 CPUs online and
`cpus_offlined` 0. So CPU thermal mitigation had never had a working sensor to read on
this device.

Both fixes are worth keeping: this one removes the failure, the other makes it
survivable if any other `therm_get_temp()` call ever fails.

## Verification

Flashed `3.18.124-perf-g07b2c9ff34ff` and rebooted five times. Every boot:

| check | result |
|---|---|
| `Unable to read TSENS sensor:-19` in dmesg | present — **the trigger still fires** |
| `cat /sys/module/msm_thermal/parameters/enabled` | returns `N` immediately |
| tasks wedged in `param_attr_store` | 0 |
| `init.qcom.post_boot.sh` | runs to completion |
| `droid-hal-init` | `S`, not `D` |
| `msm_thermal:hotplug` thread | gone — stopped cleanly, only the freq and monitor threads remain |
| FM radio, as `defaultuser` | tunes 91.1 / 98.3 / 104.0 MHz, `EXIT=0` |

The trigger firing on all five is what makes this meaningful: the failure path is
still being taken, and it no longer deadlocks. Before the patch the same path left
`thermal-engine` in `D` and the hotplug thread parked in `S` forever.

Note the device runs the branch commit `-g07b2c9ff34ff` while `hybris-18.1` now
carries the merge `ce665cd37eae`. The source is identical; only the version string a
rebuild would stamp differs.

### Rebuild droid-hal, or this patch breaks WLAN

Flashing the patched `hybris-boot.img` on its own **took WLAN down**:
`wlan-module-load.service` failed, no `wlan` module, no `wlan0`. This is the hazard
already noted in CLAUDE.md — `wlan.ko` must match the running kernel exactly, and the
kernel release string is derived from `git describe`, so *any* kernel commit changes it
and orphans the old module under `/lib/modules/<old release>/`.

The full sequence for a kernel change on this port is therefore:

```sh
/opencloud/bin/habuild /parentroot/parentroot/opencloud/bin/build-hal.sh   # kernel + boot image
rpm/dhd/helpers/build_packages.sh -d                                      # droid-hal, rebuilds wlan.ko
# flash hybris-boot.img AND install the new droid-hal-karatep* packages
```

After doing both, `wlan.ko` lands under `/lib/modules/3.18.124-perf-g07b2c9ff34ff/`
and WLAN comes back: module loaded, service active, `wlan0` present. Bluetooth was
unaffected throughout (`bluebinder` bridges through vhci, not a kernel module).

## Not the cause

For the record, since the FM investigation initially blamed these:

- **The `APPS_FM` SMD channel.** It stays `CLOSED` on a deadlocked boot only because
  nothing ever reaches the transport to ask it to open — the write never gets past
  `param_lock`. `smd_named_open_on_edge()` cannot block indefinitely anyway; its worst
  case is one `msleep(250)`.
- **WCNSS, Bluetooth, or Waydroid state.** All ruled out by direct observation.
