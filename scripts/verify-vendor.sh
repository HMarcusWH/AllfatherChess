#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

check_origin() {
  local name="$1"
  local sha="$2"
  local tree="$3"
  local origin="$ROOT/engines/$name/.allfather-origin"

  test -f "$origin" || { echo "missing $origin" >&2; exit 1; }
  grep -qx "commit=$sha" "$origin" || {
    echo "$name provenance mismatch; expected commit $sha" >&2
    exit 1
  }
  grep -qx "tree=$tree" "$origin" || {
    echo "$name provenance mismatch; expected tree $tree" >&2
    exit 1
  }
}

check_origin stockfish "17a6c8f1eb0da45c2ca405321919519bf4e211ba" "b14521db7dc6f5747042d76579a0b171e0a89f3f"
check_origin reckless  "31d9cd6fd2bea6d9f72eeb35e0bac70daa295fb1" "88763d8e81ea45b938403f7f4feee990ad244c2d"
check_origin lc0       "5cbfeb924c0fcf5efc1b2a43813ac8d63cfd9904" "6dd8aad55c79fddc291abfede17934f9ce4ca928"

# License/provenance anchors.
test -f "$ROOT/engines/stockfish/Copying.txt"
test -f "$ROOT/engines/reckless/LICENSE"
test -f "$ROOT/engines/lc0/COPYING"

# Build-critical tracked files that are easy to lose when vendoring because
# LC0's own .gitignore ignores most of subprojects/.
test -f "$ROOT/engines/lc0/subprojects/abseil-cpp.wrap"
test -f "$ROOT/engines/lc0/subprojects/protobuf.wrap"
test -f "$ROOT/engines/lc0/subprojects/zlib.wrap"
test -d "$ROOT/engines/lc0/subprojects/packagefiles"

echo "Vendored provenance, completeness anchors, and license files verified."
