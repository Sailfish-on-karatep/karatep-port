# VoLTE: BSNL's SDP carries fields longer than a 50-byte buffer in the modem

**Status:** root-caused, not fixable on this baseband.

VoLTE on karatep registers correctly, dials correctly, and dies during media
negotiation. Outgoing calls end ~35 ms after BSNL's `183 Session Progress` with
QMI end cause 373 (media class) and the text `SDP parse failed`. Incoming calls
are refused with `488 Not Acceptable Here` 28 ms after the INVITE.

The cause is a fixed-size string buffer in the modem's offer/answer API. BSNL's
SDP contains two fields longer than it, in both directions.

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

Two truncation reports per call. BSNL's answer contains exactly two fields
longer than 50 bytes:

```
o=LucentPCSF 330616592 330616592 IN IP4 imsgroup-322-0000002.tns01.ims.mnc080.mcc404.3gppnetwork.org
a=fmtp:97 mode-set=0,2,4,7; mode-change-period=2; mode-change-neighbor=1
```

| field | value | bytes |
|---|---|---|
| `o=` origin FQDN | `imsgroup-322-0000002.tns01.ims.mnc080.mcc404.3gppnetwork.org` | **60** |
| `a=fmtp:97` value | `mode-set=0,2,4,7; mode-change-period=2; mode-change-neighbor=1` | **62** |
| `a=rtpmap:97` value | `AMR/8000/1` | 10 |
| `c=` value | `IN IP4 61.2.220.148` | 19 |
| `a=fmtp:96` value | `0-15` | 4 |

The modem truncates both, the SDP becomes unusable, and the call is torn down.

Both fields are legal SDP. RFC 4566 explicitly permits an FQDN as the `o=`
unicast-address, and places no length limit on an `fmtp` parameter list.

## It fails symmetrically, which is what identifies it

An incoming VoLTE call — the first ever tested on this port — is refused with
`488 Not Acceptable Here` 28 ms after the INVITE. `488` is precisely "your SDP
is not acceptable to me", and BSNL's *offer* carries the same two over-length
fields:

| field | bytes |
|---|---|
| `o=` origin FQDN | 60 |
| `a=fmtp:96` value (`…; max-red=0`) | 73 |

That symmetry is the strongest single piece of evidence. It rules out every
hypothesis that lived in the answer-specific path — PRACK handling, precondition
and UPDATE sequencing, the 183 early-media flow — because the incoming rejection
involves none of them. The same buffer, in the same module, in both directions.

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

## Caveats

Causation is inferred from exclusivity and an exact count match, not proven.
Line 2747 fires only against BSNL's SDP, exactly as many times as there are
over-length fields, immediately before teardown — but note that
`qvp_app_oa_api.c:1117` reports a similar overflow against a 204-byte buffer at
INVITE-build time and is **not** fatal. An "exceeded" message alone does not
imply the call dies.

Which of the two truncations is the fatal one is not established. The `o=`
unicast-address is informational in RFC 4566 — media routing comes from `c=`,
which is short — so it is plausible that only the `fmtp` truncation matters.
That distinction decides whether any workaround exists at all (see below).

## Why this is not fixable here

- **Both offending fields are BSNL's to choose.** The `o=` origin is their
  Lucent P-CSCF's session identity; the `fmtp` is their AMR parameter set. We
  send nothing that influences either.
- **The buffer is a compile-time constant in signed modem firmware.** MSM8937
  PIL images are authenticated by TZ against a certificate chain rooted in OEM
  fuses; altering `modem.b*` invalidates the signed hash segment and the image
  will not load. Lenovo shipped no later baseband for this device, and another
  vendor's newer MSM8937 image will not authenticate against these fuses.
- **Nothing on the host side reaches it.** This is why fourteen NV alignments,
  five media booleans, a codec narrowing, a mode-set change and a full carrier
  config swap all failed to move the symptom: none of them were ever in contact
  with the defect.

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
