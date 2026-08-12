# Investigation: in-call volume cannot be set — the ADSP rejects the CVP voice device config

Device: **lenovo/karatep** — Lenovo Vibe K6 Note / Plus, **MSM8937 / Snapdragon 430**, Adreno 505.
Base: LineageOS 18.1 (Android 11) / `hybris-18.1`, aarch64, Sailfish OS 5.1.0.11.

Status: **open — not root-caused.** The failure is measured and reproducible; the cause is not
yet established.

Diagnosed against a **live BSNL SIM** (MCC 404 / MNC 80) on 2026-08-13, on the first CS voice
call this port has ever completed.

---

## Symptom

During an **active** CS voice call, ofono reports the call volume interface as dead:

```sh
dbus-send --system --print-reply --dest=org.ofono /ril_0 \
    org.ofono.CallVolume.GetProperties
#   SpeakerVolume     byte 0
#   MicrophoneVolume  byte 0
#   Muted             boolean true
```

Polled twice, one minute apart, on a call that was audibly connected. `Muted: true` on a live
call is wrong on its face, and both volumes read 0.

The obvious reading — "the binder RIL does not implement the CallVolume atom, so ofono is
reporting defaults" — is **wrong**. The commands are being issued and the DSP is refusing them.

## Evidence

At call setup, `dmesg` (monotonic timestamps; this boot began ≈ 01:23:45 IST, so `[1857.87]`
is 01:54:42.9 — inside the `dialing → active` window, which was 01:54:42 → 01:54:46):

```
[1857.872131] voice_send_cvp_register_dev_cfg_cmd:   DSP returned error[ADSP_EBADPARAM]
[1857.876492] voice_send_vol_step_cmd:               DSP returned error[ADSP_EFAILED]
[1857.887197] voice_send_vol_step_cmd:               DSP returned error[ADSP_EFAILED]
[1861.673308] voice_send_cvp_deregister_vol_cal_cmd: DSP returned error[ADSP_EALREADY]
[1861.674115] voice_send_cvp_deregister_cal_cmd:     DSP returned error[ADSP_EALREADY]
[1861.674914] voice_send_cvp_deregister_dev_cfg_cmd: DSP returned error[ADSP_EALREADY]
[1864.284357] voice_send_vol_step_cmd:               DSP returned error[ADSP_EALREADY]
```

Read in order:

1. **`voice_send_cvp_register_dev_cfg_cmd` → `ADSP_EBADPARAM`.** The Core Voice Processor
   device configuration is rejected. This is the command that tells the ADSP which physical
   device the voice path runs on and with what topology. It never succeeds.
2. **`voice_send_vol_step_cmd` → `ADSP_EFAILED`, twice.** Volume steps fail, which is the
   direct and sufficient explanation for `SpeakerVolume 0 / MicrophoneVolume 0 / Muted true`.
3. **The `deregister` commands → `ADSP_EALREADY`.** Secondary, not independent faults: teardown
   is unwinding a registration that never happened, so the DSP reports it is already in that
   state. They are a *consequence* of (1), not separate bugs — do not chase them.

Also present through the call, and probably unrelated:

```
q6asm_callback: payload size of 8 is less than expected.
```

## What is and is not affected

| | |
|---|---|
| Routing | **works.** PulseAudio switched the droid card to the `voicecall` profile, took `output-earpiece` on the sink and `input-voice_call` on the source, and accepted a mid-call port change to `output-speaker` with no `ENOSYS` and no error in the journal |
| Call signalling | **works.** `dialing(2) → alerting(3) → active(0)`, clean hangup |
| Audibility | **appeared fine but is not verified.** No measurement was taken; this needs a controlled test before anything is claimed |
| In-call volume | **broken.** The DSP refuses every volume step |
| Calibration | **broken.** The voice device config is rejected outright |

Note the ordering trap: because routing is applied by PulseAudio and *appears* correct, and
because audio seems to be audible, this fault is easy to miss entirely. It is only visible in
`dmesg`, and only during a call.

## Prior art — this is not a karatep bug

The `#sailfishos-porters` archive has it, and the finding materially changes the shape of the
problem. `ADSP_EFAILED` on voice volume is a **known, unresolved issue affecting 64-bit ports
generally**, seen on both Sailfish OS and Ubuntu Touch:

```
2021-08-27  <fredldotme>  (btw, is there anything new regarding voice call volume on 64bit
                           devices? I noticed SfOS has the same ADSP_EFAILED issue as sargo
                           on UT)
2021-08-27  <fredldotme>  A mess, I played around with it during ADSP/voice call volume debugging
2022-01-12  <fredldotme>  @eriki73 you haven't been able to debug voice call volume issues on
                           SfOS on sargo, right?
```

Still open five months later, on a different device (`sargo`, Pixel 3a), on a different
distribution. This port is `aarch64` with a full 64-bit userspace, which fits exactly.

Searches for `voice_send_cvp_register_dev_cfg_cmd`, `voice_send_vol_step_cmd`, `ADSP_EBADPARAM`,
`cvp_register` and `CallVolume` all return **zero** hits, so the `EBADPARAM` half — the rejected
device config — appears to be undiscussed anywhere. Only the `EFAILED` volume half has prior art.

Older hits (2015, mako; 2016) describe silent in-call volume but predate 64-bit ports and are
almost certainly a different fault. Do not conflate them.

## Leading hypotheses, untested

1. **A 32/64-bit ABI mismatch in the voice calibration path.** This is now the front-runner,
   because it is the one hypothesis that explains why the failure tracks *word size* across
   unrelated devices and distributions rather than tracking the vendor blob set. If the
   calibration structures the HAL hands the ADSP are packed differently when built 64-bit, the
   DSP would reject the device config as `EBADPARAM` and then refuse volume steps — exactly
   what is observed.
2. **Missing or mismatched ACDB voice calibration for karatep.** `ADSP_EBADPARAM` on a device
   config registration is also the classic signature of the ACDB handing the DSP a
   device/topology pair it does not recognise. Weakened by the cross-device evidence above, but
   cheap to check and not excluded.

Both are checkable without a second party — a call to any IVR is enough to reproduce.

Given the cross-port history, treat a karatep-local fix as unlikely and coordinate with the
porter community before sinking time into it.

## How to reproduce

1. Place any outgoing call and let it reach `active`.
2. `dmesg | grep -E "voice_send|ADSP_"` — the `EBADPARAM`/`EFAILED` pair appears within a
   second of the call going active.
3. `dbus-send --system --print-reply --dest=org.ofono /ril_0 org.ofono.CallVolume.GetProperties`
   during the call — `Muted: true`, both volumes 0.

Note that the journal on this device currently rotates in roughly half an hour while the VoLTE
investigation's ofono debug logging is enabled, so capture `dmesg` promptly.
