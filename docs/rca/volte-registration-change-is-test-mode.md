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

A caveat was recorded here that the test device's modem was still carrying the experimental
v58 build, so the packaged config — which bumps the vendor file's MCFG minor from 50 to 51 —
would be skipped on this handset until it was given something above 58. **That was wrong,
and the modem says so directly.** Read out of NV over diag:

```
/nv/item_files/mcfg/mcfg_sw_muxd_version_8   33 08 03 05   = (51, 8, 3, 5)
```

which is `row_ims_v51.mbn` exactly. The packaged config is what the modem is actually
running; the experimental v58 build is not active and never became active. Everything else
in NV agrees: `ims/IMS_enable = 1` and `mmode/voice_domain_pref = 3` are set, on both
subscriptions, and none of the items that only ever existed in the higher-numbered
experimental builds are present.

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
- **Not the 2019 QCN repair.** See below — the question was asked directly and answered by
  reading the modem's own NV.

## Reading the modem's NV directly, over `/dev/diag`

Everything above infers the modem's state from qcril's logging. That inference can now be
replaced with a direct reading, which changes what several earlier claims are worth.

`/dev/diag` is present on this device, `crw-rw-rw-`, with `CONFIG_DIAG_CHAR=y` and the modem
SMD channels up. That is the same channel QPST and QXDM drive over USB, and it can be driven
locally — which avoids changing the USB composition and dropping the RNDIS link. The
transport is defined by our own kernel in `drivers/char/diag`:

| | |
|---|---|
| `write(2)` | `int pkt_type` = `USER_SPACE_DATA_TYPE` (0x20), then an HDLC-framed DIAG packet — `diagchar_write()` |
| `read(2)` | `int data_type`, `int num_entries`, then per entry `int len` + `len` bytes of HDLC — `diagchar_read()` / `diag_md_copy_to_user()` |
| setup | `ioctl(fd, DIAG_IOCTL_SWITCH_LOGGING=7, {u32 req_mode=MEMORY_DEVICE_MODE, u32 peripheral_mask=DIAG_CON_ALL, u8 mode_param})` — `diag_switch_logging()` rejects an empty mask outright, and without the switch no response ever reaches the char device |

`scripts/diag/` implements this: `diagefs.py` (transport + EFS2), `diagwalk.py` (recursive
walk with timestamps), `diagnv.py` (legacy `NV_READ_F`). The EFS2 directory record layout was
confirmed against real responses from this modem — `0 dirp, 4 seq, 8 errno, 12 entry_type,
16 mode, 20 size, 24 atime, 28 mtime, 32 ctime, 36 name`, with seq 0 a null pseudo-entry and
an all-zero record terminating the directory.

### What the modem actually holds

- `ims/IMS_enable = 01` and `mmode/voice_domain_pref = 03`, on **both** subscriptions. The
  MBN patch is genuinely live in NV, not merely believed to be.
- `mcfg/mcfg_sw_muxd_version_8 = (51, 8, 3, 5)` — the packaged config, as above.
- `ims/qp_ims_plani_config` carries ASCII `404` and `80`: the modem has BSNL's PLMN.
- **`/nv/item_files/ims` contains eight files, and not one of them is an IMS profile item.**
  No `qipcall_config_items`, no `qp_ims_reg_config`, no `qp_ims_sip_extended_0_config`, no
  `qp_ims_param_config`, `qp_ims_voip_config` or `qp_ims_media_config`. The modem has IMS
  switched on and no IMS configuration to run it with.

The four items in the stock `row.mbn` *did* materialise into EFS, so MCFG activation does
write here; the absence of the rest is real, not an artefact of where MCFG stores things.
`rjil.mbn` — the config that made VoLTE work on this handset on stock — supplies 46 of them.

### The 2019 QCN restore is visible, and is not the cause

The user rebuilt this modem's EFS in 2019 after a bad flash zeroed both IMEIs, hand-entering
the IMEIs and later doing something further to restore VoLTE on Jio. That history is still
live: `rawprogram0.xml` in the Lenovo QPST package gives `modemst1`, `modemst2`, `fsg` and
`fsc` all `filename=""`, so no firmware flash has ever reset them.

It is also plainly visible. Timestamps separate the two writers cleanly — MCFG-written items
carry no mtime, QCN-restored ones carry theirs — and **436 files under `/nv/item_files` are
stamped 2019-11-22**, against 533 with none. That is a wholesale restore, matching the
account exactly.

It is nevertheless not implicated:

- **The IMEI is well-formed.** `NV_UE_IMEI_I` (550) reads back as a valid 15-digit IMEI with
  a correct Luhn check digit and a plausible TAC. This mattered because IMS registration
  carries the IMEI in the SIP instance-id, and a malformed one is a real cause of
  network-side rejection. It is not malformed.
- **The restore left no config-selection residue.** `mcfg_setting` and
  `mcfg_setting_Subscription01` are stamped 2019-11-22, but their contents are byte-identical
  to `mcfg_setting_Subscription02`, which the restore never touched. Whatever was done in
  2019 set those items to the values the modem defaults to anyway.
- **`mcfg_autoselect_by_uim = 0`** on all three subscriptions, which is normal for a target
  where qcril drives PDC selection, not evidence of a forced-config hack.
- And the strongest argument is the user's own history: VoLTE worked on Jio on this handset
  *after* the repair, so the modem's IMS stack registered successfully in exactly this NV
  state.

### The modem never puts a REGISTER on the wire

With the config gap closed — all 46 IMS profile items present in NV on both
subscriptions, `RegOnMode = PowerOn`, LTE attached to BSNL, and the IMS bearer up
(`rmnet_data1`, a real address from BSNL's `ims` APN) — IMS still does not register.
qcril reports `IMS registered valid 1, Status 0` throughout.

qcril's logging cannot say why: it only relays what IMSA reports, which is "not
registered" whether the modem was refused or never tried. Those two have completely
different fixes, so the question had to be settled directly. `scripts/diag/diagsip.py`
enables the modem's own log masks and scans the raw frames for SIP, which Qualcomm
carries as plain text rather than a QSR hash — so it can be found without a message
database.

**Nothing. Idle, and during a registration attempt, the modem emits no SIP at all.**

The negative is sound rather than an artefact of looking in the wrong place:

- Every equipment id 0–15 was enabled, i.e. the whole log mask, not a guessed code.
- The modem emits **65 distinct log codes** over the window, including the IMS/data
  range — `0x1544`, `0x158c`, `0x15bd`.
- Text-bearing frames do arrive (`0x1486`), so the scan is capable of seeing text.
- `imsdatadaemon`, `imsqmidaemon` and `ims_rtp_daemon` log **nothing** across a
  registration attempt: the modem is not asking them for anything either.

So the fault is entirely upstream of the network. Nothing is being refused, because
nothing is being sent — which independently closes the carrier-whitelisting question
that was raised earlier, and closes it from the handset side rather than by argument.

`client_prov_enabled` is also off the table. A capture of
`qcril_qmi_imss_get_client_provisioning_config` returns only `wifi_call status` and
`wifi_call_preference` — this modem's provisioning response carries no `enable_volte`
TLV at all, so the flag that looked like the blocker was never one.

### The modem's IMS Settings service refuses to enable IMS

Talking to QMI IMS Settings (0x12) directly over the MSM IPC router removes the
vendor HAL, qcril and ofono from the path entirely (`scripts/qmi/qmiims.py`).
What it finds moves the fault decisively into the modem firmware.

**A correction first.** It was recorded here that the vendor HAL "accepts
setServiceStatus and drops it", on the strength of a log capture that showed no
qcril response. That was a bad filter, not a finding: the capture searched for
`service_status`, and qcril's function is `qcril_qmi_imss_request_set_ims_srv_
status_v02` -- `srv_status`. The call is handled, and qcril sends QMI message
0x8f for it. The HAL is innocent.

**What actually happens** is that the modem rejects it. Sweeping the imss
message space separates three cases cleanly, because QMI distinguishes them:

| result | meaning | messages |
|---|---|---|
| `QMI_ERR_INVALID_MESSAGE_ID` (57) | not implemented in this firmware | most of 0x01-0x1d, and ~40 others |
| `QMI_ERR_MISSING_ARG` (17) | implemented, wants TLVs | 0x1f, 0x66, 0x89 |
| `QMI_ERR_INTERNAL` (3) | implemented, fails inside the modem | **0x8f, 0x90**, 0x1f with a valid TLV, and ~30 more |

`0x8f` and `0x90` are set/get of the IMS service enable config. They are *not*
unimplemented -- an unimplemented id answers 57 -- they exist and fail
internally, on every TLV tag and width tried. That is the only mechanism that
turns the modem's IMS services on, which is why `imsa` reports every service
unavailable and why no SIP is ever sent.

### Client provisioning does work, and the earlier reading of it was wrong

Two things previously recorded about `client_prov_enabled` were mistaken.

It was written that the modem "carries no `enable_volte` TLV at all, only
`wifi_call`". It does -- response TLV 0x11 -- qcril simply does not log it on
the path that was captured.

And its refusal to move was not the modem refusing. **Request TLVs are offset
by one from response TLVs**, which showed up by accident: writing tag 0x18 came
back as response field 0x19. So `enable_volte` is written as 0x10 and read as
0x11, and every earlier attempt had written 0x11 -- which is `enable_vt`. With
the correct tag it takes immediately and `enable_volte` now reads 1.

It changes nothing. IMS still does not register and the modem still sends no
SIP, so client provisioning was never the gate either -- but the value is now
right, and the mechanism is understood rather than assumed.

### The donor is carrier-neutral

Jio's `rjil.mbn` is the only same-generation donor available: the Nokia 6 generic ROW
config, from a newer LA.3.1.2 branch on the same SoC, uses an entirely different IMS
item generation — `RegistrationConfiguration`, `IMSVoiceDynamicConfig`,
`qp_ims_service_enablement_config` — with no name in common. That confirms at item
granularity what was previously concluded for whole configs.

Importing from a carrier config raises the obvious worry that Jio-specific parameters
come with it. They do not. Of the 46 items imported, the only ones carrying any
carrier-specific string are `qp_ims_ut_config` (`jionet`, XCAP ports) and
`qp_ims_sms_config` — and both were already excluded from the list. What is imported is
codec lists, timers, feature flags and generic 3GPP URNs.

### No prior art

`bin/ircgrep.sh` returns **zero hits** across eleven years of `#sailfishos-porters` for
`ps_sys_data_configurations`, `mcfg_sw`, `EFS2`, `qp_ims_reg_config` and
`client_prov_enabled`. The archive knows `/dev/diag` only as a permissions problem. No
porter has taken this route before, so there is no prior art to follow and none of this
should be expected to match someone else's notes.

Backups of `modemst1`, `modemst2`, `fsg` and `fsc` were taken before any of this
(`dd` from `/dev/mmcblk0p29`, `p30`, `p32`, `p18`) and are restorable the same way. `fsg` is
a signed `IMGEFS` blob and `modemst1`/`modemst2` are opaque on disk — neither can be read
without the modem, which is why the diag route was needed at all.

### The modem cannot be asked why, because it has no debug messaging

`imss` message `0x8f`/`0x90` failing with `QMI_ERR_INTERNAL` rather than
`QMI_ERR_INVALID_MESSAGE_ID` says the handler exists and fails inside itself. Qualcomm
firmware normally explains exactly that in its F3 traces — `MSG_HIGH`/`MSG_ERROR` lines
carrying the printf format string plus the source file and line — delivered over
`/dev/diag` as packet `0x79`, or as a hash (`0x92`/`0x93`) on builds using QSR.

Getting the runtime masks up took three corrections, all of them properties of this
kernel's diag driver rather than of the protocol:

- `diagchar_read()` waits in `wait_event_interruptible()` and ignores `O_NONBLOCK`, while
  `diagchar_poll()` reports the device readable whenever the driver has been woken rather
  than only when a batch is queued. So `select()` promises data that `read()` then blocks
  on, forever. The only way to put a deadline on it is a repeating `SIGALRM` whose handler
  raises — `diagefs.interruptible()`.
- The read buffer must be large enough for a whole batch. `COPY_USER_SPACE_OR_EXIT()` in
  `diagchar_core.c` fails the read with `-EFAULT` the moment `count` cannot take the next
  frame, rather than returning what fits, so a buffer that is merely generous still loses
  data under load. 1 MB is enough here; 256 KB is not.
- In `DIAG_EXT_MSG_CONFIG_F` (0x7D) sub-commands 2 and 3 the SSID range comes **before**
  the status word, per `struct diag_msg_build_mask_t` in `drivers/char/diag/diag_masks.h`:

  ```
  [0x7D][sub u8][ssid_first u16][ssid_last u16][status u8][pad u8][ rt_mask u32 * n ]
  ```

  Getting that wrong is silent: asking for 6000..6003 with the range one field too late
  comes back describing 0..129, because the firmware reads the zero padding as the range.
  Sub-command 1 (get SSID ranges) has no range and puts its status straight after the
  sub-command. This modem declares 26 ranges, 0..129 through 49152..49251.

With every one of those 26 ranges raised to all levels **and** every log mask raised, a
five-second capture returns **1511 log packets and zero F3 messages** — none plain, none
QSR-hashed. The log packets prove the capture path; the modem simply has messaging
compiled out. `MSG_MASK_TBL_CNT` masks read back as non-zero defaults, so this is not a
mask that failed to apply. There is no way to make this firmware say why `0x8f` fails.

Raising all masks at once is also not free: the first (mis-framed) `SET_ALL_RT_MASK`
pushed load average past 5 and wedged the diag channel until a reboot.

### The 2019 provisioning is not it — the whole class is eliminated

Two independent checks close this off.

`/data/ps_sys_data_configurations.txt` looked promising: dated 2019-11-22, empty for
subscription 0 and reading `2:2,0,Jionet,1,ims,; 3:1;` for subscription 1, exactly the
shape of "Jio got an IMS APN configured and BSNL did not". It is a red herring. The
string `ps_sys_data_configurations` does not appear anywhere in the running modem image,
while `/Data_Profiles/Profile%u%1s` does — the file is a leftover from the stock Android 7
firmware and this build never reads it.

More conclusively, `scripts/diag/subdiff.py` compares every per-subscription item in EFS —
the bare name against its `_Subscription01` twin. Of **71 pairs under `/nv`, exactly one
differs**, `/nv/item_files/ims/qp_ims_config`, and the difference is ours: subscription 1
still holds `rjil.mbn`'s value byte for byte, subscription 0 holds the same value with two
bytes raised by our own QMI client-provisioning writes. There is no hidden per-SIM state
that the Jio subscription received and the BSNL one did not.

### How a config declares which SIMs it is for

Every `mcfg_sw` config ends with an `MCFG_TRL` record holding TLVs of
`[tag u8][len u16][value]`:

| Tag | Meaning |
|---|---|
| 1, 5 | the MCFG version, `[minor, carrier, oem, family]` |
| 3 | the config's name |
| 4 | `[flag u8][count u8]` then `count` × `u32`, each an ICCID/IIN prefix as a decimal |
| 6 | `[flag u8][count u8]` then `count` × (MCC `u16`, MNC `u16`) |

Decoded across four files on this device:

| File | Name | Records | IMS items | Matches |
|---|---|---|---|---|
| `row.mbn` | ROW_Generic_3GPP | 6 | 1 | PLMN list **empty** |
| `rjil.mbn` | Commercial-Reliance | 97 | 49 | 405/840, 405/854…874; IIN 8991840…8991874 |
| `3uk.mbn` | UK3G_GBR | 116 | 51 | 234/20, 235/94; IIN 894420 |
| `mcfg_sw.mbn` | W-One_Th_Bringup | 133 | — | 310/480; IIN 8901000, 8900310, 8901001 |

The empty PLMN list is what makes ROW_Generic the config every unmatched SIM falls
through to. It also explains the device's history directly: **Jio VoLTE worked here
because Jio has a dedicated 97-record config on this handset.** BSNL has never had
anything but the 6-record generic one.

### A complete commercial VoLTE config changes nothing

That made the obvious experiment worth running: point a real, complete, known-good VoLTE
config at the BSNL SIM and see whether IMS comes up. `scripts/mcfg/retarget.py` rewrites
tag 4 and tag 6 **in place** — overwriting the first entry of each rather than appending —
so every length in the file is unchanged and only the three SHA-256 hashes need redoing.
`3uk.mbn` was retargeted from 234/20 to 404/80 and from IIN 894420 to 899180, with its
MCFG minor bumped 5 → 6 so qcril would not skip the load.

It was selected and applied, exactly as intended:

```
qcril_qmi_pdc_get_selected_config_ind_hdlr:
    Store active config for SUB0 as /data/vendor/radio/modem_config/mcfg_sw/3uk.mbn
qcril_qmi_pdc_get_selected_config_ind_hdlr:
    Store active config for SUB1 as /data/vendor/radio/modem_config/mcfg_sw/row.mbn
qcril_mbn_sw_activate_config_hndlr: result: 0
```

and it really did reach the modem — `/nv/item_files/ims/qp_ims_config` read back as
`000002000000000203…`, byte-identical to the value carried in `3uk.mbn`'s own record, and
it survived a reboot.

The result, both immediately and after a clean reboot with the config still applied and
the handset registered on LTE as "BSNL Mobile":

- `imsa` `query_ims_srv_status` — every service TLV zero, as before;
- `imss` `get_ims_service_enable_config` (`0x90`) — `result=1 error=3`, `QMI_ERR_INTERNAL`,
  as before;
- ofono — `Registered: false`, `VoiceCapable: false`, as before.

**The carrier configuration is not the gate.** A genuine commercial VoLTE config, with 51
IMS items against ROW_Generic's one, correctly selected for this SIM and activated by the
modem, produces exactly the same refusal.

Taken with the earlier import, that closes the class rather than just this instance. Jio's
46 IMS items were written into NV directly and changed nothing; Three UK's 51 were
delivered the sanctioned way, by an activated config, and changed nothing. The two donors
share 44 items, so there is no third arrangement of the same values left to try.

Backing the experiment out takes one more step than putting the stock `3uk.mbn` back.
qcril keeps its selection tables in an SQLite database at `/data/vendor/radio/qcril.db`:

```
CREATE TABLE qcril_sw_mbn_mcc_mnc_table
    (FILE TEXT, MCC TEXT, MNC TEXT, VOLTE_INFO TEXT, MKT_INFO TEXT, LAB_INFO TEXT,
     PRIMARY KEY(FILE, MCC, MNC))
```

and the row it wrote while the retargeted file was staged —
`…/3uk.mbn | 404 | 80 | VOLTE | OPENMKT | COMMERC` — outlives both a restage and a rild
restart, so it keeps selecting the UK config for the BSNL SIM from a file that no longer
claims it.

What rebuilds those three `qcril_sw_mbn_*` tables is a **boot**. `/vendor/bin/init.qcom.sh`
copies the vendor's pristine configs to `/data/vendor/radio/mbn-master` and drops a
`copy_complete` marker beside it, both only at boot, and qcril rebuilds its selection tables
from that master set. So the recovery is simply: reboot, then restage.

Deleting or replacing the database is not the answer, and doing either leaves the phone
worse off in a different way each time.

Deleting it: **qcril never creates the emergency schema, and on this port nothing copies it
in.** Vendor ships the populated file at `/vendor/radio/qcril_database/qcril.db`
(98304 bytes), but with `qcril.db` missing rild simply makes an empty 4096-byte SQLite file
and then logs `Operation failed 1 no such table: qcril_emergency_source_mcc_mnc_table` on
every lookup — 39 of them in one boot. The emergency-number tables are gone with it, which
matters here given the unrelated `EF_ECC` parse failure.

Copying that vendor template over the live database — which is what I did next, on every
restore attempt — is the subtler mistake, and it is what kept the restore failing. The
template carries the emergency and operator schema but **none** of the `qcril_sw_mbn_*`
tables, so each copy threw away exactly the selection tables qcril had just rebuilt during
boot, and the following rild restart then had nothing to select from. Confirmed by
inspecting a database after a clean boot with the copy step removed: all three tables
present, `no such table` count 0, and subscription 0 selected `mcfg_sw/row.mbn` on the
first try. `scripts/mcfg/restore-row.sh` no longer touches the database at all.

Two dead ends recorded so no one re-runs them: clearing `/data/vendor/radio/copy_complete`
does not force the database to be rebuilt (that marker belongs to the `mbn-master` copy),
and `ctl.restart ril-daemon` versus `ctl.stop` + `ctl.start` makes no difference either —
both were theories of mine that the evidence did not support.

Worth knowing before editing any config's match list: the staged file is not the only
place the mapping lives.

ofono's APNs are provisioned by ofono, not by the MCFG, so `bsnlnet` and the MMS context
were unaffected throughout.

## The modem's IMS settings are correct; its IMS runtime is not there

With the carrier-config class eliminated, the last question worth asking the modem directly
was whether its IMS task is running at all. `scripts/qmi/qmiims.py sweep 0x20 0xa0` walks
every message id in the `imss` (IMS Settings, service `0x12`, port `0x37`) space with an
empty request and records the result code, which partitions the service cleanly:

| result | ids | reading |
|---|---|---|
| success | 26 | `0x26 0x28 0x29 0x2a 0x34 0x36 0x37 0x39 0x3d 0x3f 0x40 0x41 0x44 0x45 0x48 0x4a 0x4b 0x53 0x54 0x56 0x57 0x58 0x5d 0x5e 0x63 0x64` |
| 3 `INTERNAL` | 34 | includes both `0x8f` (`set_ims_service_enabled`) and `0x90` (`get_ims_service_enable_config`) |
| 17 `MISSING_ARGUMENT` | 2 | `0x66 0x89` |
| 54 `CAUSE_CODE` | 13 | setters, unhappy with an empty request |
| 57 `INVALID_MESSAGE_ID` | 49 | not implemented in this firmware |

The error names are not guesses. `include/uapi/linux/ipa_qmi_service_v01.h` in our own kernel
tree spells out enough of the shared QMI error enum — `INTERNAL` `0x0003`, `INVALID_ID`
`0x0029`, `ENCODING` `0x003A`, `INCOMPATIBLE_STATE` `0x005A`, `NOT_SUPPORTED` `0x005E` — to
pin it against libqmi's table, which agrees on every one of those values; `54` and `57` are
read off that alignment.

That matters for `0x8f`/`0x90` specifically. A message this firmware does not implement comes
back `57 INVALID_MESSAGE_ID`, and 49 of them do. The enable-config pair instead comes back
`3 INTERNAL`, so **their handlers exist, are dispatched to, and fail inside the modem.** Both
the "the modem's IMS task never started" hypothesis and the milder "it does not implement
these messages" are therefore wrong.

What the 26 answers say is that the modem's IMS *settings* are fully and correctly
provisioned for this SIM (`scripts/qmi/imssdump.sh`):

- `0x28` → `ims.mnc080.mcc404.3gppnetwork.org` — the modem has derived BSNL's IMS home
  domain from the IMSI by itself. It knows exactly which network it should register to.
- `0x26` → SIP port 5060.
- `0x29` → the registration timer set: 1800 s expiry, 600 s subscription, T1 45 / T2 90 / TF 20.
- `0x34` → the QoS and media parameters, MTU 1400.
- `0x37` → three enable flags, all `1`.
- `0x48` → the IMS PDN profile, APN `ims`.

Every one of those is a read of stored configuration, and every one is correct. Whatever is
wrong, it is not that the modem lacks provisioning for this SIM — and it will not say what is
wrong, because this is the same firmware that emits no F3 debug messaging at all, so there is
no channel left to ask on.

### The IMS bearer is not the trigger either

The obvious remaining candidate was the PDN: perhaps the modem starts its IMS task only once
the IMS APN is up, which on this port never happens by itself (see the open item about ofono
not activating `context3`). `scripts/qmi/imsbearer.sh` activates it over D-Bus and re-runs
the sweep. The context comes up properly — `rmnet_data2`, address `10.206.179.69` — and the
sweep result is **byte-identical**: same 26 successes, same 34 `INTERNAL`s, same
`0x8f`/`0x90`. The bearer is not the gate, and making ofono bring `context3` up
automatically, while worth doing on its own merits, would not by itself produce VoLTE here.

### Not a missing subscription bind

The one cheap lever left in the QMI layer was that qcril binds its `imss` client to a
subscription before it ever calls `0x8f`/`0x90`, and our raw client never does — a
per-subscription handler asked without one has nothing to look up, which would explain
`INTERNAL` neatly. `scripts/qmi/bindprobe.py` tests it: retry all 34 `INTERNAL` ids with a
subscription selector in TLV `0x01` (as u32, then as u8), then walk the 13 `CAUSE_CODE`
setters looking for one that a selector satisfies, re-asking `0x90` on the same client after
each.

None of it moves. What the retry does instead is settle the question underneath it: with an
unexpected TLV, all 34 move from `3 INTERNAL` to **`58 ENCODING`**. The modem is decoding
those requests against a real IDL and rejecting a field that does not belong — so the
messages are properly declared, the empty request was the correct one, and `INTERNAL` is
coming from inside the handler body. Only `0x66` accepted the selector (a u8 in TLV `0x01`,
which is why an empty request got it `MISSING_ARGUMENT`), and `0x90` stayed `INTERNAL`
afterwards.

The handset was unharmed and still registered on LTE as `BSNL Mobile` after the probe.

### The firmware itself: what it is and what it contains

`scripts/qmi/modemims.sh` reads the modem image directly, which turns out to be worth doing
before drawing conclusions about capability. The build banner:

```
MPSS.JO.2.0.c1-00122-8937_GENNS_PACK-1_20161209_043830
```

A December 2016 MSM8937 modem — the stock Android 6/7-era firmware this device shipped with,
which LineageOS 18.1 keeps because the port runs on the vendor partition.

Its symbol strings carry the whole IMS interface layer — `ims_task.cpp`,
`ims_qmi_settings_service.c`, `ims_qmi_registration_apps_service.c`,
`ims_qmi_presence_service.c`, `ims_qmi_dcm_client.c`, `ims_qmi_imsrtp_client.c`,
`ims_reg_service_status.cpp` — *and* the session layer above it: `qipcallh.c`,
`qipcall_conf_and_transfer_call.c`, `sipClientConnection.cpp`, with live log strings such as
`SipConnection::Start INVITE_TRANS` and `qipcallh_process_incoming_call : PRACK or 100rel not
in supported list rejecting the call`.

I had started writing the opposite conclusion off a first scan that found no `SIP/2.0` and no
`sip:` in `modem.b*`, which would have meant a firmware that cannot speak SIP at all. Widening
the scan to the whole image directory finds `sip:`, and the QIPCALL and SIP-connection strings
are unambiguous. **This modem contains a complete VoLTE stack.** The absent literals are an
artefact of how the stack builds its messages, not evidence of absence — and this is exactly
the firmware that did VoLTE on Jio on this handset under LineageOS, which independently
settles the capability question.

So the `INTERNAL` class is not a firmware that cannot do IMS. The best-supported reading now
is a **vintage mismatch**: `0x8f`/`0x90` are the enable-config API that Android 11's qcril
uses, and on a 2016 modem branch those ids can be declared in the service's message table —
hence a correct decode, hence `ENCODING` on a bad TLV — while the handler behind them is a
stub that returns `INTERNAL`. On this firmware VoLTE would be enabled the older way, through
the 26 messages that do work plus the QIPCALL NV configuration. That is a testable claim, and
it is where the next work goes.

## Resolved: the Jio config, retargeted at BSNL, registers IMS

The section above ends by naming the next experiment as an NV-side one. It was, but not the
one I expected, and it worked on the first attempt.

### Choosing a better donor

The earlier "a complete commercial VoLTE config changes nothing" experiment used `3uk.mbn`
retargeted at 404/80. That was the wrong donor: `UK3G_GBR` has never worked on this handset,
so a null result from it says nothing about whether the mechanism works. `rjil.mbn` —
`Commercial-Reliance` — is the config that demonstrably produced working VoLTE on *this*
handset and *this* modem firmware under LineageOS. It is the only positive control available.

Comparing the two configs' EFS items with `scripts/mcfg/mbnitems.py` also showed the hand-built
`row_v61.mbn` in a new light. Its 50 items are a strict *subset* of Jio's 80. Everything we
wrote, Jio writes too — so the 46-item import was not wrong, it was incomplete, in two ways:

* **One value differs in an item we did copy.** `/nv/item_files/ims/qp_ims_reg_config` byte 0
  is `01` in Jio's and `00` in ours. This item is never mentioned anywhere earlier in this
  document; the difference had not been noticed.
* **Thirty items were never copied at all**, among them `/Data_Profiles/Profile1..3` (Profile2
  is the `ims` APN), `/pdp_profiles/consl_profiles/rmnet_call_prof_num` and
  `socks_call_prof_num`, and the mode-manager domain preferences
  `/nv/item_files/modem/mmode/sms_domain_pref`,
  `/nv/item_files/modem/mmode/supplement_service_domain_pref` and
  `/nv/item_files/modem/mmode/wifi_config`.

### What was staged

```sh
scripts/mcfg/retarget.py rjil.mbn rjil_bsnl.mbn 404 80 8991805
  config name: Commercial-Reliance
  iin  [0]: 8991840 -> 8991805
  plmn [0]: 405/840 -> 404/80
  MCFG version: 061b0205 -> 071b0205
```

`scripts/mcfg/stage-rjil.sh` then stages every software config from
`/vendor/firmware_mnt/image`, substituting `rjil_bsnl.mbn` for `rjil.mbn` — and staging
`row.mbn` **stock**, not `row_v61.mbn`. That matters for attribution: stock ROW sets
`IMS_enable=0`, so if the retarget were not selected, IMS would disappear entirely rather than
be propped up by our patched fallback. There is no way to read the result ambiguously.

It was selected first try, without a reboot:

```
qcril_qmi_pdc_select_config_ind_hdlr: Selected config for SUB0 is .../mcfg_sw/rjil.mbn
qcril_qmi_pdc_select_config_ind_hdlr: Selected config for SUB1 is .../mcfg_sw/row.mbn
qcril_qmi_pdc_activate_config_ind_hdlr: activate successful
```

`no such table` count 0, `sw_mbn_loaded=1`, and `/nv/item_files/mcfg/mcfg_sw_muxd_version_1`
reads `071b0205` — our bumped Jio version. The items landed: `IMS_enable` `01`,
`qp_ims_reg_config` byte 0 now `01`, `sms_domain_pref` `01`,
`supplement_service_domain_pref` `03`, and `qp_ims_ut_config` carrying Jio's `jionet`.

### Twenty-three seconds later, IMS registered

```
10:29:24  qcril_qmi_imsa_get_ims_registration_info: ims_registered: 0
10:29:24  qcril_qmi_imsa_ims_registered_wlan_status: IMS service status valid 0
10:29:47  qcril_qmi_imsa_reg_status_ind_hdlr: ims_registered: 1
10:29:47  qcril_qmi_ims_map_qmi_ims_reg_state_to_ims_reg_state: qmi ims_reg_state 1 -> ims 3
```

ofono's `IpMultimediaSystem.Registered` also reads `true`, but that is worthless as evidence
and was not used: it read `true` throughout the entire failure as well, which is the whole
subject of the ofono all-zero-struct finding earlier in this document. The proof has to come
from the modem.

It does. Asking `imsa` (service `0x21`, port `0x39`) for message `0x20` returns the
registration record, and TLV `0x15` is the **P-Associated-URI list**:

```
tlv 0x15 len 71  |.3sip:+91XXXXXXXXXX@ims.mnc080.mcc404.3gppnetwork.org.tel:+91XXXXXXXXXX|
```

That is the pair of identities BSNL's IMS core hands back in the `200 OK` to a successful
`REGISTER`, and it contains the SIM's own MSISDN in a form the modem has no other way to
learn. Message `0x21` corroborates it with eight service-status fields, `2` (full service) on
both the VoIP and the VT entries, and `qcril_qmi_imsa_is_ims_registered_for_voip_vt_service`
concludes `IMS registered for VOIP or VT service 1`.

Registration is stable: over the whole log buffer, two `ims_registered: 1` indications and
zero `ims_registered: 0`.

**The modem is registered with BSNL's IMS network.** After a fortnight of the modem refusing
to attempt registration at all, it did so within half a minute of being handed a carrier
config that had actually worked on it before.

### What this retires, and what it does not

The `0x8f`/`0x90` enable-config pair still answers `3 INTERNAL`. Nothing ever made those two
messages work, and VoLTE registration happened without them — which confirms the vintage
reading in the sense that matters: **on this firmware, enablement is NV-driven, and the
Android 11 QMI enable API is not the path.** Every attempt to reach VoLTE by finding the right
QMI call to make, or the right HIDL `ConfigItem` to set, was aimed at a door this modem does
not have. The `ext-qti` `setServiceStatus` work, item 26, the ConfigItem hunt — all of it was
looking in the wrong layer.

What is *not* yet known is which of the thirty-one differences did it. `qp_ims_reg_config`
byte 0 is the obvious single-byte suspect, but `/Data_Profiles/Profile1..3` and the mmode
domain preferences are equally plausible, and the honest answer today is that a config known
to work was applied wholesale. Bisecting it matters, because the current state is Jio's config
wearing BSNL's PLMN: it also writes Jio's `qp_ims_ut_config` (`jionet`), Jio's
`qp_ims_sms_config` (SMSC `10138`), Jio's ANDSF policies and a `vowifi.jio.com` ePDG FQDN. A
BSNL port should ship the minimal correct set on top of `ROW_Generic_3GPP`, not a retargeted
Jio config.

SMS appears unaffected so far — ofono still reports BSNL's real service centre and
`Bearer = cs-preferred`, so messaging stays on CS — but `sms_domain_pref = 01` is now set and
that needs watching.

An actual VoLTE call has not been placed yet. Registration is not the same as a working call:
SRVCC, codec negotiation and the CS fallback path are all still untested.

## Correction: the IMPU list was not proof, and the calls proved it

The section above declares the modem registered on the strength of `imsa` message `0x20`,
which returns

```
sip:+919487328324@ims.mnc080.mcc404.3gppnetwork.org
tel:+919487328324
```

and reasons that because `EF_MSISDN` is unprogrammed — ofono's `SubscriberNumbers` is an empty
array — the modem cannot know its own number, so the number must have come back in the
`P-Associated-URI` of a `200 OK`. The number is correct for this SIM.

That inference is wrong, and a call settled it: both an incoming and an outgoing call went out
over CS, with **zero** `qipcall|ims_call|imsvt` matches anywhere in the radio log.

The hole in the argument is the card. `QMI_UIM_GET_CARD_STATUS` on service `0x0b` (node 0,
port 0x29) reports:

```
slot 1: app 0 type=2 (USIM)  state=7 (READY)     aid=a0000000871002ff49ffff89081500ff
        app 1 type=5 (ISIM)  state=1 (DETECTED)  aid=a0000000871004ff49ffff89081500ff
slot 2: app 0 type=2 (USIM)  state=7 (READY)     aid=a0000000871002ff49ffff89030900ff
```

**There is an ISIM**, and `EF_IMPU` on an ISIM holds exactly that sip:/tel: pair. The modem has
a local source for the number and needs no network to report it. (The ISIM being `DETECTED`
rather than `READY` argues it was never provisioned, which is suggestive but not conclusive —
a non-provisioning session can read the file.) A second test was also inconclusive: the IMPU
survived `Modem.SetProperty Online false`, but the modem was never confirmed to have detached.

What `0x20` and `0x21` report is capability, not a registration binding. qcril's own reading
was there to be checked and said so plainly: `ims_registered: 0`, alongside

```
qcril_qmi_voice_technology_updated: voice_rte 5, data_rte 5, ims_rte 0, will be considered 5
```

`ims_rte 0` is the value that decides the voice domain, and it is why every call went CS
regardless of what the config layer reported. The network was never the obstacle —
`voice_support_on_lte val 1` says BSNL advertises VoPS on this cell.

## The port had been calling the wrong generation of the API all along

`libril-qc-qmi-1.so` keeps a table of radio-config items, laid out as
`{get_msg u64, set_msg u64, const char *name, item_id u64}`, and it appears **twice** — once
per generation of the QMI IMS Settings API. `scripts/qmi/cfgmap.py` walks both (the host
`objdump` cannot disassemble aarch64 here despite `readelf` reading the header, so
`scripts/qmi/disasm.py` uses capstone):

| item | name | legacy pair | modern pair |
|---|---|---|---|
| 24 | `CLIENT_PROVISIONING_ENABLE_VOLTE` | get `0x54` / set `0x53` | get `0x90` / set `0x8f` |
| 25 | `CLIENT_PROVISIONING_ENABLE_VT` | get `0x54` / set `0x53` | get `0x90` / set `0x8f` |
| 45 | `QIPCALL_MOBILE_DATA_ENABLED` | get `0x37` / set `0x36` | — |
| 46 | `QIPCALL_VOLTE_ENABLED` | get `0x37` / set `0x36` | get `0x90` / set `0x8f` |
| 47 | `QIPCALL_VT_CALLING_ENABLED` | get `0x37` / set `0x36` | get `0x90` / set `0x8f` |

This is the vintage mismatch, in the binary rather than as a theory. `0x8f`/`0x90` answer
`3 INTERNAL` because they are the *later* pair; `0x36`/`0x37` and `0x53`/`0x54` carry the same
items and are among the 26 messages this modem has answered correctly all along. Every hunt for
"the QMI call that enables VoLTE" was aimed at a door this firmware does not have, while the
door it does have was in the working set the whole time.

Reading both legacy getters split the problem in one shot:

```
0x37 -> 0x11=1 0x12=1 0x13=1        mobile_data=1 volte=1 vt=1
0x54 -> 0x11=0 0x12=0 0x13=0 ...    volte=0 vt=0 presence=0 wifi_call=0
```

The qipcall layer was already enabled. **Client provisioning was not** — item 24, the flag
Android's telephony framework writes when the user turns the VoLTE switch on, and which a
handset that does not recognise the carrier never writes at all. On BSNL, whose 4G postdates
most shipped carrier configs, the switch is simply never offered.

`scripts/qmi/setprovvolte.py` writes it. The set request's TLV tag is not in the table, so it
tries the plausible ones and lets the read-back decide; **tag `0x10`, one byte** is the one the
modem takes:

```
before:  volte=0 vt=0 presence=0 wifi_call=0 wifi_roam=1 wifi_pref=1
set tag 0x11/1B -> result=(0, 0)     accepted, changed nothing
set tag 0x11/4B -> result=(1, 1)     rejected
set tag 0x10/1B -> result=(0, 0)     accepted
after:   volte=1 ...
```

## What that produced, and what is still wrong

The modem's IMS state machine came alive. It now emits real registration-status *indications* —
pushed by the modem, not read back from config — over LTE:

```
qcril_qmi_imsa_reg_status_ind_hdlr: ims_registered: 1   ims_registration_network: 14
qcril_qmi_imsa_reg_status_ind_hdlr: ims_registered: 0   ims_registration_network: 14
qcril_qmi_imsa_reg_status_ind_hdlr: ims_registered: 1   ims_registration_network: 14
qcril_qmi_imsa_reg_status_ind_hdlr: ims_registered: 2   ims_registration_network: 14
```

and `VOIP service STATUS 2` appears for the first time. This needs the IMS bearer up: with
ofono's `context3` inactive nothing happens, and with it active (`rmnet_data0`,
`10.137.251.97/30`) the indications start. That reverses the earlier finding that activating the
IMS PDN changes nothing — it was true under `row_v61`, before the modem had a config it would
accept, and it is not true now. Task #22 is therefore load-bearing, not cosmetic.

It does not settle. The registration oscillates `1 → 0 → 1 → 2` rather than holding, `ims_rte`
stays `0`, `voice_radio_tech` stays `1`, and calls still go CS. So the sequence is understood
and the last gate is not yet open:

1. carrier config the modem accepts — **done**, via the retargeted Jio config;
2. IMS bearer up — **done**, by activating `context3` by hand;
3. client provisioning VoLTE enabled — **done**, via legacy `0x53` tag `0x10`;
4. registration holding, `ims_rte` = 14, voice over IMS — **not yet**.

Why it drops is the open question. Candidates, in the order worth testing: the bearer is
being brought up by ofono rather than by the modem's own DCM client, so its lifetime does not
match what the IMS stack expects; `CLIENT_PROVISIONING_ENABLE_PRESENCE` and the rest of item
24's neighbours are still 0; and the registration may simply be failing at the core and
retrying, which nothing on the AP side can see because this firmware emits no F3 messaging.

## Correction again: the modem never reached REGISTERED, and now we know why

The section above reads the modem's progression `qmi 0 -> 1 -> 2` as
NOT_REGISTERED -> REGISTERING -> REGISTERED, decoding the enum from
`qcril_qmi_ims_map_qmi_ims_reg_state_to_ims_reg_state`:

```
w0 == 0 -> 2      w0 == 1 -> 3      w0 == 2 -> 1      default -> 2
```

on the argument that a mapper's `default` falls to the safe value, so `2` must be
NOT_REGISTERED. **That argument is wrong**, and the mapper alone could never have
settled it — note that it cannot emit `0` for any input at all, which should have
been the warning.

ofono's own debug settles it without any disassembly. Turning on `OFONO_DEBUG=-d *`
makes `ofono-binder-plugin-ext-qti` print the `QtiRadioRegInfo` struct it reads
straight off the HIDL, and the registration runs this cycle, over and over:

```
ims:imsradio0: QtiRadioRegInfo state:2 radiotech:15 error_code:2147483647
ims:imsradio0: QtiRadioRegInfo state:1 radiotech:15 error_code:408
ims:imsradio0: QtiRadioRegInfo state:1 radiotech:15 error_code:0
```

`state` is `QTI_RADIO_REG_STATE` from `qti_radio_ext_types.h`, where
`REGISTERED = 0, NOT_REGISTERED = 1, REGISTERING = 2`. State 2 carries
`error_code 0x7fffffff` — the no-error sentinel — and state 1 carries **408**.
So the correlation reads directly: **REGISTERING, then NOT_REGISTERED with SIP
408.** State 0 never appears.

Back-substituting through the mapper, `qmi 2 -> 1` means **qmi state 2 is
NOT_REGISTERED**, not REGISTERED, and qcril's `default -> 2` is REGISTERING. The
`0 -> 1 -> 2` progression was an attempt that failed, which is exactly consistent
with the 408s and with both calls going CS.

### What 408 is worth

**SIP 408 is Request Timeout.** The modem built a REGISTER, sent it, and nothing
came back. That retires the oldest standing claim in this document — that no SIP
ever leaves the modem, that it is idle rather than refused. It is not idle any
more. Everything up to and including the transaction layer now works:

* the carrier config is accepted and selected (retargeted `rjil.mbn`);
* `CLIENT_PROVISIONING_ENABLE_VOLTE` is set, through the legacy `0x53` tag `0x10`;
* the IMS PDN comes up and the registration manager runs;
* a REGISTER goes out over LTE.

What fails is the round trip. The suspects, in the order the evidence points:

**The P-CSCF is not consistently delivered.** One activation logged
`qcril_data_util_fill_pcscf_addr: PCSCF Addresses : 61.2.220.137` — a real BSNL
address — and later activations logged no `fill_pcscf_addr` line at all. A
REGISTER with no P-CSCF, or one aimed at a stale address, times out exactly like
this.

**The IMS PDN is IPv4-only in practice.** ofono already requests both — context3's
`Protocol` is `dual`, and `ipv4v6` is not even valid ofono spelling — but what
comes back is an IPv4 address (`10.66.37.208/27`) and nothing but a link-local
IPv6. Indian IMS cores are normally reached over IPv6, and the P-CSCF in PCO is
usually an IPv6 address.

**The bearer belongs to ofono, not to the modem.** On a stock Qualcomm stack the
IMS PDN is brought up by the modem's own DCM client (`ims_qmi_dcm_client.c` is in
this firmware) and SIP terminates inside the modem. Here ofono establishes it with
`setupDataCall` and the AP gets the address. Whether the modem's IMS stack binds
to a PDN owned that way is unverified, and the intermittent P-CSCF may be a
symptom of it.

Worth noting what is *not* implicated: the AP's routing table shows
`61.2.220.137 via 192.168.68.1 dev wlan0`, which looks alarming but is irrelevant —
modem-originated SIP never traverses the AP network stack.

## The call goes CS anyway, and three long-standing claims turn out to be wrong

ofono now dials over IMS. With registration at `state:0` its debug shows the ext
path being taken, the HAL accepting, and the call progressing:

```
ims:Dialing (ext) +91XXXXXXXXXX
imsradio0< [00000014] 2 dial
ims:qti_ims_call_result_response 0
status: dialing (2) -> alerting (3)
```

That is `qti_ims_call_dial` over `IImsRadio`, not `RIL_REQUEST_DIAL`. It is still
not a VoLTE call. The handset's 4G indicator drops for the duration of every
call and returns when it ends, and the log agrees:

```
14:19:05.975  qcril_qmi_voice_set_audio_call_type: Set audio call_type as VOICE
14:19:05.975  convert_call_mode_to_radio_tech_family: entered call_mode 2
14:19:06.005  qcril_qmi_nas_invalidate_data_snapshot_in_case_of_csfb_in_alerting_state
```

`call_mode 2` is CS and there is an explicit **CSFB in alerting state**. ofono
asks for an IMS call; the modem sets one up on CS. Worth recording how this was
nearly missed: the same capture counted 28 `csfb` markers and `voice rte 2`
alongside the ofono lines, and those were waved through as routine noise because
the ofono lines said what was wanted. The handset's own indicator was the
correction.

### setServiceStatus is not dropped any more

This document has said since task #13 that `setServiceStatus` is *"accepted by the
vendor HAL and silently dropped -- the binder transaction completes and qcril
logs nothing at all"*. That is no longer true, and the source comment in
`qti_ims.c` saying so should be read as historical:

```
qcril_qmi_imss_request_set_ims_srv_status: has_calltype: 1, calltype: 0
qcril_qmi_imss_request_set_ims_srv_status: has_status: 1, status: 2
qcril_qmi_imss_request_set_ims_srv_status: .. qmi send async res 0
qcril_process_event: Exit QCRIL_EVT_IMS_SOCKET_REQ_SET_SERVICE_STATUS, err_no 0
```

calltype 0 is VOICE, status 2 is enabled, and the QMI send succeeds.

### qcril already picks the right generation of the API

The obvious follow-on guess -- that `setServiceStatus` lands on the modern
`0x8f` and dies of `INTERNAL` -- is also wrong. qcril carries both generations
side by side (`qcril_qmi_imss_request_set_ims_srv_status` and
`..._v02`, `set_ims_config` and `_v02`, `set_ims_registration` and `_v02`) and
selects between them, with `qcril_qmi_imss_get_modem_version` to decide.
At runtime it uses the **legacy** ones, every time; no `_v02` symbol appears in
any log. The vintage split is real and qcril handles it correctly on its own.

It also prints QMI message ids in hex with no prefix, which is worth knowing when
reading these logs:

```
qcril_qmi_imss_command_cb: .. msg id 53   -> 0x53 set client provisioning
qcril_qmi_imss_command_cb: .. msg id 36   -> 0x36 set qipcall config
qcril_qmi_imss_command_cb: .. msg id 21   -> 0x21 set reg mgr config
```

all answering `ril_err: 0, qmi res: 0`.

### The test-mode trap is not biting

The trap this document is named after -- ofono's registration request mapping to
QMI "set IMS test mode" rather than to a registration -- is still in the code
path, but harmless as configured:

```
qcril_qmi_imss_set_ims_test_mode_enabled: ims_test_mode_enabled = FALSE
```

and NV `/nv/item_files/ims/qp_ims_test_mode` reads `00000000`.

What that request *does* do is more interesting: it drives the voice domain
preference.

```
qcril_qmi_imss_request_set_ims_registration: has_state: 1, state: 1
qcril_qmi_imss_request_set_ims_registration: Need to change voice domain pref? Yes
qcril_qmi_nas_set_voice_domain_preference: voice_domain_pref : 3
```

### Everything on the configuration side is now correct

Which is the point this leaves us at. Checked directly, not inferred:

| setting | value | wanted |
|---|---|---|
| `voice_domain_pref` (NV and QMI NAS) | 3 = PS_PREFERRED | 3 |
| `ue_usage_setting` | 0 = voice-centric | 0 |
| client provisioning VoLTE (`imss 0x54`) | 1 | 1 |
| qipcall VoLTE (`imss 0x37`) | 1 | 1 |
| reg-mgr P-CSCF (`imss 0x26`) | 61.2.220.137 | set |
| `setServiceStatus` | calltype 0, status 2, res 0 | delivered |
| IMS registration | reaches `state:0` | registered |

and yet `ims_rte` stays 0, `VOIP service STATUS` flaps 2 -> 0 -> 2 -> 0, and
calls go CS. The remaining fault is that the registration does not *hold* long
enough or completely enough for the modem to treat IMS voice as in service. The
P-CSCF having to be written by hand is the most likely reason: PCO delivers it on
some IMS PDN activations and not others, so each re-registration is a coin toss.

### No prior art

Per the project rule that a zero-hit search is a finding, the `#sailfishos-porters`
archive over eleven years:

| query | hits |
|---|---|
| `client provisioning` | 0 |
| `pcscf` | 0 |
| `IImsRadio` | 16 lines, May 2026 |
| `qti_ims_call` | 5 lines, May 2026 |

There is also no public Qualcomm documentation for QMI IMS Settings (0x12) or
IMSA (0x21); neither is in libqmi. Everything here was reconstructed from the
blob's own tables and from the kernel's `ipa_qmi_service_v01.h` error enum.

The Android side that this modem was built for is in the tree and is the best
available specification: `frameworks/opt/net/ims/ImsManager.java` gives the order
as `updateVolteFeatureValue` -> `changeMmTelCapability(request)` -> `turnOnIms()`,
two distinct modem actions where this port currently does only the first.
`turnOnIms()` is `TelephonyManager.enableIms()`, and nothing in the ofono stack
has an equivalent.

## The modem decides, and it decides CS

Holding the IMS bearer up with a keeper loop (`scripts/qmi/imskeeper.sh`) removes
the last confound: the bearer is present, IMS reaches `state:0`, and a call is
placed. It still goes CS, and the handset drops from 4G to 2G for the duration.

The line that says it plainly:

```
qcril_qmi_voice_is_call_has_ims_audio: qcril_qmi_voice_info.jbims: 1, is cs call: 1
```

**`is cs call: 1`.** ofono queued `QCRIL_EVT_IMS_SOCKET_REQ_DIAL`, qcril accepted it
(`DIAL CALL RESP : ril_err=0 ... result 0`), and the modem set the call up on CS
anyway. This is not ofono choosing the wrong path and not qcril refusing; it is
the modem's own domain selection.

### The bearer teardown loop, which masked this for a long time

Before the keeper, every measurement was contaminated by a self-sustaining loop:

1. a call causes CSFB, dropping the UE to 2G/3G;
2. the CSFB tears down the IMS PDN and IMS deregisters;
3. ofono never re-activates `context3`, so IMS stays down;
4. the next call therefore has no IMS either, and falls back again.

The registration was never "unstable" -- its bearer was being removed and not
replaced. That is why five minutes of idle sampling showed `context3 Active:
false` with no registration activity at all, and it makes task #22 load-bearing
rather than cosmetic. `scripts/qmi/imskeeper.sh` is a test harness for holding
the variable still; the real fix belongs in ofono.

### Everything else is verified correct

Not inferred -- read back from the modem or from qcril's own logs:

| layer | evidence |
|---|---|
| carrier config | retargeted `rjil.mbn` selected for SUB0 |
| client provisioning VoLTE | `imss 0x54` tlv 0x11 = 1 |
| qipcall VoLTE | `imss 0x37` tlv 0x12 = 1 |
| P-CSCF | `imss 0x26` tlv 0x12 = `61.2.220.137`, reachable over the bearer at 57 ms |
| voice domain preference | 3 = PS_PREFERRED, `ue_usage_setting` 0 = voice-centric |
| VoPS from the network | `voice_support_on_lte val 1` |
| `setServiceStatus` | type VOIP(1), status ENABLED(2), one accTechStatus entry naming LTE; qcril logs calltype 0 status 2, `qmi send async res 0` |
| IMS availability per qcril | `qcril_qmi_nas_is_ims_available: is_available 1` |
| IMS registration | `QtiRadioRegInfo state:0` |

The `ext-qti` `setServiceStatus` implementation was re-checked against
`qti_radio_ext_types.h` and is right: `QTI_RADIO_STATUS_ENABLED` really is 2 (the
enum is DISABLED/PARTIALLY_ENABLED/ENABLED/NOT_SUPPORTED/INVALID, not a
three-value one), `QTI_RADIO_SERVICE_TYPE_VOIP` is 1, and the accTechStatus
vector carries exactly one LTE entry.

### What is left

One value never moves: `nas_cached_info.ims_rte` is `0`, with confidence 4 -- a
settled belief, not a missing one -- even while `is_ims_available` is 1 and the
registration reads REGISTERED. That is the input to qcril's voice-domain
decision, and it is the last thread.

The structural suspect is the gap named earlier from `ImsManager.java`: stock
Android performs `changeMmTelCapability(request)` **and then** `turnOnIms()`.
This port does the first -- that is what `setServiceStatus` is -- and has no
equivalent of the second. `turnOnIms()` is `TelephonyManager.enableIms()`, and
nothing in ofono, `ofono-binder-plugin` or our `ext-qti` fork calls anything like
it.

## Traced: what actually decides the domain, and why it is stuck

`ims_rte` is written in exactly one function, the static
`qcril_qmi_nas_update_ims_rte` (found by its own log string at `0xefa245`, its
body around `0x4ff550`). It branches on a single cached field and nothing else:

```
004ff5dc  ldr   w9, [x8, #0x624]      ; nas_cached_info + 0x624
004ff5e0  cbz   w9, #0x4ff600
          ; non-zero:
004ff5f4  str   w10(=3), [x9, #0x434] ; ims_rte = 3
004ff5f8  str   w8(=1),  [x9, #0x6d0] ; confidence = 1
          ; zero:
004ff60c  str   wzr,     [x9, #0x434] ; ims_rte = 0
004ff610  str   w8(=4),  [x9, #0x6d0] ; confidence = 4
```

We read `ims_rte 0 confd 4`, so that field is zero, and it is the whole answer.

Scanning every store to `nas_cached_info + 0x624` gives two: one zeroing it in
`qcril_qmi_nas_init`, and exactly one writer --

```
0x0062c8a8  str w9, [x8, #0x624]  in qcril_qmi_nas_set_registered_on_ims + 0x640
```

and resolving the PLT (these calls go through it, so a direct `bl` scan finds
nothing) gives that function exactly one caller in the whole library:

```
qcril_sms_process_transport_nw_reg_info_ind
```

**qcril learns "registered on IMS" from the WMS service's transport
network-registration indication -- from SMS, not from IMSA.** Nothing else in
`libril-qc-qmi-1.so` can set it. And that indication has never fired on this
device: zero occurrences in the radio log, alongside zero WMS activity of any
kind.

The reason it never fires is in our own code. `qti_ims.c` had exactly one
`setServiceStatus` call site and it enabled only `QTI_RADIO_SERVICE_TYPE_VOIP`.
The SMS service over IMS was never enabled, so the SMS transport never registers,
so the indication never comes, so `registered_on_ims` stays 0, so `ims_rte` stays
0, so every call goes CS. Android does not make this mistake: `ImsManager` builds
one `CapabilityChangeRequest` covering voice *and* SMS and sends it in a single
`changeMmTelCapability()`.

### The fix, and an honest result

`ofono-binder-plugin-ext-qti` commit `6fbbcf8` adds the second call. Built and
installed, ofono now sends both -- `Setting service 0 status 2` (SMS) and
`Setting service 1 status 2` (VOIP) -- and the HAL answers both:

```
imsradio0< [0000000c] 9 setServiceStatus
imsradio0< [0000000d] 9 setServiceStatus
imsradio0> [0000000c] 6 setServiceStatus
imsradio0> [0000000d] 6 setServiceStatus
```

**It has not moved `ims_rte`.** The transport indication still has not fired, and
the reason is visible: qcril logs no `request_set_ims_srv_status` at all for these
calls. The newest such line is from 14:14:08, while the radio log is current to
the second. So on this pass the HIDL transaction completes and qcril never
processes it -- the "accepted and dropped" behaviour that the earlier section
retired on the strength of the 14:14 evidence.

Both observations are real, which means the correction earlier in this document
was too broad: `setServiceStatus` is *sometimes* processed by qcril and sometimes
swallowed at the HAL, and what distinguishes the two is not yet known. That is
the next thing to pin down, because the mechanism behind it is now fully mapped
and only this last link is unreliable.

Current state, with the bearer held up by the keeper: IMS reaches
`QtiRadioRegInfo state:0`, both services are requested, `ims_rte` is still 0,
and calls still go CS.

## rild cannot be restarted: its IMS module does not come back

Why `setServiceStatus` is processed sometimes and swallowed other times has a
blunt answer. Counting log lines per rild instance:

| rild pid | total lines | lines containing "ims" |
|---|---|---|
| 17126 (earlier instance) | 429 | 234 |
| 26419 (after `ctl.restart ril-daemon`) | 1400 | **0** |

The restarted rild has logged fourteen hundred lines and **not one of them
mentions IMS**. Its IMS subsystem is completely inert: it still serves the
`IImsRadio` binder interface -- ofono's calls get answered, transaction 9 in,
response 6 out -- but nothing is ever turned into a
`QCRIL_EVT_IMS_SOCKET_REQ_*`, so qcril never sees it and the modem is never
told. The newest IMS socket event of any kind in the buffer stays frozen at the
last timestamp of the *previous* rild.

The ims daemons explain it. After `ctl.restart ril-daemon`:

```
vendor.imsqmidaemon    pid 2102     <- not restarted, from boot
vendor.imsdatadaemon   pid 58418    <- not restarted
vendor.imsrcsservice   pid 26543    <- restarted with rild
vendor.ims_rtp_daemon  pid 26545    <- restarted with rild
ril-daemon             pid 26419
```

Half the set restarts with rild and half does not, and the sockets rild's IMS
module needs (`/dev/socket/ims_qmid`, `/dev/socket/ims_datad`) belong to the
half that did not. The new rild never gets its IMS side up.

### What this invalidates

Every measurement taken after a rild restart was made against a rild whose IMS
module was dead, and that includes a lot of today. It also means the one window
where `setServiceStatus` visibly reached qcril was the coherent instance, and the
"accepted and dropped" behaviour recorded much earlier in this document was
probably this same effect all along rather than a HAL that ignores the call.

### The catch-22 it creates

Carrier-config staging as done here ends in `setprop ctl.restart ril-daemon`,
because that is what makes qcril re-select the config. So:

* stage the config and restart rild -> the retargeted config is selected, and
  rild's IMS module is dead;
* reboot instead -> IMS is alive, but boot rebuilds the `qcril_sw_mbn_*` tables
  from the pristine vendor images, so 404/80 no longer matches the retargeted
  config and it is not selected.

Neither order gives a working combination, which is why every call test today
ran with one half of the stack or the other missing.

The way out is the one the port already has a design for and has never used
here: stage the MBN files **at boot, before rild starts**, so a single boot
produces both a selected config and a live IMS module.
`karatep-modem-config.py` exists in droid-config for exactly this and has never
been installed on this handset -- all staging so far has been by hand. That is
the next step, and it has to come before any further call testing, because until
it lands no test is measuring the whole stack at once.

## Correction: rild restarts are not what kills IMS

The section above concludes that `ctl.restart ril-daemon` leaves rild's IMS
module inert, on the strength of a line count per rild pid: 234 lines mentioning
"ims" from one instance, zero from the next. **That measurement is circular and
the conclusion does not follow.** Counting log lines that mention IMS measures
whether IMS is *doing* anything, and with no IMS bearer up there is nothing to
do — so the count is zero whatever the module's state. The instance that scored
234 was simply the one that happened to have a bearer and an active
registration at the time.

A clean reboot settles it. After one, with rild started by init rather than by
`ctl.restart`, the same counts are zero: no lines mentioning IMS, no
`QCRIL_EVT_IMS_SOCKET_REQ_*` events at all. If a restart were the cause, a boot
would not reproduce it.

So the catch-22 described above is not real either, and the reasoning that
carrier-config staging must move to boot time because a rild restart poisons IMS
is withdrawn. Staging at boot is still worth doing — it is the only way any of
this survives a flash — but not for that reason.

### What the reboot did establish

Everything the modem was told survives a power cycle, which is worth knowing on
its own:

| item | after reboot |
|---|---|
| `mcfg_sw_muxd_version_1` | `071b0205` — the retargeted rjil config, still activated |
| `/nv/item_files/ims/IMS_enable` | 1 |
| `voice_domain_pref` | 3 |
| reg-mgr P-CSCF (`imss 0x26`) | `61.2.220.137` |
| client provisioning VoLTE / VT (`imss 0x54`) | 1 / 1 |
| qipcall VoLTE (`imss 0x37`) | 1 |
| IMS QMI services 0x12 / 0x21 / 0x22 | all present |

The earlier claim that none of this survives a reboot was wrong in the same
direction as the rest: the modem-side state persists in NV. What does not
survive is the *staged file set* being re-selected, and the bearer, and any
userspace scaffolding.

### Where it actually stops

With the bearer up after boot, there are still no IMS socket events and no
`set_registered_on_ims`. But the measurement is blocked rather than negative:
ofono is running with `OFONO_DEBUG=-d *` and reporting `Registered`,
`VoiceCapable` and `SmsCapable` all true — so the ext plugin is loaded and its
IMS interface is live — while journald shows zero lines mentioning `qti`,
`imsradio` or `Setting service`. `-d *` on this stack floods journald hard
enough to be rate-limited, and the lines that matter are being dropped.

So the next step is a narrower ofono debug selector rather than `-d *`, so that
`Setting service N status` and the `imsradio` transactions are actually
recorded. Until then it cannot be said whether ofono is sending setServiceStatus
this boot at all, and that is the one fact the whole remaining chain turns on.

## Unblocked: ofono sends it, the HAL answers it, qcril never sees it

Replacing `OFONO_DEBUG=-d *` with `-d *qti*,*binder_ims*,*binder_voicecall*`
(and disabling journald's rate limit for the test) makes the decisive lines
survive. They are unambiguous:

```
ims:Setting service 0 status 2          <- SMS
imsradio0< [0000000c] 9 setServiceStatus
ims:Setting service 1 status 2          <- VOIP
imsradio0< [0000000d] 9 setServiceStatus
ims:imsradio0 IMS services enabled
imsradio0> [0000000c] 6 setServiceStatus
imsradio0> [0000000d] 6 setServiceStatus
```

Both services, on both slots, every call answered by the HAL. And on the qcril
side, in the same window:

```
IMS socket events: 0
```

Not one `QCRIL_EVT_IMS_SOCKET_REQ_*` of any kind. So the `IImsRadio`
implementation accepts `setServiceStatus`, returns success, and does not forward
it to qcril. The SMS fix in `ext-qti 6fbbcf8` is being delivered correctly and
dying at the same place the VOIP one does.

This vindicates the oldest note in this document -- that the HAL "accepts the
call and drops it" -- and retracts the correction made earlier today which
declared that note historical. The one window where qcril did log
`request_set_ims_srv_status` (14:14:08) is the anomaly, not the rule, and what
made that window different is still unknown. It is the only evidence that this
path can work at all.

Registration in the same window is back to failing the way it did before the
P-CSCF was written by hand:

```
QtiRadioRegInfo state:2 error_code:2147483647   REGISTERING
QtiRadioRegInfo state:1 error_code:408          NOT_REGISTERED, timeout
```

with `imss 0x26` still holding `61.2.220.137`. So writing the P-CSCF into the
reg-mgr config is not sufficient on its own either; the one registration that
reached `state:0` did so under conditions that have not been reproduced since.

### Standing on this

Two things are now solidly measured rather than inferred, and both are negative:

* ofono is doing its part -- the ext plugin is loaded, both IMS services are
  requested, the dial path is the IMS one;
* the vendor `IImsRadio` implementation is where the chain breaks, silently.

Everything below that -- the config-item tables, the legacy/modern message
split, the `ims_rte` writer chain ending at the SMS transport indication -- is
mapped and correct, and none of it can be exercised while the HAL swallows the
one call that would start it.

## What serves imsradio0, and exactly where the call dies

`imsradio0` is served by **rild itself**. Only two files on the device contain
the interface name, `/vendor/lib64/vendor.qti.hardware.radio.ims@1.0.so` (the
generated interface, no implementation) and `/vendor/lib64/libril-qc-qmi-1.so`,
which rild loads. There is no separate IMS HAL process, which is why looking for
one found nothing.

The implementation is `ImsRadioImpl::setServiceStatus(int, ServiceStatusInfo
const&)` at `0x1730ac`. Its structure:

```
0017 30f4  bl qcril_malloc_adv                    ; allocate the _ims_Info
0017 30fc  cbnz x8, 0x1733f4                      ; allocated -> carry on
0017 33fc  bl utils::convertHidlToProtoServiceStatusInfo
0017 3404  cbz w0, 0x1736f8                       ; w0 == 0 -> dispatch
...
0017 36f8  mov w2, #0x1e                          ; _ims_MsgId 30 = SET_SERVICE_STATUS
0017 3708  mov x3, x8                             ; the converted request
0017 370c  bl ImsRadioImpl::processRequest
0017 3710  str w0, [sp, #0x44]
0017 3718  bl utils::isError
0017 371c  tbz w0, #0, 0x173754                   ; not an error -> done
0017 3734  bl qcril_free_adv                      ; error -> free the request
0017 3750  bl ImsRadioImpl::sendEmptyErrorResponse
```

An important misreading to record, because it nearly produced the opposite
conclusion. `convertHidlToProtoServiceStatusInfo` looks like a validating
predicate, and `cbz w0` after it reads naturally as "conversion failed, bail" --
which would have meant our `ServiceStatusInfo` payload was malformed and the
bug was ours. It is not a boolean. Its return is `ldr w0, [sp, #0x9c]`, a local
initialised to zero at entry, i.e. an **error code**, so `w0 == 0` is success
and `cbz w0, 0x1736f8` is the *dispatch* path. The payload is fine; the
conversion succeeds.

So the call is converted and handed to `processRequest` with `_ims_MsgId 30`,
which matches what qcril logged in the one working window
(`map_event_to_request: event 851998 mapped to ims_msg 30`).

**The drop is `processRequest` failing.** When it returns an error the
implementation frees the request and calls `sendEmptyErrorResponse` -- the HIDL
transaction completes normally, ofono sees response 6 and no transport error,
and nothing whatsoever reaches qcril's IMS event loop. That is precisely the
"accepted and silently dropped" behaviour this document has described from the
beginning, and it is now located to the instruction.

What is *not* yet established is why `processRequest` fails. It is inside rild,
past the HIDL boundary, so the next step is that function and whatever state it
checks -- most likely whether qcril's IMS module has a client registered on its
event loop at all.

## Down to the allocation, and what that rules out

`ImsRadioImpl::processRequest` is a short pipeline:

```
utils::imsRadioGetTag(slot, msgId, &tag)
qcril_qmi_ims_map_request_to_event(msgId)      -> event id
qcril_qmi_ims_get_msg_size(...)                -> size
qcril_qmi_ims_convert_ims_token_to_ril_token(...)
qcril_qmi_ims_flow_control_event_queue(...)    -> ril error
qcril_qmi_ims_map_ril_error_to_ims_error(...)  -> returned
```

so the error `setServiceStatus` acts on comes from
`qcril_qmi_ims_flow_control_event_queue`. That function is 0x2620 bytes with a
single exit returning a local at `sp+0x13c`, and **only three instructions ever
write it**: one initialising it to 0 at entry, and two setting it to 2
(`GENERIC_FAILURE`). Both of those sit immediately after a `qcril_malloc_adv`
whose result tested NULL — one for the 0x38-byte event header, one for the
payload buffer sized from `qcril_qmi_ims_get_msg_size`.

**There is no other failure path.** No client check, no registration check, no
"is the IMS module up" check, no state machine. The function either allocates
and queues, or fails to allocate. Which retires a family of hypotheses this
document has been entertaining all day -- that qcril refuses the request because
IMS is not registered, or because no client is attached, or because the modem is
in the wrong state. It cannot; it never looks.

Straight memory exhaustion does not explain it either. At the time of a dropped
call the handset had 1.6 GB free and 2.7 GB available, no swap pressure, no OOM
kills in dmesg, and rild's RSS was 20 MB. That leaves a bogus *size* -- if
`qcril_qmi_ims_get_msg_size` returns 0 or something absurd for this message, the
allocation fails without the system being short of memory. That is the next
thing to read.

### Both rild processes have live IMS modules

Also worth correcting, since an earlier section leaned on the opposite: slot 1
(pid 2171) and slot 2 (pid 2054) are *both* running IMS. Within seconds of an
ofono restart each logs `qcril_qmi_imsa_service_status_ind_hdlr`,
`qcril_qmi_ims_create_ims_info` and `sendMessage: IMS_UNSOL_SRV_STATUS_UPDATE`
-- rild pushing unsolicited IMS messages to a client. The IMS side of qcril is
up on both slots.

### One observation held back deliberately

At 15:37:47 UTC ofono called `setServiceStatus` on `imsradio0` and had a
response 130 ms later, and slot 1's rild logged **nothing at all** in that
second. It is tempting to read that as the call never arriving. It is not
sound: the HIDL server path's logging is gated on the same log-level globals as
everything else in this library, and `flow_control_event_queue` logs only on
some paths. Absence of a log line is not absence of a call, and this document
has already been wrong twice today by treating it as such.

## The message-size table is fine, so that hypothesis is dead

The previous section proposed that `qcril_qmi_ims_get_msg_size` returns 0 for
SET_SERVICE_STATUS, making the payload `qcril_malloc_adv(0)` return NULL and the
request get dropped. It does not.

The table can only be read from a live process: the pointer lives in a GOT slot
at `+0xfe98d0`, the library uses Android's packed relocations, so on disk that
slot is zero and `readelf -r` shows no relocation for it at all.
`scripts/qmi/readmsgtable.py` reads it out of the running rild. One trap worth
recording -- the first `PT_LOAD` of this library has `p_vaddr 0xb9000`, not 0, so
the load bias is the mapping start *minus* that; adding a vaddr to the mapping
start directly lands 0xb9000 high and reads string data ("obuf26ST" instead of a
pointer).

The table is 133 rows of `{msg_id, msg_type, ..., size}`, ids paired as
(id, type=1) and (id, type=2). For ours:

```
   56: id=30   type=1   size=72
   57: id=30   type=2   size=0
```

`get_msg_size(30, REQUEST)` returns **72**, not 0. The allocation it feeds is a
72-byte one on a handset with 2.7 GB available. It is not failing.

### Which leaves the whole "processRequest fails" story unsupported

Worth being explicit, because three sections of this document have been built on
it: **the failure was never observed.** It was inferred from qcril logging
nothing when ofono calls `setServiceStatus`, and the reasoning went
"`setServiceStatus` only drops the request when `processRequest` errors, so
`processRequest` must be erroring". Both of the ways that can happen have now
been checked and neither holds -- memory is not short, and the size lookup
returns a sane value.

So the premise is the thing to doubt. The remaining possibilities, none yet
distinguished:

* the call succeeds and is queued, and qcril simply does not log it at the level
  being captured -- the logging throughout this library is gated on a runtime
  level, and `logcat -b radio` is not the same as having that level on;
* the call is answered by something other than the rild instance whose log is
  being read;
* it does fail, but somewhere in `flow_control_event_queue` that has not been
  read yet -- the function is 0x2620 bytes and only its return-value writes have
  been examined.

The productive next step is not more static reading. It is to make rild say what
it is doing: raise its log level (`persist.vendor.radio.ril_extra_debug` is
already set, so the relevant control is elsewhere) or attach to the process, so
that a call in and a queue out can be observed rather than deduced.

## Stepping back: the registration is real, and setServiceStatus never mattered

Two measurements taken from outside the control path change the picture more
than anything in the last several sections.

### There is IPsec on the IMS bearer

`scripts/qmi/sipsniff.py` watches the IMS PDN with an unbound `AF_PACKET`
socket (unbound deliberately: the bearer bounces during a re-registration and a
bound socket dies with `ENETDOWN`, taking the capture with it). Over 100 seconds
across a forced re-registration:

```
packets=266 udp=10 sip=0
destinations seen:
  10.208.123.39 proto=50    9      <- ESP, inbound, to the IMS bearer's own address
```

**Protocol 50 is ESP.** No plaintext SIP is visible because 3GPP IMS carries SIP
inside an IPsec security association once the P-CSCF has challenged and the UE
has authenticated. Inbound ESP addressed to our IMS IP means an SA exists, which
means the REGISTER reached the core, was challenged, and was answered. The RF
path is intact and the request is intact.

That retires the reading of the earlier 408s as "nothing came back". Something
does come back, and `QtiRadioRegInfo state:0` -- REGISTERED -- is reproducible on
demand: toggling client provisioning VoLTE off and on over QMI (`imss 0x53`,
tag 0x10) re-registers IMS every time.

### setServiceStatus was never the blocker

`qcril_qmi_imss_request_set_ims_srv_status` makes exactly two QMI sends:

```
mov w0, #0xc   mov x1, #0x53   mov w3, #0xac   ; QMI 0x53, request 172 bytes
mov w0, #0xc   mov x1, #0x36   mov w3, #0x54   ; QMI 0x36, request  84 bytes
```

`0x53` is set client-provisioning config and `0x36` is set qipcall config -- the
two legacy setters already written by hand with `scripts/qmi/setprovvolte.py`,
and already reading back as 1. **So the entire HAL path, if it worked, would
achieve exactly what is already in place.** Whether `IImsRadio` swallows the
call or forwards it makes no difference to the modem's state, and the several
sections of this document devoted to that path were chasing something that could
not have been the cause.

### What is actually left

The modem is registered, provisioned and qipcall-enabled, and still reports:

```
qcril_qmi_imsa_service_status_ind_hdlr: VOIP: service_status(not valid)
qcril_qmi_imsa_service_status_ind_hdlr: VT:   service_status(not valid)
qcril_qmi_imsa_service_status_ind_hdlr: UT:   service_status(valid)
```

UT -- supplementary services -- is the one service the modem considers available,
and it is also the one whose config the Jio donor filled in
(`qp_ims_ut_config` = `jionet`). VOIP and VT are not available, `ims_rte` stays
0, and every call goes CS.

Which puts the Jio contamination back in the frame, sharply. The retargeted
config also wrote Jio's SMS settings onto a BSNL SIM:

```
qp_ims_sms_config -> '10138'  '+g.3gpp.smsip'  '0x00000400'
```

`10138` is Reliance's SMSC. SMS over IMS is not a side issue here: `ims_rte` is
written only when `qcril_sms_process_transport_nw_reg_info_ind` fires, and that
indication has still never fired once. A UE told to register SMS over IMS
against another operator's SMSC is a plausible reason for that, and it is a value
this port introduced rather than one the device came with.
