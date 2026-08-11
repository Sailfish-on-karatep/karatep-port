# FM radio: the iris event queue wedges, and the Media app hangs with it

**Status: fixed in the kernel, verified on device.** `1a66993ac33a` (merged as
`b5cc23ebd72a`) gives the event queue an eviction policy and flushes it on open. This
is a *separate* bug from [fm-radio-enablement.md](fm-radio-enablement.md), which was
about permission to open the transport; that fix was correct and still stands.

## Symptom

With a headset connected (it doubles as the antenna), the FM page in the Media app:

1. will not let you tune — the frequency control is inert, as if no tuner were present;
2. hangs when you leave the page, and the app has to be force-quit.

Both are permanent for the rest of the boot. Rebooting restores FM until it happens
again.

The same shape was reported on `#sailfishos-porters` and never answered:

> `2018-08-19  <masya_>` I was able to start FM radio with `echo 1 >
> /sys/module/radio_iris_transport/parameters/fmsmd_set`. However, **it only works
> once before the reboot.** How I can fix it.

## What is *not* wrong

Worth stating, because the driver looks broken from the app and is not:

- **The tuner works.** Driving `/dev/radio0` directly as `defaultuser` — the app's own
  identity — powers FM on, tunes 100.10 MHz and reads the station back.
- **Permissions are correct.** `/dev/radio0` is `audio:audio 0660`, `fmsmd_set` is
  `system:audio 0660`, `defaultuser` is in `audio`.
- **There is no race against the SMD open.** The plugin waits 20 ms after writing
  `fmsmd_set` and another 10 ms after `open()`. Sweeping that gap, 5 ms already
  succeeds and only 0 ms fails, so its margin is ample.

## Root cause

`radio-iris` publishes asynchronous events (tune complete, RDS arrived, radio
enabled/disabled) through a kfifo that userspace drains with `VIDIOC_DQBUF`:

```c
static void iris_q_event(struct iris_device *radio, enum iris_evt_t event)
{
	...
	data_b = &radio->data_buf[IRIS_BUF_EVENTS];
	if (kfifo_in_locked(data_b, &evt, 1, &radio->buf_lock[IRIS_BUF_EVENTS]))
		wake_up_interruptible(&radio->event_queue);
}
```

The queue holds `STD_BUF_SIZE` = **256** events, and **nothing ever empties it**:

- `iris_fops` had no `->open`, so a new session inherits whatever the last one left;
- the driver's only `kfifo_reset()`, in `hci_ev_tune_status()`, is guarded by
  `if (i >= IRIS_BUF_RT_RDS)` and so resets the RDS buffers **and skips the event
  queue by construction** (`IRIS_BUF_EVENTS` is 1, `IRIS_BUF_RT_RDS` is 2);
- the driver is built in, so the state never goes away with a module unload.

Any client that generates events without draining them — a session killed mid-flight,
a self-test, an RDS burst that outruns the reader — leaves its events behind forever.
Once 256 have accumulated, `kfifo_in_locked()` returns 0: the event is dropped
**and `wake_up_interruptible()` is skipped**. From then on the queue is not merely
full, it is inert.

### Why that produces exactly these two symptoms

`qt5-qtmultimedia-plugin-mediaservice-irisradio` runs a worker thread that blocks in
`VIDIOC_DQBUF` and gates the whole UI on one event:

```cpp
case IRIS_EVT_RADIO_READY:
    emit tunerAvailableChanged(true);
```

Every user-facing entry point begins `if (!m_tunerAvailable) return;`. A dropped
`IRIS_EVT_RADIO_READY` is therefore indistinguishable from having no tuner —
**symptom 1**.

Teardown is worse. `stop()` runs on the GUI thread:

```cpp
m_workerThread->setQuit();
SetCtrl(V4L2_CID_PRIVATE_IRIS_STATE, 0); // assuming this wakes blocking ioctl()
m_workerThread->wait();
```

That comment is the whole problem. What wakes the blocking ioctl is the
`IRIS_EVT_RADIO_DISABLED` that powering off generates. Drop it and `DQBUF` stays
parked in `wait_event_interruptible()`, the worker never re-tests `m_quit`, and
`QThread::wait()` blocks the **GUI thread** — **symptom 2**.

So one dropped byte in the kernel takes out both tuning and the app.

## Proof

Reproduced and then re-verified from the host over the debug shell, driving
`/dev/radio0` as `defaultuser` in exactly the sequence the plugin uses.

On the **unpatched** kernel:

| stage | result |
|---|---|
| power on with an empty queue | `IRIS_EVT_RADIO_READY` delivered |
| generate ~400 events with no reader | queue holds exactly **256**; the surplus is gone |
| power on with a full queue | `RADIO_READY` never queued; `DQBUF` blocks indefinitely |

On the **patched** kernel (`3.18.124-perf-g1a66993ac33a`):

| stage | result |
|---|---|
| 600 tunes with no reader | queue capped at **261** in flight, never grows |
| eviction | **155** × `event queue full, dropped stale event` — oldest discarded |
| draining after overflow | completes and empties; previously blocked forever |
| session after a heavy flood | `RADIO_READY` delivered, tuned 100.10 MHz, teardown wakes `DQBUF` |

Worth knowing for anyone reproducing this: a tune yields well under one event on
average when there is no signal (600 tunes produced ~400 events), so filling 256 takes
sustained use. In practice the queue is poisoned by *accumulated* sessions — including
aborted ones — rather than by any single one, which is why the failure looks like FM
"just stopping working" rather than something a particular action triggers.

Verification is at the V4L2 layer, replicating the plugin's exact call sequence
including its blocking `VIDIOC_DQBUF` worker; the GUI itself was not driven.

One trap when writing such a test: `VIDIOC_DQBUF` blocks inside
`wait_event_interruptible()` **regardless of `O_NONBLOCK`**, so a userspace timeout
around it does nothing. Fence it in a child process you can kill — otherwise the test
hangs in precisely the way the bug does, and looks like a failure of whatever ran
before it.

## Fix

`1a66993ac33a`, two changes to `drivers/media/radio/radio-iris.c`:

**Evict the oldest event instead of discarding the newest.** The events a client acts
on are always the most recent ones, so when the queue is full `iris_q_event()` now
makes room rather than dropping what it was asked to deliver. This also keeps the
queue self-healing if a reader ever falls behind an RDS burst, and guarantees
`wake_up_interruptible()` still runs.

**Flush the event queue on open.** A new `iris_fops_open()` resets
`data_buf[IRIS_BUF_EVENTS]` under `buf_lock`, so a session can neither inherit a
backlog nor act on a previous session's stale state.

Either change alone would clear the reported symptoms; both are kept because they
address different halves — one bounds a session's own behaviour, the other isolates
sessions from each other.

## Prior art, and what was deliberately not taken

Searching the porters archive for `kfifo_reset`, `IRIS_BUF_EVENTS` and `RADIO_READY`
returns **nothing** across eleven years. The nearest thing is a 2017 discussion on
[irisradio PR #3](https://github.com/mer-hybris/qt5-qtmultimedia-plugin-mediaservice-irisradio/pull/3),
where jusa observed that "at least on one device the kernel implementation was quite
bad ... there was a quite good chance to leak kernel resources" and posted
[a patch](https://github.com/ferrari-dev/msm-3.10/commit/291ea7e5ead02c23cf706625f75fdd70bae86f2e)
that stops `iris_fops_release()` from tearing the SMD channel down and refuses
`radio_hci_unregister_dev()` with `-EBUSY` while FM is running.

That patch is real and our tree does have the leak it describes, but it is **a
different bug** — it never touches the event queue — and it predates PR #7, which
made the plugin manage `fmsmd_set` itself around each session. Adopting it would
change the transport's lifetime model to suit a client we no longer run. Not taken;
recorded here so the next person does not have to re-derive it.

## Checking for it

The signature is that direct V4L2 access still tunes fine while the app does not.
On the fixed kernel a wedged queue is now visible rather than silent:

```
FMDERR: event queue full, dropped stale event <n>
```
