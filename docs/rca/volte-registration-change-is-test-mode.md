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
no IMS QMI services (0x12 IMSA, 0x1f IMSS, 0x20 IMSP, 0x21 IMS VT) for qcril's clients to bind
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
none of `0x12`/`0x1f`/`0x20`/`0x21` to three of the four present (`0x20`, IMS Application,
still absent). Two NV bytes and a version bump; no carrier impersonation anywhere.

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

qcril cannot tag the call with its APN types. A PDN that is not tagged as the IMS APN type
is plausibly not a PDN the modem's IMS stack will bind to, which would make this bearer
useless to it however correct the APN string is. That points at
`ofono-binder-plugin` rather than the carrier config: the radio HAL's `DataProfileInfo`
carries `supportedApnTypesBitmap`, and if ofono does not set it for the IMS context there
is nothing for qcril to tag with. **This is the most promising open lead.**

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

### Open threads

1. **`supportedApnTypesBitmap` on the IMS context** — see above. Check what
   `ofono-binder-plugin` puts in `DataProfileInfo` for a `Type: ims` context, and whether
   `qcril_data_set_apn_types` stops failing when it is set.
2. **ofono never activates the IMS context on its own.** Even once that is fixed, something
   has to bring the context up when IMS registration is wanted; right now it only happens
   by hand over D-Bus.
3. **The missing QMI service.** `0x20` has never appeared, at any point, under any config.
   qcril's presence write failing is consistent with the presence service being the absent
   one, but that identification is not proven and should be established rather than assumed.
4. **The one item we cannot set.** If `VOLTE_USER_OPT_IN_STATUS` is genuinely the flag the
   Xiaomi code writes, then this modem refusing that write may be the whole remaining story
   — and the fix would be to set the underlying NV item through the carrier config instead
   of over QMI, the same way `IMS_enable` and `qipcall_config_items` were.

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
