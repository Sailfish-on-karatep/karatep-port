#!/usr/bin/python3
#
# Dump the IMS NV items the modem actually consults.
#
# The list is not guessed: it was harvested from the modem's own F3 output.
# qpIO.c:711 logs every EFS read as
#
#   qpDplIODeviceGetItem: NV_file[/nv/item_files/ims/<name>], MCFG SubsId: 0
#
# so a capture with message masks raised enumerates the entire set the firmware
# cares about on this build -- 70-odd items -- including several that no public
# list of Qualcomm NV items mentions.
#
# Reading them matters because the media-related ones are the only host-side
# lever left: the modem aborts outgoing calls without parsing the answer SDP and
# refuses incoming offers just after tokenising them, and both decisions are
# taken inside QSR-hashed records that cannot be read without the vendor string
# database. The NV values are the inputs to those decisions and *are* readable.
#
# Read-only. Nothing here writes; use efswrite.py for that, which refuses any
# length change.

import sys

sys.path.insert(0, "/home/defaultuser")
from diagefs import Diag, read_file  # noqa: E402

BASE = "/nv/item_files/ims/"

# Harvested from qpIO.c:711 across the outgoing-call and incoming-call captures,
# ordered so the media and QoS items -- the ones bearing on the failure -- come
# first rather than in the arbitrary order the modem happened to read them.
ITEMS = [
    # media negotiation
    "qipcall_audio_codec_list",
    "qipcall_evs_codec_config",
    "qipcall_codec_mode_set",
    "qipcall_codec_mode_set_amr_wb",
    "qipcall_octet_aligned_mode_amr_nb",
    "qipcall_octet_aligned_mode_amr_wb",
    "ims_scr_amr_nb_enabled",
    "ims_scr_amr_wb_enabled",
    "voip_prfrd_codec",
    "MediaProfiles",
    "qp_ims_media_config",
    "qipcall_session_level_media_bw_enabled",
    "qipcall_enable_hd_voice",
    # preconditions and QoS -- the outgoing call is cancelled 36 ms before the
    # dedicated bearer the network is setting up actually arrives
    "qipcall_precondition_enable",
    "qipcall_qos_enabled",
    "qipcall_qos_reservation_timer",
    # call control
    "qipcall_config_items",
    "ims_operation_mode",
    "qipcall_domain_selection_enable",
    "qipcall_invite_retry_counter",
    "qipcall_ringing_timer",
    "qipcall_ringback_timer",
    "qipcall_rtp_link_aliveness_timer",
    "qipcall_rtcp_link_aliveness_timer",
    "qipcall_rtcp_reporting_interval",
    "qipcall_dan_enable",
    "qipcall_dan_needed",
    # registration / stack
    "IMS_enable",
    "ims_hybrid_enable",
    "qp_ims_config",
    "qp_ims_reg_config",
    "qp_ims_voip_config",
    "qp_ims_param_config",
    "qp_ims_sip_extended_0_config",
    "ims_user_agent",
    "qp_ims_vt_4G_media_capability",
    "qp_ims_vs_4G_media_capability",
]


def show(d, name):
    path = BASE + name
    try:
        data, err = read_file(d, path)
    except Exception as exc:                       # noqa: BLE001
        print("%-42s ERROR %s" % (name, exc))
        return
    if data is None:
        print("%-42s <absent: %s>" % (name, err))
        return
    # Trailing NUL padding is the norm for the string-valued items; show the
    # true length but strip the padding from the rendering so a 128-byte codec
    # list does not bury the screen.
    trimmed = data.rstrip(b"\x00")
    txt = trimmed.decode("ascii", "replace") if trimmed else ""
    printable = txt.isprintable() if txt else False
    body = ('"%s"' % txt) if printable else trimmed.hex()
    print("%-42s %4d B  %s" % (name, len(data), body[:120] or "<all zero>"))


def main():
    d = Diag()
    for name in (sys.argv[1:] or ITEMS):
        show(d, name)
    d.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
