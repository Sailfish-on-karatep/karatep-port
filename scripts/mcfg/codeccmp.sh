#!/bin/sh
# Compare the IMS media/codec items across every carrier config on the handset.
#
# The MO call now leaves as a real VoLTE call (QMI call_type 2) and dies with
# "SDP parse failed". That string is not in any AP-side binary -- checked across
# /vendor/lib64, /vendor/lib and /vendor/bin -- so it comes from the modem's own
# IMS stack and cannot be traced by disassembly here.
#
# What can be checked is the configuration the SDP is built from. We are running
# Reliance Jio's commercial config retargeted at BSNL, and two of its items look
# suspicious for SDP generation:
#
#   qipcall_codec_mode_set         = 00000000
#   qipcall_codec_mode_set_amr_wb  = 00000000
#
# An AMR mode-set of zero means no modes offered, which would produce an
# a=fmtp line with an empty mode-set. Whether that is actually wrong is the
# question: 0 may equally mean "use the default". Comparing against the twenty
# or so other commercial configs shipped in this modem image answers it -- if
# every operator ships 0 it is the normal default, and if Jio is the outlier
# then it is a real candidate.
SRC=/vendor/firmware_mnt/image
ITEMS="qipcall_audio_codec_list qipcall_codec_mode_set qipcall_codec_mode_set_amr_wb qipcall_precondition_enable qipcall_qos_enabled"

for f in $SRC/*.mbn; do
  n=$(basename "$f")
  out=$(/usr/bin/python3 /home/defaultuser/mbnitems.py "$f" 2>/dev/null | \
        grep -E "qipcall_codec_mode_set |qipcall_codec_mode_set_amr_wb|qipcall_audio_codec_list|qipcall_precondition_enable|qipcall_qos_enabled")
  [ -z "$out" ] && continue
  echo "== $n"
  echo "$out" | sed 's/^ */  /' | cut -c1-118
done
echo DONE-CODEC
