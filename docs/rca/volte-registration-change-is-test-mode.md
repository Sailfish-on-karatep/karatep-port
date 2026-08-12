# VoLTE never registers — ofono asks the modem the wrong question, then misreads the refusal

**Status: two of the three layers fixed and verified on hardware. The third is a vendor-side
blocker below ofono — see [The layer underneath](#the-layer-underneath-qcrils-qmi-imss-client)
for where this now stands.**

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

## What was done

Both ofono-side defects are fixed in our fork,
[`Sailfish-on-karatep/ofono-binder-plugin-ext-qti`](https://github.com/Sailfish-on-karatep/ofono-binder-plugin-ext-qti)
(branch `hybris-18.1`, built by `scripts/build-extqti.sh`, cloned into `hybris/mw` because that
directory is not `repo`-managed):

1. **`qti_ims_reg_status_response()` now checks `result` before touching the payload.**
   Verified on hardware: `IpMultimediaSystem.Registered` reads `false`, which is the truth.
   The port no longer looks like it has working VoLTE when it does not.
2. **`setServiceStatus` is implemented** (transaction 9, response 6) and sent alongside the
   existing registration request.

Two things had to be right for qcril to accept it, and both were found by trying:

- **`accTechStatus` must not be empty.** With an empty list qcril answers `request misses some
  necessary information` and returns `GENERIC_FAILURE` — indistinguishable, from ofono's side,
  from the modem refusing. One entry naming `RADIO_TECH_LTE` gets it accepted.
- **`QtiRadioServiceStatusInfo` could never have matched a parcel.** `hasIsValid`/`isValid` are
  HIDL `bool` — one byte — but were declared `gboolean`, pushing every later field 8 bytes out
  and making the struct 72 bytes against the interface's 64. Nothing in ext-qti read or wrote
  it before, so the bug was invisible. Confirmed fixed against the wire: the request buffer is
  0x40 = 64 bytes with its two vecs at parent offsets 16 and 40.

With both in place qcril parses the request correctly and gets past validation:

```
qcril_qmi_imss_request_set_ims_srv_status: has_calltype: 1, calltype: 0
qcril_qmi_imss_request_set_ims_srv_status: has_status: 1, status: 2
```

(`status: 0` on Unregister, `2` on Register — `STATUS_ENABLED`. Both fields arrive intact.)

## The layer underneath: qcril's QMI IMSS client

The request is now well-formed, accepted and correctly parsed — and it still fails, one layer
deeper:

```
qcril_qmi_imss_request_set_ims_srv_status: .. qmi send async res 1
sendMessage: msg: IMS_SET_SERVICE_STATUS RESP(type: 2, id: 30), error: 2
```

That `qmi send ... res 1` is the same failure seen on **every** IMSS operation on this device:

| qcril call | result |
|---|---|
| `qcril_qmi_imss_set_ims_test_mode_enabled` (from `requestRegistrationChange`) | `qmi send async res 1` |
| `qcril_qmi_imss_get_client_provisioning_config` | `qmi send sync res 1` |
| `qcril_qmi_imss_request_set_ims_srv_status` (from `setServiceStatus`) | `qmi send async res 1` |

Three unrelated IMSS messages, all failing at the send. Meanwhile `qcril_qmi_nas_*` (network
registration, signal strength) and `qcril_qmi_uim_*` (SIM) work normally throughout. That
pattern says the problem is not any single message but that **qcril has no usable QMI IMS
Settings client** — which also explains why IMSA reports `valid 0` on every field: it has never
received an indication, because the IMS QMI plumbing was never established.

`setConfig` (transaction 12, `CONFIG_ITEM_VOLTE_USER_OPT_IN_STATUS = 33`) is not worth
implementing: it is another IMSS message and would fail in the same place.

### Why the IMSS client never comes up

Restarting `rild` alone and capturing its whole startup (the earlier captures all began long
after boot, so this had never been seen) gives the answer:

```
qcril_qmi_init_core_client_handles: qmi_client_init_instance returned (0) for VOICE
qcril_qmi_init_core_client_handles: qmi_client_init_instance returned (0) for DMS
qcril_qmi_init_core_client_handles: qmi_client_init_instance returned (0) for NAS
qcril_qmi_init_core_client_handles: qmi_client_init_instance returned (0) for PBM
qcril_qmi_init_core_client_handles: qmi_client_init_instance returned (0) for RF SAR
qcril_qmi_init_core_client_handles: qmi_client_init_instance returned (0) for WMS
qcril_qmi_init_core_client_handles: qmi_client_init_instance returned (0) for RFRPE
qcril_qmi_init_ssr_excluded_client_handles: ... status((-3) QMI_TIMEOUT_ERR) for 10
qcril_qmi_init_ssr_excluded_client_handles: ... status((-3) QMI_TIMEOUT_ERR) for 12
qcril_qmi_client_send_msg_sync: svc 12 is not initialized
```

Everything qcril needs initialises except the IMS clients, which **time out**. `svc 12` is
IMSS — hence `res 1` on every send afterwards. This is not a boot race: it reproduces on a
`rild` restart hours after `imsqmidaemon` has been up.

They time out because the services are not there. `/sys/kernel/debug/msm_ipc_router/dump_servers`
lists every QMI service on the IPC router, and the modem (node 0) offers:

```
0x01 0x02 0x03 0x04 0x05 0x07 0x08 0x09 0x0a 0x0b 0x0c 0x0f 0x10 0x11 0x16 0x17 0x18
0x1a 0x1d 0x22 0x24 0x29 0x2a 0x2b 0x2e 0x2f 0x30 0x36 0x37
```

**`0x12` (IMS), `0x1f` (IMSP), `0x20` (IMSVT) and `0x21` (IMSA) are all absent**, while every
service qcril initialised successfully is present. The mapping is self-checking: each client
qcril reports as OK corresponds to a service in that list, and the ones that time out do not.

The modem is not refusing IMS. **The modem is not offering IMS at all.**

### The MBN carrier config was never loaded — and that is a real, separate bug

`persist.vendor.radio.sw_mbn_loaded` was unset and qcril skipped MBN handling entirely.
Setting `persist.vendor.radio.sw_mbn_update=1` makes the state machine run, and it then fails
concretely:

```
qcril_mbn_sw_load_to_db: QCRIL_ERROR:IO: No sw mbn config directory
qcril_mbn_sw_update_init_hdlr: MBN file info update in DB failed...
```

`/vendor/bin/init.qcom.sh` is supposed to populate `/data/vendor/radio/modem_config/mcfg_sw/`
by copying `/vendor/firmware_mnt/image/modem_pr/mcfg/configs/*`. **That tree does not exist on
this device** — `/vendor/firmware_mnt/image/` has no subdirectories at all, and
`/vendor/firmware_mnt/verinfo/ver_info.txt` is missing too, so the version guard always takes
the copy branch and every `cp` inside it fails. The script then runs
`setprop ro.vendor.ril.mbn_copy_completed 1` unconditionally, so nothing ever reports a
problem. The per-carrier flat files the script also names (`3uk`, `gcf`, `mexico`, `ntel`,
`rjil`, `row`, `smtf`, `ytl`) *are* present in `image/`.

Staging those eight into `/data/vendor/radio/modem_config/mcfg_sw/` by hand fixes the pipeline
end to end — `Total number of files retrieved: 8`, `Selected config ... row.mbn`,
`qcril_qmi_pdc_activate_config_ind_hdlr: activate successful`, `sw_mbn_loaded=1`. (Note
`cp --preserve=m` from the vendor script is a GNU-ism the device's `cp` rejects; plain `cp`
works.) Worth knowing: the config the modem had been running was
`OTA_/data/misc/radio/modem_config/mcfg_sw/row.mbn1574430761` — an OTA-updated `row.mbn` from
the stock ROM, pointing at the pre-Android-11 `/data/misc/radio` path.

**But it does not enable IMS.** After a full reboot with `row.mbn` selected and activated, the
modem's service list is byte-identical — still no `0x12`/`0x1f`/`0x20`/`0x21`. `row.mbn` is
the generic "rest of world" config and the smallest of the eight (8.5 KB); it evidently leaves
IMS off. Telephony is otherwise healthy afterwards (registered, LTE, BSNL).

The modem itself is not the limitation. Its firmware —
`MSM8937.LA.2.0-00440-STD.PROD-1.102262.2.113053.1` — contains the whole VoLTE stack:
`PDPRATHandlerVoLTE.cpp`, `RegisterManager.cpp:CRegistrationHandlerVoLTE`,
`IMSSupplementaryService.cpp`. So VoLTE is present in the modem and disabled by configuration.

### Why BSNL gets a config with no IMS in it

Loading the configs makes qcril build two lookup tables in `/data/vendor/radio/qcril.db` —
`qcril_sw_mbn_iin_table` (by SIM IIN, the ICCID prefix) and `qcril_sw_mbn_mcc_mnc_table` — each
carrying a `VOLTE_INFO` column. Pulling that database off the device answers the question
outright:

| config | matches | VoLTE |
|---|---|---|
| `3uk.mbn` | MCC 234/20, 235/94 | VOLTE |
| `mexico.mbn` | MCC 334/5, 334/9, 334/50, 334/90 | VOLTE |
| `rjil.mbn` | MCC **405**/840…874 (Jio) | VOLTE |
| `ytl.mbn` | MCC 502/152 | VOLTE |
| `smtf.mbn` | MCC 510/9, 510/28 | VOLTE |
| `ntel.mbn` | MCC 621/40 | VOLTE |
| `gcf.mbn` | MCC 1/1 (conformance) | VOLTE |
| `row.mbn` | IIN `wild` — **no MCC/MNC rows at all** | — |

**There is no MCC 404 entry anywhere.** India's MCC is 404 for most operators (BSNL is 404/80);
Jio is the exception at 405. So a BSNL SIM matches nothing and falls through to the `wild`
catch-all `row.mbn`.

### What `row.mbn` actually says: IMS off, CS-only voice

Decoding the configs rather than reading their file sizes settles it. `row.mbn` contains
**four** configuration items in total:

| item | `row.mbn` | `rjil.mbn` |
|---|---|---|
| `nv/item_files/ims/IMS_enable` | **0** | 1 |
| `nv/item_files/modem/mmode/voice_domain_pref` | **CsVoiceOnly (0)** | ImsPsVoicePreferred (3) |
| `nv/item_files/modem/mmode/ue_usage_setting` | VoiceCentric (0) | VoiceCentric (0) |
| `nv/item_files/modem/lte/rrc/cap/diff_fdd_tdd_fgi_enable` | 1 | 1 |
| ~60 further `ims/qp_ims_*`, `qipcall_*`, IWLAN and data-profile items | — | present |

Two bytes. Every SIM on this device that is not Jio gets a modem configured with IMS
**disabled** and voice pinned to the circuit-switched domain. That is why the modem publishes
no IMS QMI services (0x12 imss, 0x1f imsp, 0x20 imsvt, 0x21 imsa) for qcril's clients to bind
to, which is why every IMSS request times out, which is why `setServiceStatus` cannot enable
anything. The chain is complete from the two NV bytes up to ofono.

An earlier revision of this document said `row.mbn` "carries exactly one IMS NV item
(`IMS_enable`)" and inferred the modem was told *IMS on* with nothing behind it. That was
wrong, and it pointed at the wrong fix — the item is present and set to **0**.

Forcing `rjil.mbn` by staging it alone does **not** work: the configs are loaded into the
*modem*, not just qcril, and the modem does the matching. With a BSNL SIM it will not select a
Jio config no matter which files are on disk.

### These files are unsigned, so a correct config can be built

Each MBN is an ELF with three segments and a 136-byte hash segment — a 40-byte header plus
three SHA-256 hashes — and **zero trailing bytes**: no RSA signature, no certificate chain. The
scheme reproduces exactly:

```
hash[0] = SHA-256(ELF header + program headers)   <- matches
hash[1] = 00 * 32                                 <- the hash segment itself
hash[2] = SHA-256(MCFG payload)                   <- matches
```

The carrier metadata is equally accessible: in `rjil.mbn` the `MCFG_TRL` trailer sits at
0x7520 and the MCC/MNC pairs are plain adjacent little-endian 16-bit values (405 at 0x75ba,
840 at 0x75bc). So editing a config's carrier match and rehashing it is straightforward.

This is independently confirmed by SBA Research, whose `mbn-mcfg-tools` states plainly that
"the modems we tested with only checked the hashes in the secure boot header and ignored
wrong/missing signatures for MBN MCFG files". That tool parses and repacks these files, and it
round-trips ours **byte-for-byte identically** (`row.mbn` unpacked and repacked is the same
SHA-256), which is as strong a validation of both the tool and the format as is available.

Its decode of the `MCFG_TRL` trailer is also what produced the table above without going
through `qcril.db`: `rjil.mbn` is `Commercial-Reliance`, MCC 405 / MNC 840–874, ICCID prefixes
8991840…; `row.mbn` is `ROW_Generic_3GPP` with an **empty** MNO-id list and a wildcard flag.

### The fix: patch `row.mbn`, do not impersonate a carrier

Because the failing item is a value inside the config the modem *already selects* for this SIM,
there is no need to relabel a foreign carrier's config or forge an MCC/MNC match. Flipping the
two bytes in `row.mbn` is sufficient and is the honest change: the generic 3GPP config keeps its
correct APNs and its correct carrier scope, and only stops asserting that IMS is off.

```
IMS_enable        0 -> 1
voice_domain_pref 0 (CsVoiceOnly) -> 3 (ImsPsVoicePreferred)
```

`voice_domain_pref = 3` is the safe value: IMS is preferred, CS remains the fallback, so a
failure to register on IMS costs nothing. The patched file is the same 8564 bytes, its hashes
recompute correctly, and it re-extracts with the intended values.

Two things this does **not** do, and they are the open risks:

- `row.mbn` has none of the ~60 `qp_ims_*` tuning items `rjil.mbn` carries, so the modem falls
  back to firmware defaults for P-CSCF discovery, codecs and registration timers. Enabling IMS
  may therefore bring the QMI services up without the stack completing registration on BSNL.
  Bringing 0x12/0x1f/0x20/0x21 up in `dump_servers` is the checkpoint that says the barrier is
  crossed; registration is the next question, not the same one.
- BSNL requires VoLTE to be **provisioned on the account** (`ACTVOLTE` to 53733). This is
  widely reported and costs nothing to confirm, and no amount of modem configuration
  substitutes for it.

The alternative — sourcing a config that genuinely covers MCC 404 — is worth keeping in
reserve but is unlikely to be found for this SoC: BSNL's 4G, and with it BSNL VoLTE, only
launched in 2024–2025, whereas the newest config on this device is from 2017. No MSM8937-era
firmware can contain a BSNL VoLTE profile, because BSNL had no VoLTE when it was written. Note
also that the config this modem ran under stock was `OTA_..row.mbn1574430761` — a 2019
OTA-updated `row.mbn`, newer than the 2017 file on the firmware partition and lost with the
data wipe. It is still the most interesting artefact to hunt for, but on the same timeline it
would predate BSNL VoLTE too.

### Getting qcril to actually load it: two more gates

Staging the patched file and forcing a reload is not enough. qcril's SW-MBN state
machine has two further gates, and both are silent when they block.

**Gate 1: the selection flags.** `qcril_qmi_sw_mbn_select_mbn` builds its database query
from `persist.vendor.radio.sw_mbn_volte` and `persist.vendor.radio.sw_mbn_openmkt`. Both
are **empty** on this device, and with them empty the state machine loads all eight configs
into `qcril.db`, never runs a selection query, then deletes every inactive config from the
modem and stops. Set both to `1` and the cascade appears in the log:

```
select FILE from qcril_sw_mbn_mcc_mnc_table where MCC='404' and MNC='80' and VOLTE_INFO='VOLTE' ...
select FILE from qcril_sw_mbn_iin_table    where MCFG_IIN='8991805' ...
select FILE from qcril_sw_mbn_iin_table    where MCFG_IIN='899180'  ...
select FILE from qcril_sw_mbn_iin_table    where MCFG_IIN='wild'      -> row.mbn
```

which is the falling-through-to-the-wildcard behaviour, now visible rather than inferred.
Note `row.mbn` **is** tagged `VOLTE`/`OPENMKT`/`COMMERC` in `qcril_sw_mbn_iin_table`, as is
every other config — the tags come from naming convention, not from contents, so they are
worthless as evidence about what a config does. An earlier version of this document read
those tags as meaningful; they are not.

**Gate 2: the MCFG version.** Having selected `row.mbn`, qcril compares its
`MCFG_VERSION_{FAMILY,OEM,CARRIER,MINOR}` against the config the modem already has active
and skips the reload when they match. Editing NV values without touching the version
therefore changes nothing, silently — the modem keeps the copy it loaded before. The
version is four bytes `[minor, carrier, oem, family]` appearing three times in the file
(`meta.version`, `trailer.version1`, `trailer.version2`), all of which must agree.
`patch-mbn-ims.sh` bumps the minor (50 → 51 here) for exactly this reason.

With both gates open the whole pipeline runs:

```
qcril_qmi_pdc_load_configuration: load_size is 8564, conf_size is 8564
qcril_mbn_sw_send_load_config_resp: Select config for APPS SUB0
qcril_qmi_pdc_activate_config_ind_hdlr: activate successful
qcril_mbn_sw_send_activate_config_resp: Activation completed
```

**and the modem publishes IMS QMI services for the first time.** `dump_servers` went from
none of `0x12`/`0x1f`/`0x20`/`0x21` to three of the four present — `0x20` (imsvt, video
telephony) stays absent, and it turns out not to matter for voice. Two NV bytes and a
version bump; no carrier impersonation anywhere.

That is the barrier crossed. ofono still reports `Registered: false`, which is now a
question about IMS registration on BSNL rather than about whether the modem has an IMS
stack at all.

**It survives a reboot.** `init.qcom.sh` wipes `/data/vendor/radio/modem_config` on every
boot as always, and the IMS services are still published on a cold start — the activated
config lives in the modem's own store, not on disk. The staged files are only the source.
A master copy now sits in `/data/vendor/radio/mbn-master/`, which `init.qcom.sh` does not
touch, so a re-stage does not need the host.

### Where it stops now: no IMS data profile

With the services up, requests reach the modem and come back with real answers instead of
timing out. `ofono`'s `IpMultimediaSystem.Register()` now produces:

```
qcril_qmi_imss_set_qipcall_config_resp_hdlr:  ril_err: 40, qmi res: 54
qcril_qmi_imss_set_reg_mgr_config_resp_hdlr:  ril_err: 40, qmi res: 54
qcril_qmi_imss_set_reg_mgr_config_resp_hdlr:  .. Need to change voice domain pref? No
qcril_qmi_imss_set_reg_mgr_config_resp_hdlr:  .. Failed to change IMS state and remains in state 2
qcril_qmi_imsa_is_ims_registered_for_voip_vt_service: IMS registered valid 1, Status 0
```

`ril_err 40` is `RIL_E_MODEM_ERR` (checked against `hardware/ril/include/telephony/ril.h`,
not from memory — it is *not* `NO_RESOURCES`, which is 42): the modem answered and the
answer was a refusal. Two things in there are new and good: "Need to change voice domain
pref? **No**" means our `voice_domain_pref = 3` is already in effect, and `IMS registered
**valid 1**, Status 0` means IMSA is now returning real data — previously every field came
back `valid 0`, i.e. nothing at all. The modem's IMS stack is running and telling us,
truthfully, that it is not registered.

### Adding an IMS data profile: necessary-looking, not sufficient

`rjil.mbn` carries `data/ds_dsd_attach_profile.txt` and `Data_Profiles/Profile1..3`;
`row.mbn` carries **none**, so the obvious next suspect was that the modem had no IMS APN
to bring a PDN up on.

The `Data_Profiles/ProfileN` format decodes cleanly:

```
byte 0   0x07                     item-file prefix
u16      version (1)
u16      profile number
u32      payload size
u32      TLV count
4B       magic a5 a5 a5 a5
u32,u32  two unidentified values
8B       zero
         then TLVs: u16 id, u16 length, value
```

Jio's `Profile2` is the IMS one and contains nothing Jio-specific: APN `ims` (id 0x1001),
pdp_type 3 = IPv4v6 (0x0011), 0x0025 = 2, 0x001f = 1, everything else zero. `Profile1` is
the default profile with no APN at all and `Profile3` is `SOS`. Slots 1/2/3 =
default/ims/emergency is the Qualcomm convention. Grepping the whole config, the only
Jio-identifying content anywhere is in `data/andsf.xml`, `data/default_andsf.xml` and
`data/iwlan_s2b_config.txt` (the `epdg_fqdn`), none of which is the data profile.

**Adding an item means editing `nv_items`, not just dropping a file in.** `mbn-tool`'s
packer iterates the `nv_items` JSON and reads each item's bytes from `files/`; a file with
no matching item entry is silently ignored. The entry is small:

```json
{"type": 2, "attributes": 25, "reserved": 0,
 "filename": {"hex": "...", "ascii": "/Data_Profiles/Profile2 ", "__type__": "bytes"},
 "data_magic": 7, "__type__": "MCFG_Item"}
```

That was done (minor version 52, 8732 bytes), and it loads, selects and activates. It is
**not** sufficient. After a reboot the modem still reports:

```
qcril_qmi_imsa_reg_status_ind_hdlr:      ims_registered: 0
qcril_qmi_imsa_get_ims_registration_info: ims_registration_network: 14   (LTE)
qcril_qmi_imsa_get_ims_registration_info: ims registration error code: 0
qcril_data_process_qmi_dsd_ind: pdn[0] name=bsnlnet
```

One PDN, the internet one. **No IMS PDN is ever brought up**, every `rmnet_data*` is DOWN,
and `ofono`'s `Register()` returns `org.ofono.Error.Failed` with the same
`setServiceStatus` refusal underneath.

Two things did improve, and both are real: `qcril_qmi_imss_get_client_provisioning_config`
now returns `qmi send sync res 0` where it returned `1` before, and IMSA now pushes
registration-status *indications* rather than being silent. The IMS stack is running and
reporting; it simply never starts a registration.

The remaining asymmetry is the IMS parameter set itself. `rjil.mbn` has roughly fifty
`nv/item_files/ims/*` items — `ims_operation_mode`, `ims_hybrid_enable`, `qp_ims_config`,
`qp_ims_reg_config`, the `qipcall_*` family — and `row.mbn` has exactly one (`IMS_enable`).
A modem IMS stack with no registration configuration plausibly declines to start, which
would explain both the absent PDN and the `RIL_E_MODEM_ERR` on every attempt to enable a
service. Scanned for carrier identity, only two of those items are Jio-specific:
`qp_ims_ut_config` (XCAP server `jionet`) and `qp_ims_sms_config` (short code `10138`).
The rest are generic 3GPP values — codec lists, service URNs, timers.

### The IMS parameter set, and the one item that matters

Importing the 46 generic items (all of `nv/item_files/ims/*` except `IMS_enable`, already
present, and the two carrier-specific ones) produced the first unambiguous acceptance:

```
ims:imsradio0 IMS voice service enabled
```

`setServiceStatus` succeeds, `Register()` returns instead of `org.ofono.Error.Failed`, and
there are **no `ril_err` / `qmi res:` / "Failed to change IMS state" lines left at all**.

Bisecting it took three rounds, each a stage, forced reload, reboot and test:

| round | items | `setServiceStatus` |
|---|---|---|
| 1 | the 21 non-`qipcall_*` items | **refused** |
| 2 | the 25 `qipcall_*` items | **accepted** |
| 3 | `qipcall_config_items` alone | **accepted** |

One item. `nv/item_files/ims/qipcall_config_items` is the QIPCALL configuration blob, and
decoded it reads:

```
Version 26, EnableRtcpForActiveVoipCall 1, DesiredQosStrength 1,
VideoMediaProfileMode 3, VtCallingEnabled 1, MobileDataEnabled 1,
VolteDisabled 0, CvoEnabled 1, VideoFeatureTag "video"
```

`VolteDisabled = 0`. With the item absent the modem has no QIPCALL configuration at all,
`qcril_qmi_imss_set_qipcall_config` fails with `RIL_E_MODEM_ERR`, and nothing above it can
enable a service. It contains no carrier identity — it is a generic VoLTE/VT enablement
blob — so this is a defensible thing to carry rather than a borrowed setting.

The other group is not useless. With only `qipcall_config_items` the reported radio
technology is `radiotech:21` (INVALID) and `Register()` still returns
`org.ofono.Error.Failed`, because `requestRegistrationChange` needs something in the
non-`qipcall` set; with all 46 it reads `radiotech:15` (LTE) and `Register()` succeeds. So
the minimum for "VoLTE is permitted" is one item; the minimum for "the whole path works" is
larger and has not been bisected. The device is left carrying all 46.

### The network is not the problem

The SIM is BSNL **Tamil Nadu**. Before it went into this device it was in another handset
that does VoLTE, at the same location, and it registered immediately and placed calls. So
BSNL is providing IMS to this subscriber, on this cell, right now. Every remaining
hypothesis has to be a handset-side one.

That also matches what BSNL users report and what a widely circulated
[r/bsnl thread](https://www.reddit.com/r/bsnl/comments/1v9rfn4/how_to_activate_volte/)
describes: BSNL had no 4G for the best part of a decade, so shipped carrier configs do not
identify it as a VoLTE carrier, Android's telephony framework hides the VoLTE switch as a
result, and the provisioning value behind that switch is never written. On Xiaomi builds
the `*#*#86583#*#*` dialer code forces the switch open and registration follows within
seconds. Customer care and a BSNL office had both failed to "activate" anything, because
there was nothing on the network side to activate.

### The provisioning half: setConfig

That is a lever we did not have. `setServiceStatus` tells the modem which IMS services to
run; nothing was telling it the subscriber is allowed to run them. Recovering the interface
from `ims.apk` again:

```
setConfig            transaction 12, response 9,  setConfig(int32 token, ConfigInfo)
ConfigInfo           40 bytes: item@0, hasBoolValue@4, boolValue@5, intValue@8,
                     stringValue@16, errorCause@32
CONFIG_ITEM_VLT_SETTING_ENABLED        = 11
CONFIG_ITEM_MOBILE_DATA_ENABLED        = 26
CONFIG_ITEM_VOLTE_USER_OPT_IN_STATUS   = 33
ConfigFailureCause   0 NO_ERR, 1 IMS_NOT_READY, 2 FILE_NOT_AVAILABLE,
                     3 READ_FAILED, 4 WRITE_FAILED, 5 OTHER_INTERNAL_ERR
```

Implemented in the fork, and it took two corrections:

- **qcril picks the value slot per item, not from `hasBoolValue`.** The first build sent the
  value only in `boolValue` and qcril logged `Set config PRESENCE
  volte_user_opted_in_status to: 0` — request delivered, wrong value. It reads `intValue`
  and logs `type: 0` for these items. Filling both slots fixes it, and the log then reads
  `to: 1`.
- **`VOLTE_USER_OPT_IN_STATUS` (33) goes to qcril's *presence* config handler**, and that
  write returns `ConfigFailureCause 4` = `CONFIG_WRITE_FAILED` on this modem however
  correct the request is. `VLT_SETTING_ENABLED` (11) routes through the IMS settings
  service instead and is **accepted**. Both are sent; 11 succeeds, 33 fails.

So the current state of the device is: IMS enabled in NV, voice domain IMS-preferred, the
IMS parameter set present, an IMS data profile present, `VLT_SETTING_ENABLED = 1` accepted,
the VoLTE service status accepted — and every request the stack makes now returns success.

### The call our stack has never made

The BSNL SIM was moved into a handset where its VoLTE works — a Xiaomi `aliothin`
(M2012K11AI, Snapdragon 870), stock, unrooted — and its radio log read over `adb`. The
timeline is unambiguous:

```
08-12 15:00:07  card[0] state:card_state:1              SIM inserted
08-12 15:00:11  ACTION_SIM_STATE_CHANGED LOADED
08-12 15:00:11  qcril_qmi_imss_set_ims_service_enable_config_resp_hdlr: ril_err: 0, qmi res: 0
08-12 15:00:11  qcril_qmi_imss_set_ims_service_enable_config_resp_ims_reg_change_hdlr: ril_err: 0, qmi res: 0
08-12 15:00:15  SST: [0] setImsRegistrationState: {registered=true mImsRegistrationOnOff=true}
```

**IMS registered eight seconds after the SIM went in, off the back of
`set_ims_service_enable_config`** — QMI IMSS "set IMS service enable config". No reboot, no
carrier-config reload.

Our qcril has that same function:

```
qcril_qmi_imss_set_ims_service_enabled
qcril_qmi_imss_set_ims_service_enable_config_resp_hdlr
qcril_qmi_radio_config_imss_set_ims_service_enable_config_req_handler
```

and **it has never been called** — zero occurrences across the entire radio buffer, through
every experiment in this document. `setServiceStatus` on this qcril generation routes to
`set_qipcall_config` + `set_reg_mgr_config` instead, and `setConfig` routes by item:

| ims.apk `ConfigItem` | radio config | handler |
|---|---|---|
| `VLT_SETTING_ENABLED` (11) | 23 | `set_client_provisioning_config` → `CLIENT_PROVISIONING_ENABLE_VOLTE` |
| `VOLTE_USER_OPT_IN_STATUS` (33) | 42 | `set_presence_config` → `PRESENCE_VOLTE_USER_OPTED_IN_STATUS` (write fails) |

Neither is the service-enable path. Of qcril's radio-config item names, exactly one belongs
to that family — `QCRIL_QMI_RADIO_CONFIG_IMS_SERVICE_ENABLE_MOBILE_DATA_ENABLED` — and the
`ConfigItem` that plausibly maps to it is `CONFIG_ITEM_MOBILE_DATA_ENABLED` (26), which we
have never sent. Also unexplored and in the same neighbourhood:
`QCRIL_QMI_RADIO_CONFIG_QIPCALL_VOLTE_ENABLED`.

So the modem has been told, in this document's own words, "everything" — except the one
thing the working handset actually says to it.

### The reference firmware settles it: no carrier config matches BSNL, in 2017 or 2024

The reference handset's fastboot ROM (`alioth_in_global`, `OS1.0.2.0.TKHINXM`, June 2024)
carries `images/NON-HLOS.bin` — a FAT16 modem filesystem, extractable with `mtools`, no root
needed — holding **266** software carrier configs under
`image/modem_pr/mcfg/configs/mcfg_sw/`. Parsing every one of their `MCFG_TRL` trailers with
the same `mbn-tool` used throughout:

| config | MCC 404 coverage |
|---|---|
| `Commercial-Airtel` | 404/2, 3, 10, 16, 31, 40, 45, 49, 70, 90, 92, 93, 94, 95, 96, 97, 98 |
| `IDEA_Commercial` | 404/4, 7, 12, 14, 19, 22, 24, 44, 56, 78, 82, 87, 89 |
| `Commercial-Vodafone` (India) | 404/1, 5, 11, 13, 15, 20, 27, 30, 43, 46, 60, 84, 86, 88 |
| `Commercial-Reliance` | **none** — MCC 405 only, plus Jio's `89918xx` IINs |

**MNC 80 appears in none of them.** A 2024 Qualcomm config set, from a phone on which BSNL
VoLTE demonstrably works, still has no configuration for BSNL. The finding this document
made early on — "there is no MCC 404 entry anywhere" — was right, and stays right seven
years later.

Two things follow immediately.

**The `Commercial-Reliance` property is stale, conclusively.** That config matches MCC 405
and Jio ICCID prefixes; it cannot be selected for a BSNL SIM by any matching rule. So it
dates from a Jio card in that phone's slot 0, exactly as suspected. The relabelling plan
built on it is dead, and it should never have been proposed on that evidence.

**BSNL falls through to the generic config there too** — same wildcard IIN `8949024`, the
same slot our `ROW_Generic_3GPP` occupies. And that is where the real difference lives:

| | our 2017 `row.mbn` | their 2024 `ROW_Commercial` |
|---|---|---|
| `ims/IMS_enable` | **0** | **1** |
| `modem/mmode/voice_domain_pref` | **CsVoiceOnly** | **ImsPsVoicePreferred** |
| total items | 4 | 74 |

Qualcomm turned IMS on in the generic fall-through config somewhere between the two, and
populated it. The two-byte patch built here by hand reproduces exactly what they later
shipped, which is about as good a validation of the approach as could be hoped for.

The 74-item reference is not directly transplantable — the newer modem uses a different NV
item generation (`RegistrationConfiguration`, `IMSVoiceDynamicConfig`,
`qp_ims_service_enablement_config`, `ims_sip_config`, `ims_user_agent`) where ours uses the
`qp_ims_*` / `qipcall_*` family. But three of its items map onto gaps already identified
here and are worth trying: `data/ds_dsd_attach_profile.txt`, and `Data_Profiles/Profile1`
and `Profile3`, where the patched config carries only `Profile2`.

### The Reliance config property is stale — see above

The decisive evidence came from putting the BSNL SIM into a handset where its VoLTE works —
a Xiaomi `aliothin` (M2012K11AI, Snapdragon 870), stock, unrooted — and reading its state
over `adb`. Two things it says are worth more than everything inferred up to this point.

**It selects Jio's carrier config for the BSNL SIM.**

```
persist.radio.mbn_sw_sub0 = NV#71546=7;Commercial-Reliance(0x0A011B16)
persist.radio.mbn_sw_sub1 = NV#71546=23;Inactive
```

`sub0` is the BSNL subscription — the only SIM present, `phoneId=0 subId=7`, numeric
`40480`, data on `bsnlnet`. The active software MBN for it is **`Commercial-Reliance`**,
which is the same operator string carried by this device's own `rjil.mbn`. A handset on
which BSNL VoLTE demonstrably works is running BSNL on the Reliance commercial config.

**This does not establish what it first appeared to.** `persist.radio.*` is persistent
across SIM changes, and this phone's subscription history shows a Jio card
(`carrierId=2018`, `simSlotIndex=-1`, `portIndex=0`) that has previously occupied slot 0 —
every subscription on it is `isEmbedded=false`, i.e. a real card, not an eSIM profile.
Worse, the radio log covering the BSNL insertion at 15:00:07 contains **no MBN or config
selection activity at all**, so nothing shows the property being rewritten for this SIM.

It is therefore quite possible the modem is running a Jio-selected config and BSNL VoLTE
works on it regardless — which would be a different and weaker claim than "the modem
selects Reliance for BSNL". An earlier revision of this document asserted the strong
version and used it to reverse the judgement on relabelling `rjil.mbn`. That was premature:
the observation is real, the inference was not established, and the credit for catching it
belongs to the reviewer, not the analysis.

Settling it needs the property observed being rewritten — a reboot of that handset with the
BSNL SIM in, or a SIM reinsert with the vendor RIL at a verbosity that logs selection.
Neither has been done. Until then, treat the Reliance observation as suggestive only, and
prefer the service-enable finding above, which rests on a logged call rather than on a
persistent property of unknown age.

**And there is no IMS APN data call.** The only `ApnSetting` in
`mPreciseDataConnectionStates` is `bsnlnet` with types `supl | hipri | default`. A handset
doing VoLTE on BSNL right now has no framework-visible `ims` PDN at all, which confirms the
modem establishes its own internally and makes the AP-side IMS context activated earlier a
harmless red herring rather than the mechanism.

Supporting values from its carrier config, which also close off two suspicions:
`carrier_volte_provisioning_required_bool = false` — so no provisioning gate, and the
`VOLTE_USER_OPT_IN_STATUS` write this modem refuses is not the blocker — and
`allowed_initial_attach_apn_types_string_array = [ia, default, mms, dun]`, with `ims`
deliberately absent from the attach types.

The next experiment follows directly: add BSNL's match (MCC 404 / MNC 80, and the IIN
`8991805`) to `rjil.mbn`'s `MCFG_TRL` trailer so the modem selects it for this SIM. The
edit round-trips through `mbn-tool` cleanly — appending an `MnoId` to `trailer.mnoid.ids`
repacks and reads back correctly — so it is mechanically straightforward.

### Why the Pixel/Xiaomi class of fix has nothing left to give us

[`kyujin-cho/pixel-volte-patch`](https://github.com/kyujin-cho/pixel-volte-patch) is the
best-documented example of the genre, and reading what it actually does is a useful
negative result. It calls `telephony.ICarrierConfigLoader.overrideConfig()` through Shizuku
to force the carrier-config values that `ImsManager.isVolteEnabledByPlatform()` checks —
entirely inside the Android framework, no root, and explicitly **nothing to the modem**.
The Xiaomi `*#*#86583#*#*` code is the same idea by another route.

That whole family of fixes exists to make the *framework* stop hiding the VoLTE switch. We
have no framework: ofono consults no carrier database, hides nothing, and calls the vendor
HAL directly. We are already past the layer those patches operate on, and have been since
`setServiceStatus` started being accepted.

The useful inference runs the other way. On a working Android handset the framework, once
unblocked, does nothing to the modem beyond the provisioning calls — and those are exactly
the calls we already make and that this modem already accepts. So a stock device with this
firmware would be issuing the same sequence we issue and getting a registration. Our
remaining gap is therefore below that line: in the modem's own configuration or state, not
in a command we have failed to send.

### Still not registering

```
QtiRadioRegInfo state:1 radiotech:15 error_code:0     (NOT_REGISTERED, LTE, no error)
qcril_data_process_qmi_dsd_ind: pdn[0] name=bsnlnet
```

One PDN, no `rmnet_data*` addresses, no IMS PDN, `error_code: 0`, unchanged across a
reboot. Nothing refuses anything any more; the modem simply never starts a registration.

### The IMS PDN: ofono has one and never activates it

Nothing in the modem was ever going to request it. ofono already has the context —
`/ril_0/context3`, `Type: ims`, `AccessPointName: ims`, `Active: false` — and activating it
by hand brings up a real IMS bearer:

```
qcril_data_apn_based_profile_look_up_using_qdp: qdp param PROFILE_ID = [2]
qcril_data_apn_based_profile_look_up_using_qdp: successfully looked up 3gpp profile id [2]
RIL_REQUEST_SETUP_DATA_CALL (27) Complete ... Success
qcril_data_process_qmi_dsd_ind: pdn[0] name=bsnlnet
qcril_data_process_qmi_dsd_ind: pdn[1] name=ims
rmnet_data1  inet 10.67.1.133/30
```

Two things worth keeping from that. The profile lookup lands on **`PROFILE_ID = [2]`** —
the `Data_Profiles/Profile2` we imported, so that work is doing exactly what it was meant
to. And one line fails:

```
qcril_data_set_apn_types: Failed to set apn type rc [0] result [1] error [57]
```

qcril cannot tag the call with its APN types.

**That is not ofono's fault, and the obvious fix would have been wasted work.** Reading
`ofono-binder-plugin` 1.1.25, which is what is installed: `binder_data_call_setup` maps
`OFONO_GPRS_CONTEXT_TYPE_IMS` to `RADIO_DATA_PROFILE_IMS`, and every `setupDataCall`
variant including the sub-1.4 HIDL one this device uses sends both
`dp->profileId = setup->profile_id` and
`dp->supportedApnTypesBitmap = binder_radio_apn_types_for_profile(...)`, which sets
`RADIO_APN_TYPE_IMS` for that profile id. The whole path is gated on `use_data_profiles`,
whose default is `TRUE` (`BINDER_DEFAULT_SLOT_USE_DATA_PROFILES`) and which `binder.conf`
does not override. So the IMS profile id and the IMS APN type bit are both on the wire
already, and qcril's own log agrees — it looks up and finds `3gpp profile id [2]`, our
imported `Profile2`.

`qcril_data_set_apn_types` is qcril talking to the modem over dsi_netctrl *after* that
lookup has already succeeded, and the call completes regardless. So this is a
qcril-to-modem limitation on 2017 MSM8937 firmware, logged at error level and apparently
survivable, not a missing field from our side. Do not fork `ofono-binder-plugin` for it.

### RegOnMode: the imported config registers only on a call

`qp_ims_reg_config` as shipped by Jio carries `RegOnMode = OnCall (1)` — the IMS
registration manager registers when a call is placed, not when IMS becomes available.
Wholesale-importing another operator's IMS parameter set brings that along, and it produces
exactly the symptom seen: every request succeeds, the modem reports no error, and nothing
ever registers while idle.

`scripts/mbn-set-regonmode.sh` flips it to `PowerOn (0)`, which is what an
always-registered stack and a VoLTE indicator need. Verified in the packed file. It did
**not** by itself produce a registration, with or without the IMS bearer up, so it is a
correctness fix rather than the answer.

### What the QMI service IDs actually are

Settled from the device rather than from a published table. `/vendor/lib64/libqmiservices.so`
exports one `<name>_qmi_idl_service_object_v01` data symbol per service, and the
`qmi_idl_service_object` structure carries the service ID as its third `uint32`. Reading
them straight out of the ELF:

| id | service | published by this modem |
|---|---|---|
| `0x12` | **imss** — IMS Settings | yes |
| `0x13` | ims_qmi | no |
| `0x1f` | **imsp** — IMS Presence | **yes** |
| `0x20` | **imsvt** — IMS Video Telephony | **no** |
| `0x21` | **imsa** — IMS Application | yes |
| `0x24` | pdc | yes |
| `0x28` | imsrtp | yes |
| `0x46` | **lte** | **no** |
| `0x4d` | imsprivate | no |

Two things this document got wrong are now corrected, and both mattered.

**`0x20` is IMS Video Telephony, not presence.** The absent service is VT, which VoLTE
voice does not need. Every service the voice path does need — settings, presence,
application, RTP — is present. So "the modem is missing an IMS QMI service" has been a red
herring for several rounds: it is missing the one for video calling.

**Presence is present**, at `0x1f`. So `VOLTE_USER_OPT_IN_STATUS` returning
`CONFIG_WRITE_FAILED` is *not* the service being absent, as guessed above — the write
reaches a live presence service and that service refuses it. Worth noting too that the item
is a *presence* config, i.e. about RCS presence opt-in; the VoLTE setting proper is
`VLT_SETTING_ENABLED`, which this modem accepts.

**And the QMI LTE client's timeout is explained**: `lte` is service `0x46`, and the modem
publishes nothing above `0x37` in that range. The service does not exist on this firmware,
so qcril's attempt to bind it can only ever time out. That is a cosmetic init failure, not
a fault to chase.

### qcril's QMI LTE client times out at every start

Watching a full `rild` start rather than the IMS path alone turns up something that was
never visible before:

```
qcril_qmi_init_core_client_handles: qmi_client_init_instance returned (-3) for LTE
qcril_qmi_init_core_client_handles: qmi_client_init_instance returned failure((-3) QMI_TIMEOUT_ERR) for QMI LTE
```

Every other core client — VOICE, DMS, NAS, PBM, RF SAR, WMS, RFRPE — returns 0. This one
times out, on every start, and it has presumably been doing so since the port began.
Resolved by the service-ID table above: `lte` is `0x46`, this modem does not publish it,
and the timeout is therefore expected and harmless.

For the record, the complete QMI service list the modem publishes in this state:

```
01 02 03 04 05 07 08 09 0a 0b 0c 0e 0f 10 11 12 16 17 18 1a 1c 1d 1f 21 22 24 28 29 2a 2b
2e 2f 30 33 34 35 36 37  100 104 105 107 108 10d 10f 111 112 113 114 115 117 118 119 11b
11f 125 128 129 12a 12b 12c 12e 12f 131  301 302 303  1000 1001
```

`0x20` is absent and has been absent under every configuration tried.

### The config-item sweep: what this qcril will and will not do

`getConfig` (transaction 13, response 10) takes the same `ConfigInfo` as `setConfig` with
only `item` filled in and writes nothing, so sweeping all 72 items is free of side effects.
Built into the fork behind `QTI_IMS_CONFIG_PROBE`, it produced a clean split — **51 items
supported, 21 refused**:

| | items |
|---|---|
| supported | 1–13, 25, **26 (`MOBILE_DATA_ENABLED`)**, 27, 29, 31, 34–43, 45–56, 58–68 |
| refused | 14, 15, **16–24**, 28, **30**, **32**, **33 (`VOLTE_USER_OPT_IN_STATUS`)**, 44, 57, 69–72 |

Two conclusions, both firm.

**The presence family is simply absent from this build.** Items 16–24 are the presence and
publish group, and together with 30, 32 and 33 they are refused on *read* as well as write.
So `VOLTE_USER_OPT_IN_STATUS` returning `CONFIG_WRITE_FAILED` was never the modem declining
a value — that whole config path is unimplemented here. It has been dropped from the
plugin, and the thread is closed. Note this is a different explanation from the one offered
earlier in this document, which guessed at an absent presence *service*; the service is
live at `0x1f`, it is qcril's config path that is missing.

**`MOBILE_DATA_ENABLED` (26) is supported**, which makes it the viable candidate for
reaching `QCRIL_QMI_RADIO_CONFIG_IMS_SERVICE_ENABLE_MOBILE_DATA_ENABLED` and thence QMI
IMSS "set IMS service enable config". It is now sent alongside item 11, and the HAL
**accepts** it (`setConfig accepted`).

IMS still does not register, and one thing is *not* established: whether item 26 actually
dispatches to the service-enable handler. `set_ims_service_enable_config` has still never
appeared in any log, but neither have the `map_ims_config_to_radio_config_item` lines that
were visible for items 11 and 33 earlier — so the routing for 26 is unconfirmed rather than
disproved. The radio buffer is extremely chatty and rotates fast; catching it needs the
capture armed before the request and correlated against ofono's own log in the same window,
which has not yet been done cleanly.

### "setConfig accepted" does not mean the modem heard anything

A correlated capture — 16 MB radio buffer, unfiltered, armed before the request, with
ofono's journal for the same window — shows the problem with every success reported above:

```
ofono   16:24:00.569  ims:Setting config item 11 to 1
ofono   16:24:00.569  imsradio0< [00000007] 12 setConfig
ofono   16:24:00.589  imsradio0> [00000007] 9 setConfig
ofono   16:24:00.589  ims:imsradio0 setConfig accepted
radio   16:24:00        (nothing -- zero RILQ lines at that second)
```

9,881 RILQ lines in the window, none of them at the moment of the call, and no
`map_ims_config_to_radio_config_item`, no handler line, no `service_enable`. Repeated with
`rild` restarted first and three separate rounds of items 11 and 26: same result every time.

The `Mapped ims config: 11 to radio config: 23` lines quoted earlier in this document are
real, but they come from **one** run, on 08-12 at 08:33. No capture since has reproduced
them, including for item 11, which is unchanged. So somewhere between then and now the
requests stopped reaching qcril's radio-config layer, while the HAL kept answering them
within 20 ms with success.

That is a serious problem for everything built on top. **`setConfig accepted` and
`IMS voice service enabled` are not evidence that the modem received or acted on anything**
— they are evidence that `ImsRadioImpl` inside `rild` returned a result. The same doubt
applies to `setServiceStatus` being "accepted" and, by extension, to the conclusion that
importing `qipcall_config_items` made the modem "accept IMS". That bisect measured a change
in what the HAL returned; whether it measured a change in the modem is now open.

Nothing above needs retracting on the *carrier config* side — `IMS_enable`, the MBN load,
select and activate all have qcril's own log lines and are independently confirmed by
`dump_servers` changing. But every conclusion resting only on a HIDL return code should be
treated as unverified until it can be seen from qcril's side or from QMI directly.

This is the strongest argument yet for reading `imsa`/`imss` over QMI rather than inferring
from the HAL boundary.

### The modem's own answer: client provisioning is off

qcril's logging is gated by `persist.vendor.radio.adb_log_on`, which was already `1`, but
`persist.vendor.radio.ril_extra_debug` and `ril_log_enabled` were **empty**. Setting both to
`1` makes qcril log what it reads back from the modem at init — which is the first direct
sight of modem state in this whole investigation, without writing a QMI client:

```
qcril_qmi_imss_get_ims_reg_config:              qmi send sync res 0
qcril_qmi_imss_get_ims_reg_config:              IMS has_state: 1, state: 1
qcril_qmi_imss_get_client_provisioning_config:  qmi send sync res 0
qcril_qmi_imss_get_client_provisioning_config:  client_prov_enabled_valid: 1, client_prov_enabled: 0
qcril_qmi_imss_get_client_provisioning_config:  wifi_call_valid: 1, wifi_call: 2
```

**`client_prov_enabled: 0`.** The modem reports IMS client provisioning disabled, and it
says so with `valid: 1`, so this is a real value and not an absent field. A modem that
considers the client unprovisioned does not register, which is consistent with everything
observed: no error, no attempt, no IMS PDN.

The lever for it is already known from the one good capture: ims `ConfigItem`
`VLT_SETTING_ENABLED` (11) maps to radio config 23,
`qcril_qmi_radio_config_imss_set_client_provisioning_config_req_handler`,
`QCRIL_QMI_RADIO_CONFIG_CLIENT_PROVISIONING_ENABLE_VOLTE`. That is exactly the item the
plugin sends. So the shape of the remaining problem is narrow:

> the modem needs `client_prov_enabled = 1`; the call that sets it is one we already make;
> the HAL says it is accepted; and the modem still reads back 0.

Which is the delivery question from the previous section, now with a specific value to watch
rather than a registration to wait for. The follow-up test — set via ofono, then restart
`rild` and read the value back at init — did not produce data (the capture came back empty
twice), so it remains untested rather than answered.

If delivery turns out to work and the value still will not stick, the alternative is to find
the NV item behind client provisioning and set it through the carrier config, the way
`IMS_enable` and `qipcall_config_items` were — which is a route this port has already proven
it can take.

**The instrumentation problem has a one-flag answer.** `logcat --regex <expr> -m <count>`
filters *inside* logcat and exits on its own, so it cannot be outrun the way a piped `grep`
is, and it does not care that the buffer holds seventeen seconds. Every capture failure
below was avoidable:

```sh
logcat -b radio --regex "client_prov_enabled" -m 4 > out.log &
```

With that, the measurement finally works, and it is unambiguous:

```
BEFORE  RIL[0][main]  get_client_provisioning_config: client_prov_enabled_valid: 1, client_prov_enabled: 0
        RIL[0][event] client_provisioning_config_ind_hdlr: client_prov_enabled_valid: 1, client_prov_enabled: 0
                 ... ofono restart, setConfig(VLT_SETTING_ENABLED) sent and "accepted" ...
AFTER   RIL[0][main]  get_client_provisioning_config: client_prov_enabled_valid: 1, client_prov_enabled: 0
```

**The value does not move.** Our `setConfig` does not change the modem's IMS client
provisioning, whatever the HAL returns. That is the first hard confirmation that the ofono
route, as currently used, does not do the thing it appears to do — and it retires the last
theory that could be tested from the ofono side.

Note also `client_provisioning_config_ind_hdlr`: the modem *pushes* provisioning state as an
indication, and that too reads 0. So this is the modem's settled view, not a stale query.

One more trap worth recording, because it silently invalidated several runs above: **ofono
calls `set_registration` only when it decides the desired state changed.** Restarting ofono
does not reliably fire it, and `IpMultimediaSystem.Register()` can return success having
done nothing. Any test that depends on the plugin sending something must verify ofono
actually logged `Setting config item` in the same window, or it is measuring nothing.

**Reading it a second time proved harder than reading it once.** Five attempts to re-capture
`client_prov_enabled` after a `setConfig` all failed, in three distinct ways, and the reason
matters for anyone repeating this:

- With `ril_extra_debug=1` the radio log is fast enough that `logcat` dies with
  `Unexpected EOF!` after about 20 seconds — the overrun this port's own notes already
  warn about, walked straight back into.
- `logcat -d` is no help either: with that verbosity the radio buffer holds **17 seconds**
  (`-G 16M` notwithstanding), so by the time a test sequence finishes the interesting lines
  are long gone.
- Filtering inside the device pipeline keeps the file small but does not save `logcat`
  itself, which still overruns and takes the pipeline with it.

The one successful capture set the three properties and restarted `rild` in the same run,
with an unfiltered stream started two seconds earlier. Later runs with the properties
already set never reproduced the `[main] get_client_provisioning_config` lines at all, which
suggests those may only be emitted by a qcril that starts *after* the settings change. Not
understood; recorded so the next attempt starts from the working recipe rather than
rediscovering the failures.

`ril_extra_debug` and `ril_log_enabled` have been set back to `0` — they flood the log and
degrade the device — but they are the lever, and they ship empty.

Also noted in passing: `persist.vendor.radio.vdp_on_ims_cap` is empty, and
`qcril_qmi_imss_update_wifi_pref_from_ind_to_qcril_data` fails with
`qcril_data_set_rat_preference res = 501 / QCRIL DATA API returned error` on every init.
Neither is understood; the second is a real error nothing has accounted for.

### Nokia 6 (D1C): same SoC, newer modem, and the end of the ROM hunt

The Nokia 6 TA-1021 is MSM8937 — the same SoC as karatep — and its Pie firmware carries a
modem a whole branch newer than ours:

| | modem build |
|---|---|
| karatep (Nov 2017) | `MSM8937.LA.2.0-00440` |
| Nokia 6 India (Feb 2019) | `MSM8937.LA.3.1.2-00360` |
| Nokia 6 Global (Oct 2019) | `MSM8937.LA.3.1.2-00360` |

Both images yield ~182 configs from `NON-HLOS.bin`. Three things come out of them, and
together they close this line of enquiry.

**No BSNL config, again.** Searching every config for the MnoId pair `404/80` byte-for-byte:
zero hits in either image. Only four configs carry any MCC 404 at all — `w_one`, `airtel`,
`idea`, `vdf/india` — the same picture as the 2024 Xiaomi set. That is now **three
independent firmwares across seven years** (2017 Lenovo, 2019 Nokia India, 2024 Xiaomi) with
no configuration for BSNL. The conclusion is not going to change with a fourth image.

**Their generic config confirms the patch, for the third time.** `generic/common/row/commerci`
is `ROW_Commercial`, a true wildcard (no IINs, no MNO ids), and it carries:

```
ims/IMS_enable                    = 1
modem/mmode/voice_domain_pref     = ImsPsVoicePreferred
64 items
```

Against our 2017 `ROW_Generic_3GPP`'s `0`, `CsVoiceOnly` and 4 items. Whatever else is
unresolved, the two-byte patch is unambiguously the right change.

**But it cannot be transplanted, and neither could any newer image.** Two independent
blockers:

- `format_type = 4`, where every config this modem loads is `format_type = 3`.
- The IMS NV item generation changed with the modem branch. The Nokia config uses
  `RegistrationConfiguration`, `IMSVoiceDynamicConfig`, `qp_ims_service_enablement_config`,
  `ims_sip_config`, `ims_user_agent` — the same names as the 2024 Xiaomi set and *none* of
  the `qp_ims_*` / `qipcall_*` names our LA.2.0 modem and its own `rjil.mbn` use.

So the item that looks most relevant — `qp_ims_service_enablement_config` — describes service
enablement on **LA.3.1.2**, and tells us nothing about where LA.2.0 keeps it. Newer firmware
for this SoC exists and is readable, and it still cannot be backported: the boundary is the
modem branch, not the chip.

**Stop hunting ROMs.** The remaining answer is on the device, not in an image.

### Open threads

Every QMI service the VoLTE voice path needs is present, every setting is accepted, a
bearer can be established on the right profile, and the modem does not register. What is
left to find is why the modem's IMS stack never starts, and the useful next question is
what it reports about itself rather than what it accepts from us:

1. **Ask the modem directly.** qcril only ever logs what it sends. `imss` (`0x12`) and
   `imsa` (`0x21`) are both live and both have *get* messages — service enable config,
   registration status, service status. A small QMI client over `/dev/socket/qmux_radio` or
   the IPC router, using the service objects in `libqmiservices.so`, would show the modem's
   own view instead of ours. That is the first thing that would distinguish "configured but
   idle" from "trying and failing silently".
2. **`VOLTE_USER_OPT_IN_STATUS` still cannot be written**, even though presence is live. If
   it is genuinely the flag the Xiaomi code writes, the remaining fix may be to set the
   underlying NV item through the carrier config instead of over QMI, the same way
   `IMS_enable` and `qipcall_config_items` were.
3. **ofono never activates the IMS context on its own.** Whether that matters depends on
   whether this modem's IMS stack wants an AP-established bearer at all; on modem-side IMS
   it normally brings up its own. Worth settling before building anything.

### Housekeeping noticed on the way

`/etc/ofono/ril_subscription.conf` is still on the device and still sets
`useDataProfiles=true`. It is dead: that file belonged to the grilio RIL plugin this port
migrated off, and `ofono-binder-plugin` reads `binder.conf` and `binder.d/`. Harmless, but
misleading to anyone reading the device's configuration.

### What the wider community reports about BSNL VoLTE

The porters archive has nothing, but the Android modding and carrier forums have a great deal,
and it corroborates the diagnosis rather than complicating it:

- The standard remedy for "VoLTE works on stock, not on my custom ROM" is exactly this: obtain
  an `mcfg_sw.mbn` for the carrier and load it, either by dropping it into
  `/data/vendor/radio/modem_config` or by selecting it with Qualcomm's PDC tool via the
  `*#*#663368378#*#*` modem-config activity. The mechanism we arrived at from first principles
  is the one the community has been using for years.
- For India specifically, the configs people pass around are Airtel, Vodafone/Idea and Jio —
  `APAC/vodafone/commercial/india/mcfg_sw.mbn` is the usual recommendation. **No BSNL config
  circulates**, which is consistent with BSNL having had no VoLTE to configure until 2024–25.
- BSNL VoLTE fails on plenty of *stock* phones too — recurring reports against several Samsung
  Galaxy models (S22 Ultra, S23, A23, M30s, M35) where the same SIM does VoLTE on Moto, Vivo and
  Realme handsets. That pattern is a per-OEM carrier-config problem, not a network outage, and
  it is the same class of bug as ours.
- BSNL requires VoLTE to be provisioned per subscriber: SMS `ACTVOLTE` to 53733, or ask at a
  BSNL office. Worth doing before drawing conclusions from any modem-side experiment.
  (Done here, and shown to be irrelevant: the SIM already does VoLTE in another handset.)

A note on carrier-side device whitelisting, since it is the obvious thing to suspect and
several Indian operators have done it: it does not fit this evidence. A network that
refuses a handset still lets the modem **attempt** registration and answers with a
rejection. This modem reports `error_code 0`, never asks for an IMS PDN of its own, and
never emits an attempt at all. Nothing on the network side can suppress an attempt that is
never made, so the block is local. Cheap to ask the operator anyway; not a hypothesis worth
spending effort on.

Public MBN corpora exist (`JohnBel/QualcommMBNs`) but are organised by donor handset and carry
no MCC 404 profile. Tooling: `sbaresearch/mbn-mcfg-tools` (parse/pack/verify),
`JohnBel/EfsTools` (modem EFS access), `Biktorgj/mcfg_tools`, and `msm8916-mainline/qtestsign`
for test-key ELF signing if a device ever does check signatures.

### Operational notes for anyone continuing this

- `init.qcom.sh` runs `rm -rf /data/vendor/radio/modem_config` on **every boot** before its
  failing copy, so any staging is destroyed at reboot. A permanent fix needs a boot-time unit
  ordered before `rild`, not a one-off copy.
- qcril skips reloading when `persist.vendor.radio.sw_mbn_loaded` is 1 — set it to 0 to force
  re-evaluation, then restart `rild` (`setprop ctl.restart ril-daemon`).
- `persist.vendor.radio.sw_mbn_volte` and `persist.vendor.radio.sw_mbn_openmkt` must both be
  `1` or no config is ever selected. They ship empty.
- The first reload after an `rild` restart always cancels itself with "sw mbn init need to
  cancel due to iccid_0 change" — the SIM-info cache is cold. Run it twice, or ignore the
  first pass; only the second one gets past `load_to_db`.
- A config edit that does not bump the MCFG version is ignored: qcril compares versions
  against the active config and skips the reload. This fails silently.
- Do **not** delete `/data/vendor/radio/qcril.db` to force a reload; it is the wrong lever and
  it breaks the load with `db add sw mbn file failed` until the database is restored.
- Repeated `rild` restarts leave `ofono` dead (`systemctl restart ofono` recovers it), so check
  it before concluding anything about telephony.

This is the same class of problem as the one success in the porters archive:
Mister_Magister's OnePlus 6T got VoLTE only after flashing a carrier MBN so that the *modem*
enabled IMS (2025-09-01). Nothing on the Sailfish side can substitute for it.

### This is packaged now

`droid-config-karatep` ships `karatep-modem-config.service` and
`/usr/bin/droid/karatep-modem-config.py`, so the carrier-config work survives a flash
rather than living in one device's `/data`. Verified present in the built RPM, including
the `multi-user.target.wants` symlink.

Design points worth keeping:

- **No proprietary bytes are in the repo.** The configs are read from the device's own
  `/vendor/firmware_mnt/image` at runtime and `row.mbn` is patched in place, matching this
  port's existing convention of using the device's vendor partition rather than
  redistributing it. The patcher parses the ELF instead of using fixed offsets, refuses to
  write if anything is unexpected, and was checked to produce a file **byte-identical** to
  the one `mbn-mcfg-tools` builds.
- **Only the verified change is shipped** — `IMS_enable` and `voice_domain_pref`, the two
  that demonstrably bring IMS QMI services up. The 46-item import and the data profile are
  deliberately left out: their effect was measured through a HAL return code that has since
  been shown to be meaningless.
- **Software configs are identified by their MCFG payload, not by filename.**
  `/vendor/firmware_mnt/image` also holds `mba.mbn`, which is the modem boot authenticator
  and not an MCFG at all, and `mcfg_hw.mbn`, which belongs elsewhere. A first draft staged
  both; testing on hardware caught it.
- **The unit waits for `ro.vendor.ril.mbn_copy_completed`** rather than trusting ordering
  against `droid-hal-init`, which is `Type=simple` and reports started at fork while
  `init.qcom.sh`'s `rm -rf` runs asynchronously afterwards — the same trap
  `droid-fm-up.service` documents.
- **Once per install, not once per boot.** The modem keeps an activated configuration
  across reboots, so a marker file short-circuits later boots and the single `rild` restart
  is paid once. If `/data` is wiped the marker goes with it and it runs again.

One caveat for this particular handset: the packaged config bumps the vendor file's MCFG
minor from 50 to 51, while the test device's modem is still carrying the experimental v58
build. qcril skips a config whose version is not newer, so on *this* device the packaged
config will not be loaded until the modem is given something above 58. A freshly flashed
device has no such history and takes 51 normally.

### Device state

The test device carries these changes, which survive reboot and are **not** in any package:

- `persist.vendor.radio.sw_mbn_update=1`, `sw_mbn_volte=1`, `sw_mbn_openmkt=1`
- `/data/vendor/radio/mbn-master/` — the eight `.mbn` files, with `row.mbn` replaced by the
  IMS-enabled build (MCFG minor 51). `init.qcom.sh` does not touch this directory.
- The modem itself has that config **loaded, selected and activated**, and keeps it across
  reboots independently of the files on disk.

They are safe to keep — they repair a vendor script that cannot work as written — but a
freshly flashed device will not have them, and `/data/vendor/radio/modem_config/mcfg_sw/`
is empty after every boot by design.

`voice_domain_pref` is now `ImsPsVoicePreferred` rather than `CsVoiceOnly`. CS remains the
fallback, so calls should behave exactly as before, but this is the one change with any
chance of affecting ordinary calling and it has not yet been exercised with a real call.

One operational note: reinstalling the plugin RPM **restarts** ofono. On one occasion the old
process took a SEGV during that restart instead of exiting cleanly; it was not reproducible on
a repeat, does not occur in normal operation or during IMS registration, and ofono comes back
either way.

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
