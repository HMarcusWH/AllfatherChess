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
root=Path(sys.argv[1]).resolve()
bundle=Path(sys.argv[2]).resolve()
policy_path=Path(sys.argv[3]).resolve()
def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
def digest(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1<<20),b""):
            h.update(block)
    return h.hexdigest()
def rec(rel):
    path=bundle/rel
    return {"path":rel,"size":path.stat().st_size,"sha256":digest(path)}
def git(*args):
    return subprocess.check_output(["git","-C",str(root),*args],text=True).strip()

policy=load(policy_path)
vendor=load(root/"vendor.lock.json")
derived=load(root/"qualification/engine-derived-lock.json")
source=git("rev-parse","HEAD")
source_tree=git("rev-parse","HEAD^{tree}")
engine_trees={
    family:git("rev-parse",f"HEAD:engines/{family}")
    for family in ("stockfish","reckless","lc0")
}
for family,tree in engine_trees.items():
    expected=(derived.get("engines") or {}).get(family,{}).get("derived_tree")
    if tree != expected:
        raise SystemExit(
            f"derived engine tree drift for {family}: checkout={tree} lock={expected}"
        )

manifest={
    "schema_version":1,
    "profile_id":policy["profile_id"],
    "source_commit":source,
    "source_tree":source_tree,
    "contracts":{
        "vendor_lock_sha256":digest(root/"vendor.lock.json"),
        "policy_sha256":digest(policy_path),
        "runtime_config_sha256":digest(root/policy["runtime_config"]),
        "selection_sha256":digest(root/"qualification/engine-opt-v2-selection.json"),
        "evidence_sha256":digest(root/"qualification/engine-opt-v2-evidence.json"),
        "derived_lock_sha256":digest(root/"qualification/engine-derived-lock.json"),
        "lc0_strength_lock_sha256":digest(root/"qualification/lc0-strength.lock.json"),
        "lc0_strength_profile_sha256":digest(root/"qualification/lc0-strength-profile.json"),
    },
    "vendor":{
        family:{
            "commit":vendor["engines"][family]["commit"],
            "tree":vendor["engines"][family]["tree"],
        }
        for family in ("stockfish","reckless","lc0")
    },
    "derived_engine_trees":engine_trees,
    "builds":policy["builds"],
    "artifacts":{
        "engines":{
            family:rec(policy["builds"][family]["artifact"])
            for family in ("stockfish","reckless","lc0")
        },
        "networks":{
            family:rec(policy["builds"][family]["network_artifact"])
            for family in ("stockfish","reckless","lc0")
        },
    },
}
(bundle/"build-manifest.json").write_text(
    json.dumps(manifest,indent=2,sort_keys=True,allow_nan=False)+"\n",
    encoding="utf-8",
)
print("ENGINE-OPT-V2 manifest source",source,source_tree)
PY
echo "==> ENGINE-OPT-V2 bundle ready: $BUNDLE"
