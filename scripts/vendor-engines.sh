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

  local tree
  tree="$(git -C "$TMP/$name" rev-parse "$sha^{tree}")"

  rm -rf "$dest"
  mkdir -p "$dest"

  # Export exactly the files tracked by the pinned commit. Using git archive
  # avoids copying .git metadata or generated/untracked files, while preserving
  # tracked files that happen to match an engine's own .gitignore rules.
  git -C "$TMP/$name" archive "$sha" | tar -x -C "$dest"

  local expected_files actual_files
  expected_files="$(git -C "$TMP/$name" ls-tree -r --name-only "$sha" | wc -l | tr -d ' ')"
  actual_files="$(find "$dest" \( -type f -o -type l \) | wc -l | tr -d ' ')"

  if [[ "$expected_files" != "$actual_files" ]]; then
    echo "$name archive completeness failure: expected $expected_files tracked entries, got $actual_files" >&2
    exit 1
  fi

  cat > "$dest/.allfather-origin" <<EOF
repository=$repo
commit=$sha
tree=$tree
tracked_entries=$expected_files
imported_by=scripts/vendor-engines.sh
EOF
}

mkdir -p "$ROOT/engines"
import_engine stockfish "https://github.com/HMarcusWH/Stockfish.git" "17a6c8f1eb0da45c2ca405321919519bf4e211ba"
import_engine reckless  "https://github.com/HMarcusWH/Reckless.git"  "31d9cd6fd2bea6d9f72eeb35e0bac70daa295fb1"
import_engine lc0       "https://github.com/HMarcusWH/lc0.git"       "5cbfeb924c0fcf5efc1b2a43813ac8d63cfd9904"

echo "==> Vendored engine snapshots are ready."
