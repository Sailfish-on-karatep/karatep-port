# VoLTE never registers — ofono asks the modem the wrong question, then misreads the refusal

**Status: root-caused on hardware, not yet fixed. The remaining fix is new code, not configuration.**

ofono reports `org.ofono.IpMultimediaSystem` `Registered: true` on karatep. It is not
registered. Every call — incoming and outgoing — falls back to CS and the modem drops to 2G.

Two independent defects stack up here, and they have to be separated before either can be
fixed:

1. the vendor RIL implements `requestRegistrationChange` as *IMS test mode*, not as "enable
   VoLTE", and refuses it; and
2. `ofono-binder-plugin-ext-qti` ignores the refusal and parses the zeroed payload behind it,
   which happens to decode as `REGISTERED`.

## Symptoms

- `IpMultimediaSystem.Registered = true`, `VoiceCapable = true`, `SmsCapable = true`.
- Calls take the IMS dial path (`ims:Dialing (ext)`) and the HAL accepts the dial, but the
  call CSFBs to 2G every time.
- qcril reports `IMS registered for VOIP or VT service 0` with `valid 0` on every field.

## What is actually on the wire

Captured with `ofonod -d 'qti_binder_trace,qti_binder_dump,...'`, which hexdumps the parcels:

```
imsradio0< [00000001]  4 getImsRegistrationState
imsradio0< [00000002]  7 requestRegistrationChange
  0030: 30 3a 3a 49 49 6d 73 52  61 64 69 6f 00 00 00 00
  0040: 02 00 00 00 00 00 00 00       <- token 2, regState 0 (REGISTERED)

imsradio0> [00000001] 11 getImsRegistrationState
  0030: 6f 6e 73 65 00 00 00 00  01 00 00 00 02 00 00 00
                                 ^token 1  ^errorCode 2
imsradio0> [00000002]  4 requestRegistrationChange
  0030: 6f 6e 73 65 00 00 00 00  02 00 00 00 02 00 00 00
                                 ^token 2  ^errorCode 2
```

`errorCode 2` is `android.hardware.radio@1.0::RadioError::GENERIC_FAILURE`. **Both IMS calls
fail.** The reply to `getImsRegistrationState` carries a 48-byte `RegistrationInfo` buffer
(visible as three `BINDER_TYPE_PTR` objects — the struct plus its two empty strings) whose
contents are entirely zero.

### Why the failure reads as success

`ofono-binder-plugin-ext-qti` `src/qti_ims.c`:

```c
qti_ims_reg_status_response(QtiRadioExt* radio_ext, int result,
                            GBinderReader* reader, void* user_data)
{
    ...
    const QtiRadioRegInfo* info = qti_radio_ext_read_ims_reg_status_info(radio_ext, &reader_copy);
    if (!info) { ... return; }
    state = info->state;          /* result is never examined */
```

`result` — the `errorCode 2` above — is accepted as a parameter and never looked at. The
zeroed struct parses cleanly (the C layout is correct; see below), giving `state == 0`, and
`QTI_RADIO_REG_STATE_REGISTERED == 0`. So a hard failure is reported to ofono as a successful
registration.

`iface->flags` is likewise a compile-time constant
(`BINDER_EXT_IMS_INTERFACE_FLAG_VOICE_SUPPORT | ..._SMS_SUPPORT`), so `VoiceCapable` and
`SmsCapable` say nothing about the hardware either. **All three D-Bus properties are
artefacts.**

The struct layout is *not* the bug, and must not be "fixed":

| `QtiRadioRegInfo` field | offset | reply buffer |
|---|---|---|
| `state` | 0 | — |
| `error_code` | 4 | — |
| `error_message` | 8 | child buffer, length 1 (empty string) |
| `radio_tech` | 24 | — |
| `uri` | 32 | child buffer, length 1 (empty string) |
| total | 48 | main buffer length `0x30` = 48 ✓ |

The parcel's own buffer sizes and child `parent_offset`s (8 and 0x20) match the C struct
exactly. Ports where IMS does work report non-zero values through this same code — `state:2
radiotech:16` (mal, fp6) and `state:1 radiotech:15` (Mister_Magister) on the porters channel.
Ours reports `state:0 radiotech:0 error_code:0 uri: error_msg:` — every field zero or empty,
which is an unpopulated struct, not an answer.

## Why the modem refuses

The vendor side of the same transaction, from `logcat -b radio`:

```
qcril_process_event: RIL <--- QCRIL_EVT_IMS_SOCKET_REQ_IMS_REG_STATE_CHANGE(851994) --- AMSS
qcril_qmi_imss_request_set_ims_registration: has_state: 1, state: 1
qcril_qmi_imss_request_set_ims_registration: Need to change voice domain pref? No
qcril_qmi_imss_set_ims_test_mode_enabled: ims_test_mode_enabled = FALSE
qcril_qmi_imss_set_ims_test_mode_enabled: .. qmi send async res 1
qcril_qmi_ims_map_ril_error_to_ims_error: ril error 2 mapped to ims error 2
sendMessage: msg: IMS_REG_STATE_CHANGE RESP(type: 2, id: 26), error: 2
```

**`requestRegistrationChange` is wired to QMI IMSS "set IMS test mode".** It is not the call
that enables VoLTE; it is a lab hook, and this production modem rejects it. ofono asks the one
question the modem will not answer, and never asks the one it would.

Consistent with that, the modem's IMS Application service never reports anything at all:

```
qcril_qmi_imsa_is_ims_registered_for_voip_vt_service: IMS registered valid 0, Status 0
qcril_qmi_imsa_is_ims_registered_for_voip_vt_service: IMS service status valid 0
qcril_qmi_imsa_is_ims_registered_for_voip_vt_service: IMS registered for VOIP or VT service 0
```

`valid 0` means *no indication has ever arrived*, which is different from a negative answer.
The modem is not refusing to register; nothing has asked it to.

## What Android does instead

`vendor.qti.hardware.radio.ims@1.0::IImsRadio` has forty methods.
`ofono-binder-plugin-ext-qti` implements eight. Two of the ones it does not implement are the
ones Android uses to turn VoLTE on.

Transaction codes recovered from the device's own
`/system/system_ext/priv-app/ims/ims.apk` with
[`scripts/hidl-from-apk.py`](../../scripts/hidl-from-apk.py), which decodes the
`IImsRadio$Proxy` bytecode — HIDL numbers methods by declaration order, which the DEX class
layout does not preserve, but every generated proxy body ends in
`mRemote.transact(<code>, ...)`. The extraction reproduces all eight codes the plugin already
hardcodes — `setCallback=1`, `dial=2`, `getImsRegistrationState=4`, `answer=5`, `hangup=6`,
`requestRegistrationChange=7`, `setSuppServiceNotification=31`, `cancelModifyCall=40` — which
is what makes the rest trustworthy:

| code | method | in ext-qti? |
|---|---|---|
| 7 | `requestRegistrationChange` | yes — and it is the wrong call |
| 8 | `queryServiceStatus` | no |
| **9** | **`setServiceStatus`** | **no** |
| **12** | **`setConfig`** | **no** |
| 13 | `getConfig` | no |

On Android, `ims.apk` calls `setServiceStatus` (→ QMI IMSS *Set IMS Service Enable Config*) to
enable VoIP/VT per radio technology, and `setConfig` with
`CONFIG_ITEM_VOLTE_USER_OPT_IN_STATUS = 33` for provisioning. Both enum values were extracted
from the same APK, so they are this device's values, not a guess:

```
ServiceType:     SMS=0  VOIP=1  VT=2  INVALID=3
StatusType:      STATUS_DISABLED=0  STATUS_PARTIALLY_ENABLED=1  STATUS_ENABLED=2
                 STATUS_NOT_SUPPORTED=3  STATUS_INVALID=4
RadioTechType:   RADIO_TECH_LTE=15  RADIO_TECH_IWLAN=20  RADIO_TECH_ANY=0
ConfigItem:      CONFIG_ITEM_VOLTE_USER_OPT_IN_STATUS=33
```

`setServiceStatus(int32_t token, ServiceStatusInfo)` — signature confirmed from the APK's
method prototypes. `ServiceStatusInfo`'s nine fields match the HIDL declaration already
quoted in ext-qti's `qti_radio_ext_types.h`, so the layout is known. (Note the C struct there
declares `hasIsValid`/`isValid` as 4-byte `gboolean` where HIDL specifies 1-byte `bool`; that
will need fixing before the struct is written rather than only read.)

## Prior art: none

The `#sailfishos-porters` archive was searched for `setConfig`, `ConfigItem`,
`VOLTE_USER_OPT_IN`, `opt_in`, `qcril_qmi_imss` and `IMS_REG_STATE_CHANGE`. **All six return
zero hits** across eleven years of logs. The channel's entire VoLTE effort — rinigus, mal and
Mister_Magister through 2025 and 2026 — goes through `requestRegistrationChange`, and the
recurring outcome is mal's "I haven't had luck getting ims registered yet"
(2025-07-26). The one success on the channel, Mister_Magister's
"AND WE GOT VOLTE LADIES AND GENTLEMEN" (2025-09-01), came from flashing a carrier MBN onto
the OnePlus 6T's EFS partition so the *modem* enabled IMS by itself — the ofono side was never
what turned it on. So the QMI IMSS config path is unexplored territory here, and there is no
prior art to follow.

## What would fix it

Implement `setServiceStatus` (code 9) in a fork of `ofono-binder-plugin-ext-qti` and call it
when ofono asks for IMS registration, instead of — or before — `requestRegistrationChange`:
`type = VOIP`, `status = STATUS_ENABLED`, `accTechStatus` naming `RADIO_TECH_LTE`. If the
modem also needs provisioning, add `setConfig` with item 33. Both are new code against a HAL
nobody has driven this way from Sailfish, so it needs to be tried on hardware before it can be
claimed.

Independently, and regardless of whether that works, ext-qti should stop reporting a failed
request as a successful registration: `qti_ims_reg_status_response()` must check `result`
before it touches the payload. That one is a clear upstream bug with a two-line fix, and it is
what makes this failure so hard to see — the port looks like it has working VoLTE.

## Not the causes

- **Not the RegState enum.** `REGISTERED=0, NOT_REGISTERED=1, REGISTERING=2, INVALID=3` was
  extracted from `ims.apk` and matches ext-qti exactly. rinigus's `REGISTERED=1` values are
  AIDL-only, as his own source comment says. Do not fork ext-qti for this.
- **Not a HAL version mismatch.** `/vendor/etc/vintf/manifest.xml` declares
  `vendor.qti.hardware.radio.ims@1.0::IImsRadio` with `imsradio0`/`imsradio1`, and the plugin
  connects to `@1.0` after correctly failing over from `@1.2` and `@1.1`.
- **Not the IMS daemons.** `imsqmidaemon`, `imsdatadaemon`, `imsrcsservice` and
  `ims_rtp_daemon` all run from boot and never restart; `vendor.ims.QMI_DAEMON_STATUS = 1`.
- **Not the LTE attach or the IMS PDN.** The device is registered on LTE with data working.
- **Not `persist.dbg.volte_avail_ovr`.** That property gates the *Android framework's* VoLTE
  UI, not the modem, and nothing on Sailfish reads it.
