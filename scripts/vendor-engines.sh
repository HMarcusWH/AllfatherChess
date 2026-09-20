#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

import_engine() {
  local name="$1"
  local repo="$2"
  local sha="$3"
  local dest="$ROOT/engines/$name"

  echo "==> Importing $name @ $sha"
  git clone --quiet --no-checkout "$repo" "$TMP/$name"
  git -C "$TMP/$name" checkout --quiet --detach "$sha"

  rm -rf "$dest"
  mkdir -p "$dest"
  rsync -a --delete --exclude='.git/' "$TMP/$name/" "$dest/"

  cat > "$dest/.allfather-origin" <<EOF
repository=$repo
commit=$sha
imported_by=scripts/vendor-engines.sh
EOF
}

mkdir -p "$ROOT/engines"
import_engine stockfish "https://github.com/HMarcusWH/Stockfish.git" "17a6c8f1eb0da45c2ca405321919519bf4e211ba"
import_engine reckless  "https://github.com/HMarcusWH/Reckless.git"  "31d9cd6fd2bea6d9f72eeb35e0bac70daa295fb1"
import_engine lc0       "https://github.com/HMarcusWH/lc0.git"       "5cbfeb924c0fcf5efc1b2a43813ac8d63cfd9904"
echo "==> Vendored engine snapshots are ready."
