#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="$ROOT/scripts/vendor-lock.py"

"$LOCK" validate >/dev/null

check_origin() {
  local name="$1"
  local repo commit tree entries dest_rel origin
  repo="$("$LOCK" engine "$name" repository)"
  commit="$("$LOCK" engine "$name" commit)"
  tree="$("$LOCK" engine "$name" tree)"
  entries="$("$LOCK" engine "$name" tracked_entries)"
  dest_rel="$("$LOCK" engine "$name" destination)"
  origin="$ROOT/$dest_rel/.allfather-origin"

  [[ -f "$origin" ]] || {
    echo "missing provenance record: $origin" >&2
    exit 1
  }

  grep -Fqx "repository=$repo" "$origin" || {
    echo "$name provenance mismatch: repository" >&2
    exit 1
  }
  grep -Fqx "commit=$commit" "$origin" || {
    echo "$name provenance mismatch: commit" >&2
    exit 1
  }
  grep -Fqx "tree=$tree" "$origin" || {
    echo "$name provenance mismatch: tree" >&2
    exit 1
  }
  grep -Fqx "tracked_entries=$entries" "$origin" || {
    echo "$name provenance mismatch: tracked_entries" >&2
    exit 1
  }
  grep -Fqx "imported_by=scripts/vendor-engines.sh" "$origin" || {
    echo "$name provenance mismatch: importer identity" >&2
    exit 1
  }
}

check_origin stockfish
check_origin reckless
check_origin lc0

# License/provenance anchors.
[[ -f "$ROOT/engines/stockfish/Copying.txt" ]]
[[ -f "$ROOT/engines/reckless/LICENSE" ]]
[[ -f "$ROOT/engines/lc0/COPYING" ]]

# Build-critical LC0 files that were previously lost by ignore-sensitive
# vendoring. These are structural anchors, not an assertion that the current
# derived LC0 tree must remain byte-identical to its imported upstream tree.
[[ -f "$ROOT/engines/lc0/subprojects/abseil-cpp.wrap" ]]
[[ -f "$ROOT/engines/lc0/subprojects/protobuf.wrap" ]]
[[ -f "$ROOT/engines/lc0/subprojects/zlib.wrap" ]]
[[ -d "$ROOT/engines/lc0/subprojects/packagefiles" ]]

# A monorepo engine subtree must never silently become a nested git repository.
if find "$ROOT/engines" -type d -name .git -print -quit | grep -q .; then
  echo "nested .git directory found under engines/" >&2
  exit 1
fi

echo "Declared upstream ancestry, structural anchors, and license files verified."
