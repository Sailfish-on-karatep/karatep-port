#!/bin/bash
# Enable IMS in a Qualcomm MBN modem configuration.
#
# HOST shell. Takes the generic carrier config the modem selects for a SIM that
# matches nothing (on karatep: row.mbn, "ROW_Generic_3GPP") and flips the two
# NV items that switch IMS off:
#
#     nv/item_files/ims/IMS_enable                0 -> 1
#     nv/item_files/modem/mmode/voice_domain_pref 0 (CsVoiceOnly)
#                                                   -> 3 (ImsPsVoicePreferred)
#
# voice_domain_pref = 3 keeps CS as the fallback, so a failure to register on
# IMS costs nothing.
#
# MBN MCFG files carry three SHA-256 hashes and no signature, and this modem
# only checks the hashes, so a repacked file is accepted. Repacking is done by
# sbaresearch/mbn-mcfg-tools, which round-trips karatep's configs byte for byte.
#
# See docs/rca/volte-registration-change-is-test-mode.md.
#
# Usage: patch-mbn-ims.sh <in.mbn> <out.mbn> [workdir]

set -euo pipefail

IN=${1:?usage: patch-mbn-ims.sh <in.mbn> <out.mbn> [workdir]}
OUT=${2:?usage: patch-mbn-ims.sh <in.mbn> <out.mbn> [workdir]}
WORK=${3:-/opencloud/work/telephony}

TOOLS="$WORK/mbn-mcfg-tools"
VENV="$WORK/venv"

if [ ! -d "$TOOLS" ]; then
    git clone --depth 1 https://github.com/sbaresearch/mbn-mcfg-tools.git "$TOOLS"
fi
if [ ! -x "$VENV/bin/mbn-tool" ]; then
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q "$TOOLS"
fi

DIR=$(mktemp -d "$WORK/patch-mbn.XXXXXX")
trap 'rm -rf "$DIR"' EXIT

"$VENV/bin/mbn-tool" -c "$IN"
"$VENV/bin/mbn-tool" -e "$IN" "$DIR/x"

python3 - "$DIR/x" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
base = root / 'files'
for rel, val in (('nv/item_files/ims/IMS_enable', 1),
                 ('nv/item_files/modem/mmode/voice_domain_pref', 3)):
    p = base / rel
    if not p.exists():
        sys.exit(f'{rel} is not present in this config; refusing to guess')
    b = bytearray(p.read_bytes())
    if len(b) != 2 or b[0] != 0x07:
        sys.exit(f'{rel}: unexpected encoding {b.hex(" ")}')
    print(f'{rel}: {b[1]} -> {val}')
    b[1] = val
    p.write_bytes(bytes(b))

# Bump the minor version. qcril compares the selected config's MCFG version
# against what the modem already has active and skips the reload when they
# match -- so an edit that leaves the version alone is silently ignored. The
# version is four bytes, [minor, carrier, oem, family], and appears three
# times: meta.version and the trailer's version1/version2, which must agree.
meta = json.loads((root / 'meta').read_text())


def bump(field):
    b = bytearray.fromhex(field['hex'])
    if b[0] == 0xff:
        sys.exit('minor version is already 0xff; pick another field to bump')
    b[0] += 1
    field['hex'] = ' '.join(f'{x:02x}' for x in b)
    field['ascii'] = bytes(b).decode('latin-1')
    return b[0]


old = bytearray.fromhex(meta['version']['hex'])[0]
new = bump(meta['version'])
bump(meta['trailer']['version1'])
bump(meta['trailer']['version2'])
print(f'MCFG minor version: {old} -> {new}')
(root / 'meta').write_text(json.dumps(meta, indent=2))
PY

"$VENV/bin/mbn-tool" -p "$OUT" "$DIR/x"
"$VENV/bin/mbn-tool" -c "$OUT"

# Read the result back out of the packed file rather than trusting the edit.
"$VENV/bin/mbn-tool" -e "$OUT" "$DIR/v" >/dev/null
python3 - "$DIR/v/parsed_nv_files" <<'PY'
import json, pathlib, sys
base = pathlib.Path(sys.argv[1])
for rel, want in (('nv/item_files/ims/IMS_enable', 1),
                  ('nv/item_files/modem/mmode/voice_domain_pref', 3)):
    v = json.load(open(base / rel))['fields']['Value']
    got = v['value'] if isinstance(v, dict) else v
    assert got == want, f'{rel}: read back {got}, wanted {want}'
    print(f'verified {rel} = {v}')
PY

python3 - "$DIR/v/meta" "$DIR/x/meta" <<'PY'
import json, sys
got = json.load(open(sys.argv[1]))
want = json.load(open(sys.argv[2]))
for path in (('version',), ('trailer', 'version1'), ('trailer', 'version2')):
    g = w = None
    g, w = got, want
    for k in path:
        g, w = g[k], w[k]
    assert g['hex'] == w['hex'], f'{path}: {g["hex"]} != {w["hex"]}'
    print(f'verified {".".join(path)} = {g["hex"]}')
PY

echo "wrote $OUT"
