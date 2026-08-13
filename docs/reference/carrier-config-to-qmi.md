# Reconstructing the carrier database, and how far it is worth going

Android configures telephony from a per-operator database that this port has no
equivalent of. The question is whether it can be extracted and rebuilt here. It
can, but not in the shape the question implies, and the useful part is much
smaller than the whole.

## AOSP has no BSNL config

`packages/apps/CarrierConfig/assets/` in the LineageOS tree holds 298 files, 26
of them keyed by MCCMNC. **None mentions 40480 or BSNL.** So there is no upstream
per-operator entry to mine for this network. What the reference handset reports
is the framework defaults -- `CarrierConfigManager` defines 407 keys -- merged
with MIUI and Qualcomm overlays and anything the carrier provisioned.

Which means the dump already taken *is* the extraction, and it is a better
artifact than any source file would have been: it is the effective, merged,
as-running configuration from a handset on which VoLTE works. It is saved as
`bsnl-carrier-config-android13.txt`, 1499 key/value lines.

## Most of it does not reconstruct into anything

The majority of those 407 keys configure Android framework behaviour -- dialer
UI, provisioning flows, roaming policy, emergency-number handling, RCS. They have
no counterpart in ofono and none in the modem. Copying them somewhere would
achieve nothing.

## The IMS subset maps almost one-to-one onto QMI config items

This is the part worth having. The modem's own config-item table, recovered in
`scripts/qmi/cfgmap.py`, carries direct counterparts:

| Android carrier-config key | BSNL value | modem config item | legacy set/get |
|---|---|---|---|
| `ims.sip_timer_t1_millis_int` | 2000 | `SIP_TIMER_T1` (7) | 0x20 / 0x25 |
| `ims.sip_timer_t2_millis_int` | 16000 | `SIP_TIMER_T2` (8) | 0x20 / 0x25 |
| `ims.sip_timer_t4_millis_int` | 17000 | `SIP_TIMER_T4` (10) | 0x20 / 0x25 |
| `ims.sip_timer_b_millis_int` | 128000 | `SIP_TIMER_TB_VALUE` (12) | 0x20 / 0x25 |
| `ims.sip_timer_d_millis_int` | 130000 | `SIP_TIMER_TD_VALUE` (13) | 0x20 / 0x25 |
| `ims.sip_timer_f_millis_int` | 128000 | `SIP_TIMER_TF` (9) | 0x20 / 0x25 |
| `ims.sip_timer_h_millis_int` | 128000 | `SIP_TIMER_TH_VALUE` (16) | 0x20 / 0x25 |
| `ims.sip_timer_j_millis_int` | 128000 | `SIP_TIMER_TJ` (18) | 0x20 / 0x25 |
| `ims.registration_retry_base_timer_millis_int` | 30000 | `REG_MGR_EXTENDED_REG_RETRY_BASE_TIME` (22) | 0x44 / 0x45 |
| `ims.registration_retry_max_timer_millis_int` | 1800000 | `REG_MGR_EXTENDED_REG_RETRY_MAX_TIME` (23) | 0x44 / 0x45 |
| `ims.sip_server_port_number_int` | 5060 | `REG_MGR_CONFIG_REG_MGR_PRIMARY_CSCF` (58) | 0x21 / 0x26 |
| `imssms.sms_over_ims_format_int` | 0 (3GPP) | `SMS_FORMAT` (31) | 0x22 / 0x27 |
| `imssms.sms_over_ims_supported_bool` | true | `SMS_OVER_IP` (32) | 0x22 / 0x27 |
| — | — | `SMS_PSI` (33) | 0x22 / 0x27 |

Not directly mapped, but modem-settable through the carrier config (`.mbn`)
rather than through QMI:

| key | BSNL value | where it lives here |
|---|---|---|
| `ims.ipv4_sip_mtu_size_cellular_int` | 1500 | `imss 0x39`, currently **1300** |
| `ims.sip_over_ipsec_enabled_bool` | true | ESP confirmed on the bearer |
| `ims.ipsec_authentication_algorithms_int_array` | [0, 1] | `qp_ims_sip_extended_0_config` |
| `ims.ipsec_encryption_algorithms_int_array` | [0, 1, 2] | `qp_ims_sip_extended_0_config` |

**Units differ and must not be copied across naively.** Android states these in
milliseconds; the modem's `imss 0x29` reports T1 as 45 and T2 as 90 against
Android's 2000 and 16000, so the modem is plainly not using milliseconds. Each
value needs its unit established before it is written.

## The shape worth building

Not a reimplementation of Android's database -- a small per-operator IMS profile,
keyed by MCC/MNC, carrying the dozen or so values above, applied at boot by the
mechanism that already exists for exactly this: `karatep-modem-config.py`, which
patches the carrier config and restages it. `scripts/mcfg/patchitem.py` already
does the patching half.

That is a tractable piece of work, it is reusable by other Sailfish ports on
Qualcomm hardware, and it addresses the structural gap this investigation kept
running into -- ofono has no operator database, so every value it gets wrong has
to be found by hand.
