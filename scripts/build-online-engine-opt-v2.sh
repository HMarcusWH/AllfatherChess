#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOBS="${JOBS:-2}"
BUNDLE="$ROOT/build/online-engine-opt-v2"
POLICY="$ROOT/qualification/online-engine-opt-v2.json"
"$ROOT/scripts/verify-vendor.sh"
python3 "$ROOT/scripts/lc0-strength-lock.py" validate-frozen
STOCKFISH_NET="$("$ROOT/scripts/fetch-stockfish-network.sh")"
STOCKFISH_NAME="$("$ROOT/scripts/vendor-lock.py" artifact stockfish default_nnue filename)"
ln -sfn "$STOCKFISH_NET" "$ROOT/engines/stockfish/src/$STOCKFISH_NAME"
RECKLESS_NET="$("$ROOT/scripts/fetch-reckless-network.sh")"
python3 "$ROOT/scripts/fetch-lc0-network.py" --allow-candidate-size >/dev/null
LC0_NET="$ROOT/build/artifacts/lc0/791556.pb.gz"
rm -rf "$BUNDLE"; mkdir -p "$BUNDLE/bin" "$BUNDLE/networks"
echo "==> Building portable PGO Stockfish ARCH=x86-64"
make -C "$ROOT/engines/stockfish/src" ARCH=x86-64 objclean
make -C "$ROOT/engines/stockfish/src" -j"$JOBS" profile-build ARCH=x86-64
cp "$ROOT/engines/stockfish/src/stockfish" "$BUNDLE/bin/stockfish"
echo "==> Building portable Reckless target-cpu=x86-64"
( cd "$ROOT/engines/reckless"; EVALFILE="$RECKLESS_NET" CARGO_ENCODED_RUSTFLAGS='-Ctarget-cpu=x86-64' cargo build --release --no-default-features --target x86_64-unknown-linux-gnu )
cp "$ROOT/engines/reckless/target/x86_64-unknown-linux-gnu/release/reckless" "$BUNDLE/bin/reckless"
echo "==> Building portable LC0 BLAS candidate"
bash "$ROOT/scripts/build-lc0-strength.sh"
cp "$ROOT/engines/lc0/build/release/lc0" "$BUNDLE/bin/lc0"
cp "$STOCKFISH_NET" "$BUNDLE/networks/$STOCKFISH_NAME"
cp "$RECKLESS_NET" "$BUNDLE/networks/$(basename "$RECKLESS_NET")"
cp "$LC0_NET" "$BUNDLE/networks/791556.pb.gz"
python3 - "$ROOT" "$BUNDLE" "$POLICY" <<'PY'
import hashlib,json,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1]); bundle=Path(sys.argv[2]); policy_path=Path(sys.argv[3])
def digest(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1<<20),b''): h.update(b)
 return h.hexdigest()
def rec(rel):
 p=bundle/rel; return {'path':rel,'size':p.stat().st_size,'sha256':digest(p)}
policy=json.loads(policy_path.read_text())
source=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
manifest={'schema_version':1,'profile_id':policy['profile_id'],'source_commit':source,'contracts':{'policy_sha256':digest(policy_path),'selection_sha256':digest(root/'qualification/engine-opt-v2-selection.json'),'derived_lock_sha256':digest(root/'qualification/engine-derived-lock.json')},'builds':policy['builds'],'artifacts':{'engines':{k:rec(v['artifact']) for k,v in policy['builds'].items()},'networks':{k:rec(v['network_artifact']) for k,v in policy['builds'].items()}}}
(bundle/'build-manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
PY
echo "==> ENGINE-OPT-V2 bundle ready: $BUNDLE"
