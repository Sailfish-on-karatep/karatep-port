#!/bin/bash
# Set RegOnMode in an MBN config's nv/item_files/ims/qp_ims_reg_config.
#
# HOST shell. The IMS registration manager has two modes:
#
#     PowerOn = 0    register as soon as IMS is available
#     OnCall  = 1    register only when a call is placed
#
# A config carrying OnCall produces a stack in which every request succeeds,
# the modem reports no error, and IMS simply never registers while idle --
# which looks identical to a modem that is refusing, and is not. Carrier
# configs do ship OnCall (Jio's does), so a config imported wholesale from
# another operator brings it along.
#
# PowerOn is what a VoLTE indicator and an always-registered stack need.
#
# The item's layout is a 0x07 item-file prefix then RegOnMode as the first
# byte, so this is a one-byte edit; the MCFG minor version is bumped as ever,
# or set explicitly with MBN_MINOR.
#
# See docs/rca/volte-registration-change-is-test-mode.md.
#
# Usage: mbn-set-regonmode.sh <in.mbn> <out.mbn> <poweron|oncall> [workdir]

set -euo pipefail

USAGE='usage: mbn-set-regonmode.sh <in.mbn> <out.mbn> <poweron|oncall> [workdir]'
IN=${1:?$USAGE}
OUT=${2:?$USAGE}
MODE=${3:?$USAGE}
WORK=${4:-/opencloud/work/telephony}

case "$MODE" in
    poweron) MODE_VAL=0 ;;
    oncall)  MODE_VAL=1 ;;
    *) echo "$USAGE" >&2; exit 1 ;;
esac

TOOLS="$WORK/mbn-mcfg-tools"
VENV="$WORK/venv"

if [ ! -d "$TOOLS" ]; then
    git clone --depth 1 https://github.com/sbaresearch/mbn-mcfg-tools.git "$TOOLS"
fi
if [ ! -x "$VENV/bin/mbn-tool" ]; then
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q "$TOOLS"
fi

DIR=$(mktemp -d "$WORK/mbn-regon.XXXXXX")
trap 'rm -rf "$DIR"' EXIT

"$VENV/bin/mbn-tool" -c "$IN"
"$VENV/bin/mbn-tool" -e "$IN" "$DIR/x" >/dev/null

python3 - "$DIR/x" "$MODE_VAL" "${MBN_MINOR-}" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
want = int(sys.argv[2])
forced = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else None

p = root / 'files' / 'nv/item_files/ims/qp_ims_reg_config'
if not p.exists():
    sys.exit('config has no nv/item_files/ims/qp_ims_reg_config')
b = bytearray(p.read_bytes())
if b[0] != 0x07:
    sys.exit(f'unexpected item prefix {b[0]:#04x}')
print(f'RegOnMode: {b[1]} -> {want}')
b[1] = want
p.write_bytes(bytes(b))

meta = json.loads((root / 'meta').read_text())
old = new = None
for f in (meta['version'], meta['trailer']['version1'], meta['trailer']['version2']):
    v = bytearray.fromhex(f['hex'])
    if old is None:
        old = v[0]
    if forced is not None:
        v[0] = forced
    elif v[0] == 0xff:
        sys.exit('minor version is already 0xff; set MBN_MINOR explicitly')
    else:
        v[0] += 1
    new = v[0]
    f['hex'] = ' '.join(f'{x:02x}' for x in v)
    f['ascii'] = bytes(v).decode('latin-1')
(root / 'meta').write_text(json.dumps(meta, indent=2))
print(f'MCFG minor version: {old} -> {new}')
PY

"$VENV/bin/mbn-tool" -p "$OUT" "$DIR/x"
"$VENV/bin/mbn-tool" -c "$OUT"

"$VENV/bin/mbn-tool" -e "$OUT" "$DIR/v" >/dev/null
python3 - "$DIR/v" "$MODE_VAL" <<'PY'
import json, pathlib, sys
root, want = pathlib.Path(sys.argv[1]), int(sys.argv[2])
v = json.load(open(root / 'parsed_nv_files/nv/item_files/ims/qp_ims_reg_config'))
got = v['fields']['RegOnMode']
val = got['value'] if isinstance(got, dict) else got
assert val == want, f'read back RegOnMode {got}, wanted {want}'
print(f'verified RegOnMode = {got}')
PY

echo "wrote $OUT"
