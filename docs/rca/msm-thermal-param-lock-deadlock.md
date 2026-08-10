# msm_thermal deadlocks the global module-parameter lock on some boots

**Status: root-caused, not yet fixed. Needs a kernel patch.**

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
- `systemctl is-system-running` stays `starting` indefinitely.

## Proposed fix

A kernel patch, via the usual fork-and-repin route on
`Sailfish-on-karatep/android_kernel_lenovo_msm8937`. Completing the completion on the
error path is **not** sufficient on its own — the thread could consume it, loop, and
be asleep again before `kthread_stop()` sets the stop flag. The wait itself has to
become stop-aware, e.g.:

```c
	while (!kthread_should_stop()) {
		while (wait_for_completion_interruptible_timeout(
			&hotplug_notify_complete, HZ) <= 0) {
			if (kthread_should_stop())
				return 0;
		}
		reinit_completion(&hotplug_notify_complete);
		if (kthread_should_stop())
			return 0;
```

`do_freq_mitigation()` and `do_thermal_monitor()` in the same driver use the identical
`wait_for_completion_interruptible()` pattern and should be checked for the same
hazard while we are in there.

Fixing the TSENS sensor id resolution (`sensor_id == -19`) would remove the trigger,
but not the bug: any other failure of `therm_get_temp()` re-arms it.

## Not the cause

For the record, since the FM investigation initially blamed these:

- **The `APPS_FM` SMD channel.** It stays `CLOSED` on a deadlocked boot only because
  nothing ever reaches the transport to ask it to open — the write never gets past
  `param_lock`. `smd_named_open_on_edge()` cannot block indefinitely anyway; its worst
  case is one `msleep(250)`.
- **WCNSS, Bluetooth, or Waydroid state.** All ruled out by direct observation.
