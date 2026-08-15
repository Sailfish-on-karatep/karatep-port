# VoLTE: the modem never parses BSNL's answer SDP

**Status:** the failure is located — the modem aborts the call without ever
running its SDP parser on BSNL's answer — but the instruction that decides the
abort is emitted as a QSR-hashed F3 record, and the string database needed to
read it does not ship in the firmware. Two earlier conclusions of this document
are retracted below.

VoLTE on karatep registers correctly, dials correctly, and dies during what
looks like media negotiation. Outgoing calls end ~35 ms after BSNL's
`183 Session Progress` with QMI end cause 373 (media class) and the text
`SDP parse failed`. Incoming calls are refused with `488 Not Acceptable Here`
28 ms after the INVITE.

Identifiers below are redacted: `4048001XXXXXXXX` is the private user identity,
`+9194873XXXXX` the MSISDN, `+9194873YYYYY` the called party.

Baseband is `MSM8937.LA.2.0-00440-STD.PROD-1`, November 2017 — the only one
Lenovo ever shipped for this device.

## The finding: `SDP parse failed` names a function that never ran

`qipcallsdp.c` is the modem's SDP module. Across a 420-second F3 capture
containing two complete failing call attempts, it speaks at exactly four
moments — and none of them is the arrival of BSNL's answer:

| t | message | what it is |
|---|---|---|
| 43.758 | `ReSizing med_arr by num_formats = 4`, `Bandwidth : AS 0`, `Max Bandwidth : AS 41` | call 1, building **our** INVITE offer |
| 45.008 | `qipcallsdp_cleanup_sdp_info_m_lines: … cleanup` | call 1, teardown |
| 48.987 | same three as 43.758 | call 2, building our offer |
| 49.841 | same cleanup | call 2, teardown |

BSNL's `183` arrives at 44.953 with a 1486-byte message carrying the answer SDP.
Between that instant and the `CANCEL` at 44.990, **`qipcallsdp.c` emits nothing
at all.** Neither does any `qvp_rtp*` module. The same holds for call 2.

The reason text is generated afterwards, at 45.002, by the code that reports the
failure northbound:

```
45.002  qipcall_indication…:416   before conversion reason_text = SDP parse failed
```

So `SDP parse failed` is a label attached to an error code on its way to the AP,
not a report from a parser that ran and failed. Every experiment this
investigation ran against the *contents* of the SDP — codec narrowing, bandwidth
lines, `fmtp` values, `telephone-event` channel counts — was aimed at a stage the
modem never reaches.

## What actually happens in the window

Taking the interval from the `183` to the start of `CANCEL` assembly, for both
calls, and keeping only the `file:line` records that fire in **both** windows and
nowhere else in 420 seconds, yields exactly twelve:

```
qvp_app_oa_api.c:2747      (x2 each)   string truncation, see below
qipcalldialog.c:5361 4670 5331 5227 5258
sipDialog.cpp:905 988 711 793
qimfif.cpp:8136
qimfif_cbs.cpp:1300 1537
```

All twelve are SIP dialog and IMS-interface bookkeeping: dialog init, local and
remote tag storage, dialog-id update, Contact header, peer capabilities, a new
client connection for the PRACK. Nothing media-related appears.

The last call-layer message before the abort is:

```
44.970  qipcalldialog.c:5258  [qipcalldialog_update_peer_caps] Tags not present in Contact, so ignore
```

BSNL's Contact header carries `x-afi` and `encoded-parm` but no `+g.3gpp.*`
feature tags, so the modem cannot derive peer capabilities from it. Twelve
milliseconds later the PRACK goes out; one millisecond after that, at 44.983,
the modem begins assembling the CANCEL. No network response arrived in between —
the `200 OK` to the PRACK does not come until 45.122. **The abort is decided
locally, immediately after the PRACK is sent.**

No dedicated QCI-1 voice bearer is established at any point before the CANCEL.

## Retraction: the 50-byte truncation is a logging artefact

Two earlier revisions of this document built on
`qvp_app_oa_api.c:2747  len of str printed exceeded dst str len50`, first
attributing it to BSNL's SDP fields and then, after retracting that, leaving it
as the one signal specific to the failing window. It is neither the cause nor a
symptom. It is the debug printer truncating its own output.

Each fire is immediately followed by the message whose argument it was
truncating:

```
qipcalldialog.c:4670   [update_dialog_id] [TEMP_OUTGOING_DIALOG ?= TEMP_OUTGOING_DIALOG]
qvp_app_oa_api.c:2747  len of str printed exceeded dst str len50          <---
qipcalldialog.c:5331   [updating call_ptr with dialog_id: <call-id><local-tag><remote-tag>]
```

That dialog id is 92 bytes with BSNL's tag values (call-id 34 + local tag 10 +
remote tag 48), printed into a 50-byte destination. The stored value is fine —
`qpSipSessionService:7011` reports `Found Sip Dialog ID` for that same identifier
several hundred times across the call, and `getSessionStateStructByDialogID`
resolves it. Only the log line is short.

Two independent checks confirm it:

- **A control capture across a full IMS re-registration** (`Online` toggled off
  and on, `REGISTER` → `401` → `REGISTER` → `200 OK` → `SUBSCRIBE`/`NOTIFY`, all
  successful) fires line 2747 **zero** times, while firing the other six
  `qvp_app_oa_api.c` sites in exactly the same 1:1:1:3:1:1 per-event proportion
  as a call does. The module behaves identically in the succeeding and failing
  cases; only the dialog-id print differs.
- Registration never builds a real dialog id — `qipcalldialog.c:5361` fires there
  with `[call_id: ] [dialog_id: ]`, both empty — which is why 2747 cannot fire.
  So the control confirms the mechanism, not the causality: it shows *why* the
  message is call-only, and that it tracks a print, not a parse.

`qvp_app_oa_api.c` is a generic string helper, not an offer/answer module: its
other call sites report destination sizes of 3000, 204, 44 and 24 bytes, and
line 1117 reports the identical kind of overflow against a 204-byte buffer at
INVITE-build time on every call, including in the successful registration
capture, harmlessly.

## The blocker: 88% of the modem's F3 output is hashed

The capture holds **1,920,523** F3 records. Only **228,750** carry inline format
strings. The other 1.69 M are QSR ("silent reporting") records: an `EXT_MSG_F`
wrapper in which the format string and filename are replaced by a single 32-bit
hash placed immediately after `ss_mask`:

```
u8 cmd (0x92) | u8 ts_type | u8 num_args | u8 drop_cnt
u64 timestamp
u16 line | u16 ss_id | u32 ss_mask
u32 msg_hash                       <-- where the strings would be
u32 args[num_args]
```

A parser that expects two trailing NUL-terminated strings silently drops all of
them, which is what happened here at first.

The hashes resolve against a table in **`modem.b14`**, 8 bytes per entry:

```
u16 file_index | u16 line | u32 msg_hash
```

Verified against the wire: every hash observed in a live record is present in
`modem.b14` exactly once, and the `line` beside it equals the line number in the
record's own header. What `modem.b14` does **not** contain is any mapping from
`file_index` to a filename, or from a hash to a format string — that is the point
of QSR. The strings live in the vendor database that ships with QXDM/QCAT for
this exact build, and no such database is present in the Lenovo QPST package, the
stock ROM, or any other firmware bundle held here.

**Correction:** an earlier revision of this document called `modem.b24` "the QSR
string table". That is wrong. `modem.b24` (97 KB, 92% printable) is the
**inline** string table — `filename.c:format string\0` pairs, NUL-padded to
4-byte alignment — holding exactly the strings that arrive already readable on
the wire. It resolves nothing that is hashed.

Attributing the hidden records by `ss_id`, using an empirical `ss_id` → module
map built from the plaintext records, shows where the decision is being made:

| ss_id | call 1 | call 2 | whole capture | % in the two 20 ms windows | module (from plaintext) |
|---|---|---|---|---|---|
| 51 | 275 | 218 | 31,247 | 1.6% | `qimfif.cpp`, `qipcalliface_ho_mgr.c`, `qipcallsdp.c`, `qvp_app_oa_api.c` |
| 6054 | 84 | 59 | 1,405 | 10.2% | `sipConnection.cpp`, `sipDialog.cpp` |
| 6062 | 10 | 9 | 402 | 4.7% | `qpSipSessionService`, `qpRequestProcessor` |
| 6053 | 6 | 5 | 45 | 24.4% | `singoConfig.cpp` |

The two windows are 40 ms out of 420 s — 0.0095% of the capture. Subsystem 51,
the IMS call application, emits **493 hashed records** inside them against 4
plaintext ones. The decision to abort is in that set, and it cannot be read
without the vendor string database.

(`ss 5018` shows the sharpest concentration of all, 30 records in call 1 and
26.5% of its whole-capture output — but zero in call 2, so it is not part of the
reproducible path.)

## It fails symmetrically

An incoming VoLTE call is refused with `488 Not Acceptable Here` 28 ms after the
INVITE. `488` is precisely "your SDP is not acceptable to me". That symmetry
rules out every hypothesis living in the answer-specific path — PRACK handling,
precondition and UPDATE sequencing, the 183 early-media flow — because the
incoming rejection involves none of them.

It also eliminated two suspects outright: the incoming offer contains **no**
`telephone-event/8000/1` channel count and **no** duplicate `c=` line — the two
oddities an earlier revision had flagged — and was rejected anyway.

**The incoming case remains the cleanest un-run experiment.** INVITE to 488 is
28 ms with no PRACK, no preconditions, no CANCEL and no dialog-id concatenation,
so it should isolate the same decision with far less around it. It has still not
been captured with F3 enabled.

## The instrument was wrong three times

Each error has the same shape: a real measurement of the wrong thing, recorded as
a property of the modem.

**1. Log masks were only ever raised for equipment id 1.** Every earlier
conclusion — "72,323 frames, 93 distinct codes, no failure-specific code" — came
from one sixteenth of the modem's logging. Writing the driver's batches to disk
untouched and decoding on the host removes the CPU cost that had forced the
restriction: the same window then yields **739,497 packets and 444 distinct codes
across equipment ids 1, 4, 5, 7 and 11**. (The honest result of that sweep: even
at full spectrum, no log code is failure-specific. The question is now closed
properly rather than by budget.)

**2. F3 message masks were never set at all.** This document's parent RCA states
in several places that this modem "emits no F3 debug messaging at all". Log masks
and message masks are separate mechanisms with separate commands, and only the
log masks had ever been raised. The command format comes from the device's own
kernel (`drivers/char/diag`):

```
DIAG_CMD_MSG_CONFIG           0x7D      diagchar.h:95
DIAG_CMD_OP_SET_ALL_MSG_MASK  5         diagchar.h:137

struct diag_msg_config_rsp_t {          diag_masks.h:85
    uint8 cmd_code; uint8 sub_cmd; uint8 status; uint8 padding;
    uint32 rt_mask;
} __packed;
```

`diag_cmd_set_all_msg_mask()` does `memset(mask->ptr, req->rt_mask, ...)`, so it
is the low byte of `rt_mask` that lands in every mask byte.

**3. The F3 decoder silently discarded seven records in eight.** See *The
blocker*, above. The first version of this document's own parser assumed every F3
record ends in two NUL-terminated strings, so it dropped every QSR record without
error — and the conclusion "nothing in that gap concerns media" was drawn from
the 12% that survived. Frame CRCs were checked afterwards: **6,545 of 6,545**
frames in the decisive window pass, and the modem's own `drop_cnt` is zero
throughout it. Nothing was lost on the wire; it was lost in the decoder.

`MSG_F` / `EXT_MSG_F` inline layout, for anyone decoding these:

```
u8 cmd_code | u8 ts_type | u8 num_args | u8 drop_cnt
u64 timestamp
u16 line | u16 ss_id | u32 ss_mask
u32 args[num_args]
char fmt[]   (NUL-terminated)
char file[]  (NUL-terminated)
```

**QSherlock also exists** and had never been read: packet type `0x98`, carrying
plain-text events such as
`CM | EVENT | HIGH | CALL_DROP: as_id 0, start_addr 0x0`, which appear at each
failed call. Low volume, and it carries the event but not the reason.

## Corrections to earlier conclusions in this investigation

- **The `b=AS` bandwidth hypothesis was wrong in its mechanism.** `modem.b24`
  does contain
  `qipcallsdp.c:qipcallsdp_should_AS_validation_be_ignored: ignore AS validation
  for RJIL` — a real, hardcoded carrier bypass for Reliance Jio. But the observed
  `Bandwidth : AS 0` / `Max Bandwidth : AS 41` pair fires at **INVITE-build**
  time: the modem computing *our own* `b=AS:41`. It is not validating BSNL's
  answer, and it now cannot be — the answer is never parsed.

- **Narrowing the codec offer changed nothing.** `qipcall_audio_codec_list` is
  ASCII, semicolon-separated, NUL-padded to 128 bytes — `rjil.mbn` ships
  `AMR_WB_OA;AMR_WB_BE;AMR_OA;AMR_BE`. Setting it to `AMR_OA;AMR_BE` moved our
  offer from `RTP/AVP 99 97 105 96` to `RTP/AVP 103 97 96`, dropping AMR-WB and
  `telephone-event/16000` as intended. BSNL's answer was structurally identical
  either way and the call failed the same way. With the SDP parser now known not
  to run, this result is expected rather than puzzling.

- **`modem.b24` is not the QSR string table.** See *The blocker*.

## Next measurements

1. **F3 during an incoming call.** 28 ms, no PRACK, no preconditions, no CANCEL.
   The cleanest window in which to catch the same decision. Costs one inbound
   call and is the only cheap experiment left.
2. **Obtain the QSR string database for `MSM8937.LA.2.0-00440-STD.PROD-1`.**
   Without it, 493 records at the decision point are permanently opaque. It ships
   with QXDM/QCAT and is not in any firmware bundle held here.
3. **Diff against a working handset on the same SIM.** The same SIM works
   immediately in a Snapdragon 870 handset. A capture of *its* IMS exchange would
   show what BSNL sends when it works, and whether the difference is in what we
   send or in what they send back.

## Why a firmware fix is not available

- **The modem image cannot be altered.** MSM8937 PIL images are authenticated by
  TZ against a certificate chain rooted in OEM fuses; changing any `modem.b*`
  invalidates the signed hash segment and the image will not load. Re-signing
  needs the OEM key. Lenovo shipped no later baseband for this device, and
  another vendor's newer MSM8937 image will not authenticate against these fuses.
- **No host-side lever has ever touched it.** Fourteen NV alignments, five media
  booleans, a codec narrowing, a mode-set change and a full carrier config swap
  all failed to move the symptom.
- **The workaround that was proposed no longer has a mechanism.** Forcing a G.711
  offer was worth trying while the fault was thought to be an over-length SDP
  field in BSNL's answer, since their own offer lists `PCMA/8000` and carries no
  `fmtp` at all. Now that the answer is known never to be parsed, changing what
  we offer cannot change the outcome — as the codec-narrowing run already showed
  empirically.

This is consistent with reports of the same symptom on shipping handsets on
BSNL — calls dropping immediately, callers hearing "switched off", resolved by
turning VoLTE off — and with the same SIM working immediately in a Snapdragon
870 handset four firmware generations newer.

**Current state:** the dial fix is reverted, so voice is placed on CS and works.

## Reusable tooling

Written for this investigation, useful to any Qualcomm porter:

| script | what it does |
|---|---|
| `scripts/qmi/sdpraw.py` | raises all 16 log masks **and** the F3 message masks, writes the DIAG driver's batches to disk verbatim — no analysis on the handset |
| `scripts/qmi/sdprawparse.py` | host-side decode of log packets: HDLC, log headers, SIP reassembly, per-window frame dumps |
| `scripts/qmi/f3parse.py` | host-side decode of F3: `MSG_F` and `EXT_MSG_F` with arguments substituted, QSR records counted rather than dropped, `ss_id` census |
| `scripts/qmi/f3probe.py` | enables F3 messaging and censuses what the modem actually emits |
| `scripts/qmi/f3reg.sh` | the registration control: F3 across a forced deregistration/re-registration, no calls, no NV writes |

The device-side cost of full-spectrum capture is what forces the design: 198
KB/s with F3 and equip-1 logs, about 1 MB/s with all sixteen log masks during a
call. Both are affordable only because the handset does no parsing.

## Prior art

None. The `#sailfishos-porters` archive returns zero hits for `SDP parse
failed`, `LucentPCSF`, `qipcall_dan`, `qipcall_audio_codec_list` and
`telephone-event`. Eleven years of porter logs have not discussed this.
