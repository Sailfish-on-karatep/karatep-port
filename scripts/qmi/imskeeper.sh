#!/bin/sh
# Keep the IMS context up.
#
# ofono activates context3 on request and never re-activates it when the bearer
# goes away. A CS call causes CSFB to 3G, which tears the IMS PDN down and
# deregisters IMS, and nothing brings it back -- so the next call has no IMS
# either and falls back again. That is a self-sustaining loop, and it is why the
# registration never appeared to "hold": it was not unstable, its bearer was
# being removed and not replaced.
#
# This is a test harness, not the fix. The fix belongs in ofono (see task #22);
# this exists to hold the variable still long enough to find out whether a call
# placed while IMS is genuinely registered goes over IMS.
CTX=/ril_0/context3
while true; do
  a=$(dbus-send --system --print-reply --dest=org.ofono $CTX \
      org.ofono.ConnectionContext.GetProperties 2>/dev/null | \
      grep -A1 '"Active"' | grep -oE "true|false" | head -1)
  if [ "$a" != "true" ]; then
    echo "$(date -u '+%H:%M:%S') bearer down, reactivating"
    dbus-send --system --print-reply --dest=org.ofono $CTX \
      org.ofono.ConnectionContext.SetProperty string:Active variant:boolean:true \
      >/dev/null 2>&1
  fi
  sleep 5
done
