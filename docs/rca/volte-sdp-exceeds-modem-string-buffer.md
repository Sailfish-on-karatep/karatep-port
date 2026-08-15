# VoLTE: a 50-byte string truncation fires only while parsing BSNL's answer

**Status:** narrowed to one reproducible signal; the offending string and the
causal link to the abort are **not** established. An earlier revision of this
document claimed both. See *What this does and does not establish*.

VoLTE on karatep registers correctly, dials correctly, and dies during media
negotiation. Outgoing calls end ~35 ms after BSNL's `183 Session Progress` with
QMI end cause 373 (media class) and the text `SDP parse failed`. Incoming calls
are refused with `488 Not Acceptable Here` 28 ms after the INVITE.

The one signal that distinguishes the failing window from everything else is a
50-byte string truncation inside the modem, reported twice per call and only
while BSNL's answer is being processed. Which string overflows, and whether the
overflow is what kills the call, are both still open.

Identifiers below are redacted: `4048001XXXXXXXX` is the private user identity,
`+9194873XXXXX` the MSISDN, `+9194873YYYYY` the called party.

Baseband is `MSM8937.LA.2.0-00440-STD.PROD-1`, November 2017 — the only one
Lenovo ever shipped for this device.

## The finding

With F3 debug messaging enabled (see *The instrument was wrong twice*, below),
the modem says it directly:

```
qvp_app_oa_api.c:2747   len of str printed exceeded dst str len50
```

`qvp_app_oa_api.c` is the offer/answer API. In a 420-second capture containing
two VoLTE call attempts, that message fires **exactly four times** — twice per
call, both times while processing BSNL's `183 Session Progress`, and nowhere
else. Every other call site in that file fires at INVITE-build time:

| line | message | when it fires |
|---|---|---|
| 523 | `len of str printed len3000` | INVITE build |
| 904 | `len of str printed len44` | INVITE build |
| 1043 | `len of str printed len204` | INVITE build |
| 1079 | `len of str printed len204` | INVITE build |
| 1117 | `len of str printed exceeded len204` | INVITE build |
| 1960 | `len of str printed len24` | INVITE build (×3) |
| **2747** | **`len of str printed exceeded dst str len50`** | **answer parse only (×2 per call)** |

## What this does and does not establish

**An earlier revision of this document claimed the truncated strings were the
SDP `o=` origin FQDN (60 bytes) and the `a=fmtp:97` parameter value (62 bytes),
on the grounds that BSNL's answer contains exactly two fields over 50 bytes and
the message fires exactly twice. That was reasoning from a coincidence of
counts, and the surrounding F3 context does not support it.**

The messages bracketing each fire are dialog bookkeeping, not media parsing:

```
qipcalldialog.c:5361  [incoming_msg] [call_id: ...] [dialog_id: ...]
qipcalldialog.c:4670  [update_dialog_id] [TEMP_OUTGOING_DIALOG ?= TEMP_OUTGOING_DIALOG]
qvp_app_oa_api.c:2747 len of str printed exceeded dst str len50      <---
qipcalldialog.c:5331  [updating call_ptr with dialog_id: <call-id><local-tag><remote-tag>]
```

`qipcalldialog.c:5331` concatenates call-id, local tag and remote tag into one
dialog identifier. With BSNL's tag values that is **92 bytes**:

| component | bytes |
|---|---|
| call-id | 34 |
| local tag | 10 |
| BSNL remote tag (`…-gm-po-lucentPCSF-…`) | 48 |
| **concatenated dialog id** | **92** |

`qvp_app_oa_api.c` is also not obviously an offer/answer module: its other call
sites report destination sizes of 3000, 204, 44 and 24 bytes, which is the
signature of a generic string-printing helper used throughout, not of SDP code.
"OA" may not mean offer/answer at all.

So there are at least three plausible over-length candidates at that moment —
the dialog id (92), the `o=` FQDN (60), the `fmtp` value (62) — and the context
points at the first, not the two this document originally named.

**The causal link is also unproven.** Both truncations fire at 44.961 and
44.970. The PRACK is built and sent at 44.982, and only then, at 44.983, does
the modem begin building the CANCEL. A whole successful PRACK transaction sits
between the truncation and the abort. And `qvp_app_oa_api.c:1117` reports the
same kind of overflow against a 204-byte buffer at INVITE-build time and is
harmless. Truncation here is suggestive, not established as fatal.

### What is solid

- Line 2747 fires **only** in the window where BSNL's answer is processed,
  exactly twice per call, and nowhere else in 420 seconds.
- Something in that exchange exceeds a 50-byte destination buffer in the modem.
- The failure is symmetric: incoming calls are refused `488 Not Acceptable
  Here`, which rules out everything specific to the answer path.
- The abort decision itself is taken **after** the PRACK is sent, in the 1 ms
  between 44.982 and 44.983, and nothing in the plain-text F3 stream in that gap
  concerns media. Some messages in that window have unresolved filenames and
  integer arguments that look like QSR hashes — those are the next thing to
  decode.

## It fails symmetrically, which is what identifies it

An incoming VoLTE call — the first ever tested on this port — is refused with
`488 Not Acceptable Here` 28 ms after the INVITE. `488` is precisely "your SDP
is not acceptable to me". BSNL's *offer* carries over-length fields of its own —
the `o=` FQDN at 60 bytes and an `a=fmtp:96` value at 73 — and its tag values
are the same shape as in the answer, so both the SDP and the dialog-string
candidates are present in this direction too.

That symmetry is the strongest single piece of evidence, and it is about *where*
the fault is, not what it is. It rules out every hypothesis that lived in the
answer-specific path — PRACK handling, precondition and UPDATE sequencing, the
183 early-media flow — because the incoming rejection involves none of them.

**The incoming case is also the cleaner experiment, and it has not yet been run
with F3 enabled.** INVITE to 488 is 28 ms with no PRACK, no preconditions and no
CANCEL, so whatever refuses the SDP must log inside that window with far less
noise around it. That is the next measurement worth taking.

It also eliminated two suspects outright. The incoming offer contains **no**
`telephone-event/8000/1` channel count and **no** duplicate `c=` line — two
things an earlier revision of this investigation had flagged as the remaining
oddities — and was rejected anyway.

## The instrument was wrong twice

Both errors have the same shape: a real measurement of the wrong thing, recorded
as a property of the modem.

**1. Log masks were only ever raised for equipment id 1.** Every earlier
conclusion — "72,323 frames, 93 distinct codes, no failure-specific code" — was
drawn from one sixteenth of the modem's logging. Raising all sixteen masks was
avoided because scanning frames in Python on the handset cost a load average
above 4. Writing the driver's batches to disk untouched and decoding on the host
removes that cost entirely: the same window then yields **739,497 packets and
444 distinct codes across equipment ids 1, 4, 5, 7 and 11**. (The honest result
of that sweep: even at full spectrum, no log code is failure-specific. The
question is now closed properly rather than by budget.)

**2. F3 message masks were never set at all.** This document's parent RCA states
in several places that this modem "emits no F3 debug messaging at all". That is
wrong. Log masks and message masks are separate mechanisms with separate
commands, and only the log masks had ever been raised. A packet-type census of
the full-spectrum capture finds only `0x10` log packets and nine QSherlock
frames — no `0x79`, `0x92`, `0x93` or `0x99` — because nothing had asked for
them.

Setting them takes one command, whose format comes from the device's own kernel
(`drivers/char/diag`):

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

With that sent, the modem emits **34,758 F3 messages in 20 seconds** at about
115 KB/s — and they arrive as `EXT_MSG_F` (`0x92`) and `MSG_F` (`0x79`), i.e.
whole format strings with their arguments, not QSR hashes. No string database is
needed to read them. The modem is extremely talkative; it had simply never been
asked to speak.

`MSG_F` / `EXT_MSG_F` layout, for anyone decoding these:

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

- **"The modem is packed; `strings` finds nothing."** The SDP protocol literals
  genuinely are absent, but `modem.b24` is the QSR string table and is fully
  readable — it holds `qipcallsdp.c`, `sipConnection.cpp`, `qvp_app_oa_api.c`
  and the rest. Only the wrong strings had been searched for.

- **The `b=AS` bandwidth hypothesis was wrong in its mechanism.** `modem.b24`
  does contain
  `qipcallsdp.c:qipcallsdp_should_AS_validation_be_ignored: ignore AS validation
  for RJIL` — a real, hardcoded carrier bypass for Reliance Jio — and it is
  genuinely suggestive. But the observed
  `Bandwidth : AS 0` / `Max Bandwidth : AS 41` pair fires at **INVITE-build**
  time: it is the modem computing *our own* `b=AS:41`, not validating BSNL's
  answer. The AS bypass is a different carrier accommodation and is not what
  kills these calls. The string hunt reached the right module for the wrong
  reason.

- **Narrowing the codec offer changed nothing.** `qipcall_audio_codec_list` is
  ASCII, semicolon-separated, NUL-padded to 128 bytes — `rjil.mbn` ships
  `AMR_WB_OA;AMR_WB_BE;AMR_OA;AMR_BE`. Setting it to `AMR_OA;AMR_BE` moved our
  offer from `RTP/AVP 99 97 105 96` to `RTP/AVP 103 97 96`, dropping AMR-WB and
  `telephone-event/16000` as intended. BSNL's answer was structurally identical
  either way (only the RTP port differs) and the call failed the same way.

## Next measurements

In priority order, each of which can falsify something:

1. **F3 during an incoming call.** 28 ms, no PRACK, no preconditions. The
   cleanest window in which to see what refuses the SDP.
2. **Resolve the QSR-hashed messages in the 1 ms before the CANCEL.** Some F3
   records in that gap decode to garbage filenames with integer arguments that
   look like hashes; `modem.b24` is the string table they resolve against.
   `scripts/qmi/readmsgtable.py` is the existing starting point.
3. **Does line 2747 fire outside calls?** BSNL's REGISTER `200 OK` carries tag
   values of the same length. If the truncation happens during registration too
   — which succeeds — it is bookkeeping noise and not the fault at all. This is
   the cheapest test of the whole hypothesis and needs no call, only F3 enabled
   across a re-registration.

## Why a firmware fix is not available

- **The buffer is a compile-time constant in signed modem firmware.** MSM8937
  PIL images are authenticated by TZ against a certificate chain rooted in OEM
  fuses; altering `modem.b*` invalidates the signed hash segment and the image
  will not load. Lenovo shipped no later baseband for this device, and another
  vendor's newer MSM8937 image will not authenticate against these fuses.
- **No host-side lever has ever touched it.** Fourteen NV alignments, five media
  booleans, a codec narrowing, a mode-set change and a full carrier config swap
  all failed to move the symptom.
- **If the fault is in the SDP fields, there may still be a workaround**, since
  BSNL's `a=fmtp` is an *answer to our offer*: their own incoming offer lists
  `PCMA/8000`, which carries no `fmtp` at all. Forcing a G.711 offer via
  `qipcall_audio_codec_list` would remove that field from their answer and leave
  only the `o=` FQDN over-length — which, being informational in RFC 4566, may
  be survivable. If the fault is in the dialog id instead, nothing we send
  changes it. Note the codec-list token names are not plaintext anywhere in the
  image, so which tokens the parser accepts beyond `AMR_OA`/`AMR_BE` is unknown
  and would have to be found by trial.

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
| `scripts/qmi/sdprawparse.py` | host-side decode: HDLC, log packets, F3 messages with arguments substituted, SIP reassembly, per-window frame dumps |
| `scripts/qmi/f3probe.py` | enables F3 messaging and censuses what the modem actually emits |

The device-side cost of full-spectrum capture is what forces the design: 419
KB/s with F3 and equip-1 logs, about 1 MB/s with all sixteen log masks during a
call. Both are affordable only because the handset does no parsing.

## Prior art

None. The `#sailfishos-porters` archive returns zero hits for `SDP parse
failed`, `LucentPCSF`, `qipcall_dan`, `qipcall_audio_codec_list` and
`telephone-event`. Eleven years of porter logs have not discussed this.
