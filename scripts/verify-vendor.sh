#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

check_origin() {
  local name="$1"
  local sha="$2"
  local origin="$ROOT/engines/$name/.allfather-origin"
  test -f "$origin" || { echo "missing $origin" >&2; exit 1; }
  grep -qx "commit=$sha" "$origin" || {
    echo "$name provenance mismatch; expected $sha" >&2
    exit 1
  }
}

check_origin stockfish "17a6c8f1eb0da45c2ca405321919519bf4e211ba"
check_origin reckless  "31d9cd6fd2bea6d9f72eeb35e0bac70daa295fb1"
check_origin lc0       "5cbfeb924c0fcf5efc1b2a43813ac8d63cfd9904"

test -f "$ROOT/engines/stockfish/Copying.txt"
test -f "$ROOT/engines/reckless/LICENSE"
test -f "$ROOT/engines/lc0/COPYING"

echo "Vendored provenance and license files verified."
