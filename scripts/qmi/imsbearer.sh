#!/bin/sh
# Bring the IMS PDN up, then re-run the imss sweep with the bearer established.
#
# The sweep splits imss cleanly: every settings getter answers, every runtime
# query returns INTERNAL. If those runtime queries only work once the IMS bearer
# exists, then the modem's IMS task waits on the PDN and the whole problem
# reduces to task #22 -- ofono never activates context3 by itself.
CTX=/ril_0/context3
dbus-send --system --print-reply --dest=org.ofono $CTX \
  org.ofono.ConnectionContext.SetProperty string:Active variant:boolean:true 2>&1 | tail -2
i=0; while [ $i -lt 10 ]; do sleep 1; i=$((i + 1)); done
echo "== context3 =="
dbus-send --system --print-reply --dest=org.ofono $CTX \
  org.ofono.ConnectionContext.GetProperties 2>&1 | \
  grep -A2 -E '"Active"|"Interface"|"Address"' | grep -E "boolean|string" | head -8
echo
echo "== imss sweep with the bearer up =="
/usr/bin/python3 /home/defaultuser/qmiims.py sweep 0x20 0xa0 2>&1 | tail -12
echo
echo "== imsa service status =="
/usr/bin/python3 /home/defaultuser/qmiims.py port:0x39 0x22 2>&1 | head -20
