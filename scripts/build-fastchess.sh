#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="$ROOT/qualification/fastchess.lock.json"
OUT="$ROOT/build/tools/fastchess"
SRC="$OUT/src"
JOBS="${JOBS:-2}"

read_lock() {
  python3 - "$LOCK" "$1" <<'PY'
import json,sys
doc=json.load(open(sys.argv[1],encoding="utf-8"))
value=doc
for part in sys.argv[2].split("."):
    value=value[part]
print(value)
PY
}

REPO="$(read_lock repository)"
COMMIT="$(read_lock commit)"
TREE="$(read_lock tree)"

rm -rf "$SRC"
mkdir -p "$OUT/bin"
git init -q "$SRC"
git -C "$SRC" remote add origin "$REPO"
git -C "$SRC" fetch -q --depth 1 origin "$COMMIT"
git -C "$SRC" -c advice.detachedHead=false checkout -q --detach FETCH_HEAD

test "$(git -C "$SRC" rev-parse HEAD)" = "$COMMIT"
test "$(git -C "$SRC" rev-parse 'HEAD^{tree}')" = "$TREE"

# Build the lifecycle runner from the exact source pin. Upstream's unit-test
# suite is qualified separately on its supported Ubuntu 22.04 CI environment;
# this Ubuntu 24.04 lifecycle host only builds/executes the tournament binary.
make -C "$SRC" clean
make -C "$SRC" -j"$JOBS" build=release NATIVE='-march=x86-64'

cp "$SRC/fastchess" "$OUT/bin/fastchess"
chmod +x "$OUT/bin/fastchess"
cp "$SRC/LICENSE" "$OUT/LICENSE"

python3 - "$ROOT" "$LOCK" "$OUT" <<'PY'
import hashlib,json,subprocess,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()
lock_path=Path(sys.argv[2]).resolve()
out=Path(sys.argv[3]).resolve()
lock=json.loads(lock_path.read_text(encoding="utf-8"))
def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda:f.read(1<<20),b""):
            h.update(chunk)
    return h.hexdigest()
binary=out/"bin"/"fastchess"
manifest={
    "schema_version":1,
    "tool":"fastchess",
    "source_commit":lock["commit"],
    "source_tree":lock["tree"],
    "lock_sha256":sha(lock_path),
    "binary":{"path":"bin/fastchess","size":binary.stat().st_size,"sha256":sha(binary)},
    "license":{"path":"LICENSE","sha256":sha(out/"LICENSE")},
    "compiler":subprocess.check_output(["c++","--version"],text=True).splitlines()[0],
    "source_tests":"qualified by dedicated Ubuntu 22.04 workflow job",
    "release_build":"clean -> make build=release NATIVE=-march=x86-64",
    "reported_version":subprocess.check_output([str(binary),"-version"],text=True,stderr=subprocess.STDOUT).strip(),
}
(out/"build-manifest.json").write_text(
    json.dumps(manifest,indent=2,sort_keys=True,allow_nan=False)+"\n",
    encoding="utf-8",
)
print("Fastchess ready", lock["commit"], manifest["binary"]["sha256"])
PY
