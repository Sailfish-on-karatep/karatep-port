#!/bin/bash
# Copy configuration items from one Qualcomm MBN config into another.
#
# HOST shell. Written for the VoLTE work, where karatep's generic ROW config
# carries one IMS item and the Jio config carries fifty, and the question is
# which of them the modem's IMS stack actually needs. Being able to import an
# arbitrary subset is what makes that bisectable.
#
# The donor's nv_items entry is copied verbatim rather than synthesised, so
# item attributes come from the config that is known to work.
#
# Adding an item means adding to nv_items as well as writing under files/:
# mbn-tool's packer iterates nv_items and silently drops a file with no entry.
#
# The MCFG minor version is bumped by one relative to <in.mbn>, because qcril
# skips a config whose version matches what the modem already has active. When
# iterating, feed the previous output back in so the version keeps climbing --
# or set MBN_MINOR to an explicit value, which is what a bisect wants: each
# round is built from the same baseline but must still outrank the config the
# modem is currently running.
#
# See docs/rca/volte-registration-change-is-test-mode.md.
#
# Usage: mbn-import-items.sh <in.mbn> <out.mbn> <donor.mbn> <items-file> [workdir]
#
#   items-file: one path per line, relative to the extracted files/ directory,
#               e.g. nv/item_files/ims/qp_ims_config. Blank lines and lines
#               starting with # are ignored.

set -euo pipefail

USAGE='usage: mbn-import-items.sh <in.mbn> <out.mbn> <donor.mbn> <items-file> [workdir]'
IN=${1:?$USAGE}
OUT=${2:?$USAGE}
DONOR=${3:?$USAGE}
ITEMS=${4:?$USAGE}
WORK=${5:-/opencloud/work/telephony}

TOOLS="$WORK/mbn-mcfg-tools"
VENV="$WORK/venv"

if [ ! -d "$TOOLS" ]; then
    git clone --depth 1 https://github.com/sbaresearch/mbn-mcfg-tools.git "$TOOLS"
fi
if [ ! -x "$VENV/bin/mbn-tool" ]; then
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q "$TOOLS"
fi

DIR=$(mktemp -d "$WORK/mbn-import.XXXXXX")
trap 'rm -rf "$DIR"' EXIT

"$VENV/bin/mbn-tool" -c "$IN"
"$VENV/bin/mbn-tool" -e "$IN" "$DIR/x" >/dev/null
"$VENV/bin/mbn-tool" -e "$DONOR" "$DIR/donor" >/dev/null

python3 - "$DIR/x" "$DIR/donor" "$ITEMS" "${MBN_MINOR-}" <<'PY'
import json, pathlib, shutil, sys

root, donor, items_file = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
forced_minor = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] else None

wanted = [ln.strip() for ln in open(items_file)]
wanted = [w for w in wanted if w and not w.startswith('#')]
if not wanted:
    sys.exit('items file is empty')

donor_items = json.loads((donor / 'nv_items').read_text())
by_path = {}
for it in donor_items:
    fn = it.get('filename')
    if fn:
        by_path[fn['ascii'].rstrip('\x00').lstrip('/')] = it

items = json.loads((root / 'nv_items').read_text())
have = {it['filename']['ascii'].rstrip('\x00').lstrip('/')
        for it in items if it.get('filename')}

added, skipped = [], []
for rel in wanted:
    if rel in have:
        skipped.append(rel)
        continue
    if rel not in by_path:
        sys.exit(f'donor has no item {rel}')
    src = donor / 'files' / rel
    if not src.exists():
        sys.exit(f'donor item {rel} has no data under files/')
    dst = root / 'files' / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    items.append(by_path[rel])
    added.append(rel)

(root / 'nv_items').write_text(json.dumps(items, indent=2))

# Bump the minor version so qcril does not skip the reload.
meta = json.loads((root / 'meta').read_text())
old = None
new_minor = None
for f in (meta['version'], meta['trailer']['version1'], meta['trailer']['version2']):
    b = bytearray.fromhex(f['hex'])
    if old is None:
        old = b[0]
    if forced_minor is not None:
        if not 0 <= forced_minor <= 0xff:
            sys.exit('MBN_MINOR must be 0..255')
        b[0] = forced_minor
    else:
        if b[0] == 0xff:
            sys.exit('minor version is already 0xff; set MBN_MINOR explicitly')
        b[0] += 1
    new_minor = b[0]
    f['hex'] = ' '.join(f'{x:02x}' for x in b)
    f['ascii'] = bytes(b).decode('latin-1')
(root / 'meta').write_text(json.dumps(meta, indent=2))

print(f'imported {len(added)} item(s), skipped {len(skipped)} already present')
for rel in added:
    print(f'  + {rel}')
for rel in skipped:
    print(f'  = {rel} (already in target, left alone)')
print(f'MCFG minor version: {old} -> {new_minor}')
json.dump(added, open(root / '.imported', 'w'))
PY

"$VENV/bin/mbn-tool" -p "$OUT" "$DIR/x"
"$VENV/bin/mbn-tool" -c "$OUT"

# Read it back out rather than trusting the write.
"$VENV/bin/mbn-tool" -e "$OUT" "$DIR/v" >/dev/null
python3 - "$DIR/x" "$DIR/v" <<'PY'
import filecmp, json, pathlib, sys
src, got = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
added = json.load(open(src / '.imported'))
for rel in added:
    a, b = src / 'files' / rel, got / 'files' / rel
    if not b.exists():
        sys.exit(f'{rel} did not survive the pack')
    if not filecmp.cmp(a, b, shallow=False):
        sys.exit(f'{rel} changed during the pack')
for path in (('version',), ('trailer', 'version1'), ('trailer', 'version2')):
    g, w = json.load(open(got / 'meta')), json.load(open(src / 'meta'))
    for k in path:
        g, w = g[k], w[k]
    if g['hex'] != w['hex']:
        sys.exit(f'{".".join(path)}: {g["hex"]} != {w["hex"]}')
print(f'verified {len(added)} imported item(s) and the version survive the pack')
PY

echo "wrote $OUT"
