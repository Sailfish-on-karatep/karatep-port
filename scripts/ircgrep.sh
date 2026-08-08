#!/bin/bash
# Grep the #sailfishos-porters IRC archive (https://piggz.co.uk/sailfishos-porters-archive/)
# and print the matching lines as plain text.
#
#   ircgrep.sh 'controller init failed'
#
# The archive is a plain substring search over eleven years of logs, exposed only as an HTML
# form, so this POSTs the query and strips the markup out of the <pre> block it returns.
# Search for symptoms and identifiers, not prose -- there is no tokenisation or stemming.
# Zero hits is a real answer: it means nobody has discussed it and there is no prior art.
set -u

if [ $# -lt 1 ]; then
    echo "usage: ${0##*/} <search text>" >&2
    exit 2
fi

timeout 180 curl -s -X POST \
    --data-urlencode "inputSearchText=$1" \
    -d submit=submit \
    https://piggz.co.uk/sailfishos-porters-archive/index.php \
| sed -n '/<pre>/,/<\/pre>/p' \
| sed -e 's/<br>/\n/g' -e 's/<[^>]*>//g' \
| sed -e 's/&quot;/"/g' -e 's/&amp;/\&/g' -e 's/&lt;/</g' -e 's/&gt;/>/g' -e "s/&#039;/'/g" \
| grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}\.txt:'   # drop page chrome; no <\/pre> is emitted on zero hits
