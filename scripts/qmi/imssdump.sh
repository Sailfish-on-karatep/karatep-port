#!/bin/sh
# Dump the imss messages that the sweep found the modem willing to answer.
#
# 0x8f/0x90 -- the enable-config pair qcril uses -- fail with INTERNAL, but 26
# other messages return QMI_RESULT_SUCCESS. If one of those is an alternate view
# of the same state, it says what the modem thinks its IMS configuration is,
# which is the one thing the failing getters will not tell us.
#
# Note: "port:0x37" is not the way to ask for individual messages on the default
# port -- that branch only runs when the port differs. Use the get subcommand.
/usr/bin/python3 /home/defaultuser/qmiims.py get \
  0x26 0x28 0x29 0x2a 0x34 0x36 0x37 0x39 0x3d 0x48 0x54 0x5e 2>&1
