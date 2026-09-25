#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOBS="${JOBS:-2}"
BUNDLE="$ROOT/build/online-cpu-reference"
POLICY="$ROOT/qualification/online-cpu-reference.json"

"$ROOT/scripts/verify-vendor.sh"
python3 "$ROOT/scripts/lc0-strength-lock.py" validate-frozen
python3 "$ROOT/tests/controller/test_online_profile.py"

STOCKFISH_NET="$("$ROOT/scripts/fetch-stockfish-network.sh")"
STOCKFISH_NAME="$("$ROOT/scripts/vendor-lock.py" artifact stockfish default_nnue filename)"
ln -sfn "$STOCKFISH_NET" "$ROOT/engines/stockfish/src/$STOCKFISH_NAME"
RECKLESS_NET="$("$ROOT/scripts/fetch-reckless-network.sh")"
python3 "$ROOT/scripts/fetch-lc0-network.py" --allow-candidate-size >/dev/null
LC0_NET="$ROOT/build/artifacts/lc0/791556.pb.gz"

rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/bin" "$BUNDLE/networks"

echo "==> Building Stockfish ARCH=x86-64"
make -C "$ROOT/engines/stockfish/src" -j"$JOBS" build ARCH=x86-64
cp "$ROOT/engines/stockfish/src/stockfish" "$BUNDLE/bin/stockfish"

echo "==> Building Reckless target-cpu=x86-64"
(
  cd "$ROOT/engines/reckless"
  EVALFILE="$RECKLESS_NET"   CARGO_ENCODED_RUSTFLAGS='-Ctarget-cpu=x86-64'   cargo build --release --no-default-features --target x86_64-unknown-linux-gnu
)
cp "$ROOT/engines/reckless/target/x86_64-unknown-linux-gnu/release/reckless" "$BUNDLE/bin/reckless"

echo "==> Building LC0 BLAS"
bash "$ROOT/scripts/build-lc0-strength.sh"
cp "$ROOT/engines/lc0/build/release/lc0" "$BUNDLE/bin/lc0"

cp "$STOCKFISH_NET" "$BUNDLE/networks/$STOCKFISH_NAME"
cp "$RECKLESS_NET" "$BUNDLE/networks/$(basename "$RECKLESS_NET")"
cp "$LC0_NET" "$BUNDLE/networks/791556.pb.gz"

python3 - "$ROOT" "$BUNDLE" "$POLICY" <<'PY'
import hashlib,json,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
bundle=Path(sys.argv[2]).resolve()
policy_path=Path(sys.argv[3]).resolve()
load=lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
def digest(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda:f.read(1<<20),b""): h.update(chunk)
    return h.hexdigest()
def record(rel):
    p=bundle/rel
    return {"path":rel,"size":p.stat().st_size,"sha256":digest(p)}
policy=load(policy_path); vendor=load(root/"vendor.lock.json")
source=subprocess.check_output(["git","-C",str(root),"rev-parse","HEAD"],text=True).strip()
manifest={
 "schema_version":1,"profile_id":policy["profile_id"],"source_commit":source,
 "contracts":{
   "vendor_lock_sha256":digest(root/"vendor.lock.json"),
   "online_policy_sha256":digest(policy_path),
   "runtime_config_sha256":digest(root/policy["runtime_config"]),
   "lc0_strength_lock_sha256":digest(root/"qualification/lc0-strength.lock.json"),
   "lc0_strength_profile_sha256":digest(root/"qualification/lc0-strength-profile.json"),
 },
 "vendor":{family:{"commit":vendor["engines"][family]["commit"],"tree":vendor["engines"][family]["tree"]} for family in ("stockfish","reckless","lc0")},
 "builds":policy["builds"],
 "runtime_environment":policy["process_environment"],
 "artifacts":{
   "engines":{family:record(policy["builds"][family]["artifact"]) for family in ("stockfish","reckless","lc0")},
   "networks":{family:record(policy["builds"][family]["network_artifact"]) for family in ("stockfish","reckless","lc0")},
 },
}
(bundle/"build-manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
print("ONLINE-2 manifest source",source)
PY
echo "==> ONLINE-2 bundle ready: $BUNDLE"
