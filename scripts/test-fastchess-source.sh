#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="$ROOT/qualification/fastchess.lock.json"
SRC="$ROOT/build/tools/fastchess-source-test"
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
git init -q "$SRC"
git -C "$SRC" remote add origin "$REPO"
git -C "$SRC" fetch -q --depth 1 origin "$COMMIT"
git -C "$SRC" -c advice.detachedHead=false checkout -q --detach FETCH_HEAD

test "$(git -C "$SRC" rev-parse HEAD)" = "$COMMIT"
test "$(git -C "$SRC" rev-parse 'HEAD^{tree}')" = "$TREE"

# This intentionally mirrors upstream .github/workflows/unit_tests.yml:
# clean test objects, build the tests, then execute the produced suite.
make -C "$SRC" clean
make -C "$SRC" -j"$JOBS" tests
"$SRC"/fastchess-tests*

echo "Fastchess source tests passed for $COMMIT"
