# VoLTE: the modem never parses BSNL's answer SDP

**Status:** the failure is located — on outgoing calls the modem aborts without
ever running its SDP parser on BSNL's answer — and an F3 capture of incoming
calls shows this is not a general property of the modem: on the incoming path
the same parser *does* run. That asymmetry is the sharpest structural fact in
this investigation. The instruction that decides each abort is emitted as a
QSR-hashed F3 record, and the string database needed to read it does not ship in
the firmware. Two earlier conclusions of this document are retracted below.

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

## The parser runs on incoming calls and not on outgoing ones

The incoming path was captured with F3 enabled for the first time: two calls,
INVITE to `488 Not Acceptable Here` in **27 ms** and **29 ms**, with no PRACK, no
preconditions and no CANCEL. It behaves differently from the outgoing path in
exactly one structural way, and it is the important one.

`qvp_sdp_parser_util.c` — the SDP parser proper — fires **once per incoming
call**, six milliseconds before the `488`:

```
20.757  qipcalldialog.c:5258      [update_peer_caps] Tags not present in Contact, so ignore
20.757  qipcallh.c:22317          [updating call_ptr with dialog_id: TEMP_INCOMING_DIALOG]
20.757  qimfif.cpp:8116           qimfif_get_contact: contact hdr = <sip:pcgw-tcsp…>
20.757  qvp_sdp_parser_util.c:2015  [g_l_s]pppppppppppppppppppppppppppppppppppppppppppppppppp
20.757  qvp_sdp_parser_util.c:2018  [g_t_s]--------------------------------------------------
20.757  qvp_app_oa_api.c:2747     len of str printed exceeded dst str len50
20.763  sipConnection.cpp:2940    OutGoing:LogSipMsg Method: 2 RespCode: 488
```

Across the three captures held:

| capture | duration | events | `qvp_sdp_parser_util.c` records |
|---|---|---|---|
| outgoing calls | 420 s | 2 full call attempts | **0** |
| registration control | 200 s | 2 full IMS registrations | **0** |
| incoming calls | 300 s | 2 incoming INVITEs | **4** (2 per call) |

So the parser is not dead code, not masked out, and not unreachable on this
build. It runs when an offer arrives and does not run when an answer arrives.
Whatever kills an outgoing call happens **before** the answer reaches the
parser, which is why `SDP parse failed` is a mislabel there; the incoming
rejection, by contrast, comes *after* a parse.

The two parser records are themselves casualties of the print helper. Each
prints exactly fifty fill characters — fifty `p`, then fifty `-` — rather than
any SDP content, and `qvp_app_oa_api.c:2747` fires immediately afterwards. The
consistent reading is that the helper detects an over-length source, logs the
complaint and leaves the destination's fill pattern in place rather than copying
a truncated prefix. That also confirms 2747 as a generic string helper: it fires
adjacent to dialog bookkeeping on one path and adjacent to the SDP parser on the
other.

**The decision itself is still hidden.** In the 6 ms and 4 ms between the last
parser record and the `488`, subsystem 51 emits **122** and **230** QSR-hashed
records respectively and nothing in plaintext. Same wall as on the outgoing
path, in a much smaller window.

Two side observations from the same capture:

- **IMS registration is churning.** In 300 seconds the device re-registered
  twice, unprompted, each time landing on a different local address
  (`…22.107:8995` → `…245.74:8996` → `…75.32:8997`), roughly 35 s after each
  failed call. The IMS PDN is being torn down and rebuilt, which no earlier
  capture was long enough to show.
- **QSherlock records the fallback.** `CALL_DROP` fires at each `488`, followed
  ~13 s later by `FULL_SRV` — the CS fallback that makes the call ring at all.

## What actually happens in the outgoing window

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
capture, harmlessly. The incoming capture settles this: 2747 fires there twice
per call as well, once next to dialog bookkeeping and once next to
`qvp_sdp_parser_util.c`. One helper, two unrelated callers, no causal role in
either direction.

## The blocker: 88% of the modem's F3 output is hashed

The capture holds **1,920,523** F3 records. Only **227,849** carry inline format
strings. The other 1,692,674 are QSR ("silent reporting") records, in which the
format string and filename are replaced by a single 32-bit hash placed
immediately after `ss_mask`.

**The packet type is the discriminator, exactly.** Every one of the 227,849
`MSG_F` (`0x79`) records is inline and every one of the 1,692,674 `EXT_MSG_F`
(`0x92`) records is hashed — 100% and 0%, no overlap. Do not infer the layout
from the names: on this build it is the *legacy* type that carries whole format
strings and the *extended* type that carries a hash. An earlier revision of this
document had that backwards.

```
u8 cmd (0x92) | u8 ts_type | u8 num_args | u8 drop_cnt
u64 timestamp
u16 line | u16 ss_id | u32 ss_mask
u32 msg_hash                       <-- where the strings would be
u32 args[num_args]
```

A parser that expects two trailing NUL-terminated strings silently drops all of
them, which is what happened here at first. Worse than dropping: **901 of them
decoded as plausible inline records**, because their trailing argument bytes
happened to contain a NUL followed by printable ASCII. One decoded as file
`oh`, format `5`, appeared exactly once in 420 seconds, and — five milliseconds
before a call was cancelled — was briefly taken for a unique event. Its frame
CRC was valid. It was `f101:6482` with args `(…, 2000, …)`, and the `oh` was two
bytes of an integer. `scripts/qmi/f3parse.py` now keys on the packet type and
additionally requires the filename to look like a source path; no conclusion in
this document depended on the 901.

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
string table". That is wrong. `modem.b24` (97 KB, 92% printable) is *an*
**inline** string table — `filename.c:format string\0` pairs, NUL-padded to
4-byte alignment — holding strings that arrive already readable on the wire. It
resolves nothing that is hashed.

`modem.b24` is not even the whole inline pool: it holds 1,178 strings, and
plenty of messages that print in plaintext are absent from it and from every
other segment (`len of str printed`, `qimfif allow header`). Those live
compressed in `modem.b21`, whose entropy is 7.91, and are unpacked at load.

**Inline messages carry no `file_index`, which blocks the obvious shortcut.**
Take the `(file, line)` pairs of messages known to print inline — seven lines of
`qipcallsdp.c`, seven of `qvp_app_oa_api.c`, six of `qipcalldialog.c`, eight of
`qimfif_cbs.cpp` — and intersect the `file_index` candidates for each file's
lines in `b14`: every intersection is **empty**. An inline message has no entry
in the hash table, because it needs no hash. So no inline string can be used to
calibrate what a `file_index` means.

(Stated more carefully than an earlier revision of this document did: it is
*messages* that are either inline or hashed, not files. A single source file can
have some of each — `qipcallh.c` prints lines 3363, 22317 and 30143 in plaintext
while other lines of some large file are hashed.)

### Can the names be recovered from the image at all? Not yet, but not never

`modem.b14` is 5 MB and 35% printable, which is far more than an 8-byte-per-entry
binary table needs. Mapping it out:

| region | contents |
|---|---|
| `0x000000`–`0x000380` | 56 legacy 16-byte descriptors: `u32 line, u32 ss_id, u32 fmt_ptr, u32 file_ptr`, pointers into modem address space |
| `0x000380`–`0x207020` | the QSR table, ~2 MB, ~260k entries, sorted by line within per-file-group segments |
| `0x207020`–`0x212ea6` | **a filename array**: 3,038 NUL-terminated names, `rex_os_context.c`, `bit.c`, `crc.c`, `memheap.c`, … |
| `0x227df4`–… | 386 `ssscr_*` supplementary-service strings, and further string regions to ~`0x4d0000` |

So the firmware does carry a filename array. It is not, however, what
`file_index` indexes — checked against three subsystems whose modules are known
from plaintext, and all three are incoherent:

| subsystem | known modules | observed `file_index` | name at that array position |
|---|---|---|---|
| 51 | `qipcall*`, `qimfif*`, `qvp_*` | 98–101 | `tdsrf_lm.c`, `tdsirat.c` (TD-SCDMA) |
| 9501 | `lte_rrc_*` | 442–490 | `srchtc_sm.c`, `outputstream.cpp` |
| 9509 | `lte_ml1_*` | 530–577 | `rex_tcb.c`, `rcinit_term.c` |

No constant offset reconciles them, and the relative order is wrong for a
compressed subset (`qipcall*` sits at array 2758 while `lte_ml1_*` sits at 1993,
the opposite of the observed ordering). There is no offset or lookup array
between the end of the QSR table and the start of the filename blob — they abut.

An attempt to identify files by line-number fingerprint instead — an inline line
must be *absent* from its own file's hashed line set while its neighbours are
present — failed on data quality: a naive 8-byte scan of the 2 MB table picks up
non-table bytes, and the resulting sets collide with almost every known inline
line. Delimiting the table's segment structure properly is the prerequisite, and
it has not been done.

**Other firmware does not substitute.** The two Nokia MSM8937 images held here
are `MSM8937.LA.3.1.2-00360-STD.PROD-1`; this device is
`MSM8937.LA.2.0-00440-STD.PROD-1.102262.2.113053.1`. Different Qualcomm release,
different source tree, so neither line numbers nor file indices correspond.

The realistic routes to the names, in order of cost:

1. **The QXDM/QCAT string database for this exact build.** The legitimate source;
   Qualcomm licenses it to OEMs and partners. It is not on the device, not in any
   of the eight carrier configs, and not in the Lenovo QPST package or stock ROM
   — all checked.
2. **Finish reversing `modem.b14`.** A bounded task: the container is mapped
   above, and what is missing is only how `file_index` resolves. Getting it would
   name the three files that decide both failures, which is most of what is
   wanted — the format strings would still be absent, but a module name plus a
   line number is enough to reason about.
3. **Sidestep it** with a capture from the Snapdragon 870 handset that works on
   this SIM, which shows what a successful exchange looks like without needing to
   read this modem's mind.

### What the hashes do give: the same three files decide both failures

`file_index` is recoverable even though the filename is not, and it answers the
open question of whether the two directions share a fault. For every hashed
`ss_id 51` record in the four failing windows the hash resolves in `b14` —
**zero unresolved** — and the file indices are:

| window | records | file 101 | file 98 | file 99 |
|---|---|---|---|---|
| outgoing call 1 | 272 | 191 | 68 | 13 |
| outgoing call 2 | 218 | 163 | 43 | 12 |
| incoming call 1 | 122 | 88 | 27 | 7 |
| incoming call 2 | 230 | 130 | 84 | 16 |

Three files, the same three, in the same order of dominance, in both directions.
The windows are 0.0095% (outgoing) and 0.0033% (incoming) of their captures, so
these are enrichments of 80× to 310× over background. **Whatever refuses an
incoming offer and whatever cancels an outgoing call are running in the same
three source files of the IMS call application.** That is evidence for one fault
with two expressions rather than two unrelated defects.

The instruction traces even share a motif. Both directions run this identical
thirteen-record sequence, ending at the same inline message:

```
f98:10107 | f101:310 x2 | f101:1887      (three times)
f98:5115  | f101:10078  | f98:4760
INLINE qipcalldialog.c:5258  [update_peer_caps] Tags not present in Contact
```

after which they diverge. Sixty-nine `file:line` keys are common to all four
failing windows, but **none of them fires exclusively there**, so there is no
single hashed record that marks the failure. The nearest miss, `f101:6456`,
fires exactly once per failure in both directions — but also during
registration, and its one argument is `2000` or `500`. It is a timer helper, not
a verdict.

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

## The network builds the voice bearer 36 ms after the modem gives up

The outgoing abort is not a timeout. The dedicated QCI-1 bearer BSNL's answer
asks the UE to confirm *does* get established — just after the CANCEL:

```
44.983  CANCEL header assembly begins
44.987  lte_rrc_stm.c   LTE_RRC_RRC_CONNECTION_RECONFIGURATION_DLM
44.990  CANCEL sent
45.026  netmgr_tc.c:3208  flow bearer_id=7  priority=7  datarate=42000
45.026  netmgr_tc.c:458   [0] UDP SRC start port = 50010
45.026  netmgr_tc.c:462   [0] UDP Dest start port = 39460
```

Those filter ports are the negotiated RTP ports: 50010 is our own
`m=audio 50010`, 39460 is BSNL's `m=audio 39460` from the answer. The network
did everything right and delivered the bearer 36 ms late relative to a decision
the modem had already taken 1 ms after sending the PRACK.

`qipcall_qos_reservation_timer` reads `401f` — 8000 ms. The modem is configured
to wait eight seconds for exactly this and waited one millisecond, because it
never entered the precondition path at all: it never parsed the answer that
would have told it preconditions were in play.

## The answer's contents are provably irrelevant

This is the earlier investigation's precondition experiment, which is worth
re-reading now that the parse is known not to happen. All five media booleans
were set to Jio's zeros, and the capture confirmed the change reached the wire —
zero `a=curr`/`a=des`/`a=conf:qos` in either direction, our session-level `b=`
block gone, and BSNL's answer stripped to its minimum:

```
v=0
o=LucentPCSF 817657360 817657360 IN IP4 imsgroup-322-…3gppnetwork.org
s=-
c=IN IP4 61.2.220.148
t=0 0
m=audio 40584 RTP/AVP 97 96
a=rtpmap:97 AMR/8000/1
a=fmtp:97 mode-set=0,2,4,7; mode-change-period=2; mode-change-neighbor=1
a=rtpmap:96 telephone-event/8000/1
a=fmtp:96 0-15
a=sendrecv
```

The call failed identically: `INVITE → 100 → 183 → PRACK → CANCEL`, cause 373,
`SDP parse failed`. At the time this was read as eliminating preconditions, QoS
and bandwidth as suspects. It does more than that. A minimal, unobjectionable
answer — no preconditions, no bandwidth lines, no over-length fields beyond the
`o=` FQDN — is not parsed either. **Nothing BSNL puts in the answer changes
whether the answer is read.**

Put beside the incoming capture, the statement is sharp: on this configuration
the modem parses SDP in **requests** and never in **responses**, and it is the
response case that reports `SDP parse failed`.

The divergence is localised to a few instructions. Both paths run the same
dialog bookkeeping and both end at `qipcalldialog.c:5258`
(`update_peer_caps: Tags not present in Contact, so ignore`). On the incoming
path `qimfif.cpp:8116` then fetches the Contact a second time and the parser
runs. On the outgoing path — nothing, and the PRACK goes out. The two paths even
call different `get_contact` sites: `qimfif.cpp:8116` incoming, `8136` outgoing.
What happens after 5258 is QSR-hashed in both directions.

## NV is exhausted, confirmed exhaustively

The earlier investigation eliminated the NV space item by item, by hand.
`scripts/qmi/nvsweep.sh` now does it mechanically: for all 62 `/nv/item_files/ims/*`
items, it compares the live value against all eight carrier configs in the modem
image and reports only disagreements. The result agrees with the hand analysis.

Exactly three items match no config at all, and none is a candidate:

| item | live | note |
|---|---|---|
| `qipcall_audio_codec_list` | 128 bytes of zeros | blanked by this investigation itself, not a baseline |
| `qp_ims_ut_config` | `"jionet"` | Jio's XCAP domain, a retargeting leftover; supplementary services, not voice |
| `qp_ims_vt_4G_media_capability` | — | video |

One correction to the parent RCA's table: `qp_ims_media_config` is **not**
Jio-specific. Compared over all 535 bytes rather than its first byte, the live
value is byte-identical to `gcf` and `ntel`, and the leading `06` is shared by
four of the eight configs. It was aligned correctly and is not an outlier.

**The codec list is a real lever and it is not the fault.** Restoring Jio's
`AMR_WB_OA;AMR_WB_BE;AMR_OA;AMR_BE` moves the offer from
`ReSizing med_arr by num_formats = 4` to `num_formats = 6`, so the item plainly
drives media construction. But `qipcallsdp_reallocate_med_arr:num_formats is 0!`
and `Failed to reallocate med_arr!! Status = 1` fire **exactly eight times per
capture either way** — that pair is a different `med_arr` and is background
noise at every IMS bring-up, not a symptom. Tested without placing a single
call, since the signal appears at registration.

## Both directions fail, but not in the same place

Both directions fail, and for a long time that symmetry was read as evidence of
one shared cause. With F3 on both paths, that reading no longer holds at the
level of mechanism.

| | outgoing | incoming |
|---|---|---|
| verdict | `CANCEL`, QMI cause 373, `SDP parse failed` | `488 Not Acceptable Here` |
| time from peer SDP to verdict | ~35 ms | 27 / 29 ms |
| SDP parser runs? | **no** | **yes**, 6 ms before the verdict |
| what precedes the verdict | PRACK sent, then abort 1 ms later | parse, then refusal |

`488` is precisely "your SDP is not acceptable to me", and on the incoming path
that is at least an honest answer: the modem looked at the offer and declined it.
On the outgoing path the same complaint is issued about an answer the modem
never read.

What the symmetry does still buy is elimination. The incoming rejection involves
no PRACK, no preconditions, no early-media flow and no dialog-id concatenation,
so none of those can be necessary to the failure. It also cleared two suspects
outright: the incoming offer contains **no** `telephone-event/8000/1` channel
count and **no** duplicate `c=` line — the two oddities an earlier revision had
flagged — and was rejected anyway.

Whether one fault produces both verdicts, or two faults do, is open. A single
capability mismatch evaluated at different points in the two flows would explain
it; so would two unrelated defects. Nothing measured so far distinguishes these.

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

`MSG_F` (`0x79`) inline layout, for anyone decoding these — note this is the
type that carries strings, not `EXT_MSG_F`:

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

1. **Obtain the QSR string database for `MSM8937.LA.2.0-00440-STD.PROD-1`.**
   This is now the only thing standing between the capture and the answer. The
   decision is taken inside 122–493 hashed subsystem-51 records that are already
   on disk, in three separate captures; the database is the decoder for them. It
   ships with QXDM/QCAT and is not in any firmware bundle held here.
2. **Find why the answer never reaches the parser.** The parser demonstrably
   works — it runs on every incoming offer. Something on the outgoing path
   discards or rejects the answer before dispatching it. The `183`'s body is
   present (`ActualMsgLen: 1486`) and `qimfif_cbs.cpp:1932`
   (`content length is 0`) fires for the bodiless `100 Trying` but not for the
   `183`, so the body is at least noticed. What happens to it next is hashed.
   The one remaining untested difference between the two paths is the *message
   type* carrying the SDP: every answer this device has ever received arrived in
   a reliable provisional `183` with `RSeq`, never in a `200 OK`. Forcing the
   answer into a `200 OK` would need 100rel disabled, and no NV item for that
   has been identified — `qp_ims_sip_extended_0_config` (1024 bytes) is the
   likeliest home for such a flag and is undecoded.
3. **Diff against a working handset on the same SIM.** The same SIM works
   immediately in a Snapdragon 870 handset. A capture of *its* IMS exchange would
   show what BSNL sends when it works, and whether the difference is in what we
   send or in what they send back.
4. **Investigate the IMS registration churn.** Two unprompted re-registrations
   with three different local addresses inside 300 s is not normal, was not
   visible in shorter captures, and may be either a consequence of the failed
   calls or a second fault sitting underneath them.

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
  empirically. The incoming path does parse, so what we *advertise* could in
  principle still matter there; but a device that can only receive VoLTE calls
  and not place them is not a working phone, so that is not a workaround either.

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
| `scripts/qmi/f3parse.py` | host-side decode of F3: inline `MSG_F` with arguments substituted, QSR `EXT_MSG_F` parsed to `(ss_id, line, hash, args)` rather than dropped, `ss_id` census |
| `scripts/qmi/f3probe.py` | enables F3 messaging and censuses what the modem actually emits |
| `scripts/qmi/f3reg.sh` | the registration control: F3 across a forced deregistration/re-registration, no calls, no NV writes |
| `scripts/qmi/f3in.sh` | F3 across incoming calls — the 27 ms INVITE-to-488 window, no plugin swap and no NV writes |
| `scripts/qmi/imsnv.py` | reads the IMS NV items the modem actually consults — the list harvested from its own `qpIO.c:711` output, not guessed |
| `scripts/qmi/nvsweep.sh` | compares every live `/nv/item_files/ims/*` value against all eight carrier configs and reports only the disagreements |
| `scripts/qmi/mediacfg.sh` | byte-compares `qp_ims_media_config` across configs and against EFS |
| `scripts/qmi/codeclist.sh` | the codec-list measurement: writes, captures a re-registration, restores generated values |

The device-side cost of full-spectrum capture is what forces the design: 198
KB/s with F3 and equip-1 logs, about 1 MB/s with all sixteen log masks during a
call. Both are affordable only because the handset does no parsing.

## Prior art

None. The `#sailfishos-porters` archive returns zero hits for `SDP parse
failed`, `LucentPCSF`, `qipcall_dan`, `qipcall_audio_codec_list` and
`telephone-event`. Eleven years of porter logs have not discussed this.
