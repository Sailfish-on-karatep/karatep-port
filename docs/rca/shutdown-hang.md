# Shutdown sometimes wedges instead of powering off

**Status: in progress — evidence gathered, root cause not yet proven.**

## Symptom

Shutdown works sometimes. Often the device instead ends up with the screen off but
still powered, unresponsive to the power key, and only a hard reset recovers it.

## What is established

### Clean shutdowns really are clean

A ramoops console log from a boot that shut down normally opens with:

```
qcom,qpnp-power-on qpnp-power-on-4:  PMIC@SID0: Power-off reason:
    Triggered from PS_HOLD (PS_HOLD/MSM controlled shutdown)
```

PS_HOLD is the normal MSM controlled power-off, so `pm_power_off` is wired and the
kernel side works. This is **not** a missing power-off handler; it is an
intermittent stall higher up.

### A failed shutdown, from ramoops

`/sys/fs/pstore/console-ramoops` survives the hang and the hard reset. The buffer
started at `[0.000000]`, so it had not wrapped and holds the whole session. It ends
with no shutdown completion at all:

| time | event |
|---|---|
| 4231.76 | `subsys-restart: wait_for_shutdown_ack(): [modem]: Timed out waiting for shutdown ack` |
| 4231.76 | `sysmon-qmi: shutdown_ack SMP2P bit for modem not set` / `QMI shutdown indication not received` |
| 4231.79 | `pil-q6v5-mss 4080000.qcom,mss: Port 0000000000000000 halt timeout` (x2) |
| 4235.49 | camera stack torn down — `msm_flash_release`, `msm_csiphy_release`, `msm_csid_release`, `msm_cci_release`, `MSM-CPP ... shutdown cpp node` |
| 4238–4247 | ~10 power key press/release pairs, no effect |
| → end | only `ESD lcd reg` panel polling every 2 s, then nothing |

So a shutdown **starts** (camera teardown proves userspace got well into it), the
modem fails to halt, and then the sequence stalls. The kernel never reaches
`Power down` / PS_HOLD.

`Port ... halt timeout` means the modem's AXI ports never quiesced — the modem was
genuinely unresponsive, not just slow.

## Why there is no userspace evidence yet

`console=tty60` in `BOARD_KERNEL_CMDLINE` means only kernel printk reaches ramoops.
systemd and dsme log to the journal, and `/etc/systemd/journald.conf` has:

```
Storage=volatile
RuntimeMaxUse=1M
```

A 1 MB volatile journal is destroyed within minutes and never survives a reboot, so
the shutdown records are simply gone.

## Instrumentation applied (device-only, not in droid-config)

Routes systemd's own logs into kmsg, which ramoops **does** capture, so the late
shutdown phase survives a hang:

* `/etc/systemd/journald.conf.d/99-karatep-shutdown-debug.conf` —
  `RuntimeMaxUse=32M`, `ForwardToKMsg=yes`, `MaxLevelKMsg=info`
* `/etc/systemd/system.conf.d/99-karatep-shutdown-debug.conf` —
  `LogTarget=kmsg`, `LogLevel=debug`

`systemd-analyze` is not installed on the device; apply to a running PID 1 with
systemd's runtime signals instead (glibc `SIGRTMIN` is 34):

```sh
kill -62 1   # SIGRTMIN+28: set log target to kmsg
kill -56 1   # SIGRTMIN+22: set log level to debug
```

Confirmed working — `systemd[1]: Setting log target to kmsg.` appears in `dmesg`.

**These drop-ins live in the rootfs and are wiped by a reflash.** Reapply them
before resuming this investigation.

## Leads examined and discarded

* **Load average pinned at 3.0 on an idle device.** Not a stuck task.
  `/proc/loadavg` reports `1/1177` runnable with the CPU 96 % idle; the three
  contributors are kernel threads idling in uninterruptible sleep inside their own
  main loops — `mdss_dsi_event` in `dsi_event_thread`, `mmc-cmdqd/0` in
  `mmc_cmdq_thread`, `kworker/u16:0` in `msm_mpm_work_fn`. Normal for this kernel.
* **A single hung systemd service.** `DefaultTimeoutStopUSec=15s`, so systemd
  SIGKILLs a stuck unit. This cannot stall shutdown indefinitely by itself, which
  points at the late phase (`systemd-shutdown`, unmounts) or at the Android service
  tree, which systemd sees as one unit.

## Leading hypothesis: the modem

Not proven. Weigh both sides:

**For** — the modem failure is the last meaningful kernel activity before the
wedge, and the RIL stack is known-broken here (mobile data broken, SIM 2 "Network:
Denied"). Five vendor daemons run under `droid-hal-init` (`rild`, `rild -c 2`,
`netmgrd`, `cnd`, `qseecomd`); a stall inside that tree is invisible to systemd's
per-unit timeout.

**Against** — `wait_for_shutdown_ack` and `halt timeout` are timeouts that *expire*.
The kernel logs them and proceeds, so on their own they do not block forever.

## Prior art

None. The `#sailfishos-porters` archive returns **zero hits** for
`wait_for_shutdown_ack`, for `Port 0000000000000000 halt timeout`, and for the
general shutdown-hang phrasings. No other porter has discussed this.

## Next steps

1. **Run A (baseline).** Shut down normally. On a hang, hard reset, boot, and read
   `/sys/fs/pstore/console-ramoops` — it will now contain the userspace trace.
2. **Run B (modem excluded).** Stop `ofono` and the RIL side, wait, then shut down.
   Consistently clean B against hanging A implicates the modem, and the fix is
   shutdown ordering for those services.

The fault is intermittent, so a single clean shutdown proves nothing; repeat each
arm several times.

Optional, only *after* a good trace is captured: arm systemd's shutdown watchdog so
a wedged shutdown reboots itself instead of needing a hard reset. It is a mitigation
and it masks the symptom, so it must not be enabled while still diagnosing.

## New lead (Aug 2026): droid-hal-init can be stuck in `D` before shutdown even starts

Found while investigating FM radio, and untested against this bug so far.

On some boots `msm_thermal` deadlocks the kernel's single global module-parameter
mutex, after which every `/sys/module/*/parameters/*` access blocks forever in
uninterruptible sleep. `droid-hal-init` is one of the tasks that hits it — it writes
`/sys/module/g_android/parameters/mtp_rx_req_len` — and was observed sitting
unkillable in `D` state for the rest of the boot.

That is a direct candidate explanation for "a graceful `reboot` always hangs —
`droid-hal-init.service` never stops": systemd cannot stop a service whose main
process is wedged in `D`, and no signal will move it.

It does not obviously explain the *modem* halt timeouts above, so these may well be
two separate faults, and this one is intermittent while the reboot hang is described
as always happening. Worth deciding by testing a graceful `reboot` on a boot where the
deadlock did **not** fire — check with `cat /sys/module/msm_thermal/parameters/enabled`,
which hangs on an affected boot and returns `N` immediately on a healthy one.

→ [msm-thermal-param-lock-deadlock.md](msm-thermal-param-lock-deadlock.md)

## Other findings noted along the way

Three units are failed at runtime (`systemctl --failed`); relevance unknown:

```
dev-binderfs.mount              Droid mount for /dev/binder
droid-bootctl.service           Droid bootctl
systemd-tmpfiles-setup.service  Create Volatile Files and Directories
```

`systemctl` on this device pipes into `less`, which eats a heredoc driving the
telnet debug shell. Always pass `--no-pager` (or export `SYSTEMD_PAGER=cat`).
