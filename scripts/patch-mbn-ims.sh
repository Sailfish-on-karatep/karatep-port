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
# With --ims-profile it also copies a data profile in from a donor config, so
# the modem has an IMS APN to bring a PDN up on. On karatep the donor is
# rjil.mbn's Profile2, which is a plain "ims" APN with pdp_type IPv4v6 and
# everything else zero -- nothing carrier-specific. Adding an item means adding
# an entry to nv_items as well as a file under files/: the packer iterates
# nv_items, so a file with no entry is silently dropped.
#
# See docs/rca/volte-registration-change-is-test-mode.md.
#
# Usage: patch-mbn-ims.sh [--ims-profile <donor.mbn>[:ProfileN]] \
#                         <in.mbn> <out.mbn> [workdir]

set -euo pipefail

USAGE='usage: patch-mbn-ims.sh [--ims-profile <donor.mbn>[:ProfileN]] <in.mbn> <out.mbn> [workdir]'

DONOR=
if [ "${1-}" = "--ims-profile" ]; then
    DONOR=${2:?$USAGE}
    shift 2
fi

IN=${1:?$USAGE}
OUT=${2:?$USAGE}
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

if [ -n "$DONOR" ]; then
    DONOR_MBN=${DONOR%%:*}
    DONOR_PROFILE=${DONOR#*:}
    [ "$DONOR_PROFILE" = "$DONOR_MBN" ] && DONOR_PROFILE=Profile2
    "$VENV/bin/mbn-tool" -e "$DONOR_MBN" "$DIR/donor" >/dev/null
    python3 - "$DIR/x" "$DIR/donor" "$DONOR_PROFILE" <<'PY'
import json, pathlib, shutil, sys
root, donor, prof = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]

src = donor / 'files' / 'Data_Profiles' / prof
if not src.exists():
    sys.exit(f'donor has no Data_Profiles/{prof}')
dst = root / 'files' / 'Data_Profiles' / prof
if dst.exists():
    sys.exit(f'target already has Data_Profiles/{prof}; refusing to overwrite')
dst.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(src, dst)

name = f'/Data_Profiles/{prof}\x00'
items = json.loads((root / 'nv_items').read_text())
items.append({
    'type': 2, 'attributes': 25, 'reserved': 0,
    'filename': {'hex': ' '.join(f'{b:02x}' for b in name.encode('latin-1')),
                 'ascii': name, '__type__': 'bytes'},
    'data_magic': 7, '__type__': 'MCFG_Item'})
(root / 'nv_items').write_text(json.dumps(items, indent=2))
print(f'added Data_Profiles/{prof} from {donor.name} ({len(items)} items total)')
PY
fi

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
#
# The bump is +1 relative to the INPUT file. If the modem already has a
# patched config active, patching the pristine config again reproduces the
# version already loaded and will be skipped -- feed it the previous output,
# or bump again, so the version is strictly higher than what is active.
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

if [ -n "$DONOR" ]; then
    DONOR_PROFILE=${DONOR#*:}
    [ "$DONOR_PROFILE" = "${DONOR%%:*}" ] && DONOR_PROFILE=Profile2
    cmp "$DIR/v/files/Data_Profiles/$DONOR_PROFILE" \
        "$DIR/x/files/Data_Profiles/$DONOR_PROFILE"
    echo "verified Data_Profiles/$DONOR_PROFILE survives the pack byte-identical"
fi

echo "wrote $OUT"
