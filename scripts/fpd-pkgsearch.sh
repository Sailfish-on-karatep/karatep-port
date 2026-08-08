#!/bin/bash
source /opencloud/hadk.env
T="${VENDOR}-${DEVICE}-${PORT_ARCH}"
for p in jolla-calendar jolla-email jolla-notes jolla-calculator jolla-mediaplayer \
         sailfish-office jolla-weather jolla-maps jolla-notes-all-translations-pack; do
    r=$(sb2 -t "$T" -m sdk-install -R zypper --non-interactive se --match-exact "$p" 2>/dev/null | grep -cE "^ *\| *$p *\|")
    [ "$r" -gt 0 ] && echo "OK       $p" || echo "NOTFOUND $p"
done
