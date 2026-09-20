#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="$ROOT/scripts/vendor-lock.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

usage() {
  cat <<'EOF'
Usage:
  scripts/vendor-engines.sh --engine <stockfish|reckless|lc0> --overwrite
  scripts/vendor-engines.sh --all --overwrite

This is an explicit, destructive upstream-baseline refresh tool.
It replaces the selected live engine tree(s) with exact git-archive exports from
vendor.lock.json. It is NOT a CI normalizer and must not be used to erase
Allfather-local engine changes accidentally.
EOF
}

overwrite=0
declare -a engines=()

while (($#)); do
  case "$1" in
    --engine)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      engines+=("$2")
      shift 2
      ;;
    --all)
      engines=(stockfish reckless lc0)
      shift
      ;;
    --overwrite)
      overwrite=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

"$LOCK" validate >/dev/null

((${#engines[@]} > 0)) || {
  echo "refusing to select an engine implicitly" >&2
  usage >&2
  exit 2
}

((overwrite == 1)) || {
  echo "refusing destructive refresh without --overwrite" >&2
  usage >&2
  exit 2
}

import_engine() {
  local name="$1"
  local repo sha expected_tree expected_entries dest_rel dest
  repo="$("$LOCK" engine "$name" repository)"
  sha="$("$LOCK" engine "$name" commit)"
  expected_tree="$("$LOCK" engine "$name" tree)"
  expected_entries="$("$LOCK" engine "$name" tracked_entries)"
  dest_rel="$("$LOCK" engine "$name" destination)"
  dest="$ROOT/$dest_rel"

  echo "==> Refreshing $name from $repo @ $sha" >&2
  git clone --quiet --no-checkout "$repo" "$TMP/$name"

  local actual_tree actual_entries
  actual_tree="$(git -C "$TMP/$name" rev-parse "$sha^{tree}")"
  [[ "$actual_tree" == "$expected_tree" ]] || {
    echo "$name tree mismatch: lock=$expected_tree upstream=$actual_tree" >&2
    exit 1
  }

  actual_entries="$(git -C "$TMP/$name" ls-tree -r --name-only "$sha" | wc -l | tr -d ' ')"
  [[ "$actual_entries" == "$expected_entries" ]] || {
    echo "$name tracked-entry mismatch: lock=$expected_entries upstream=$actual_entries" >&2
    exit 1
  }

  rm -rf "$dest"
  mkdir -p "$dest"

  # Export exactly the files tracked by the pinned commit. git archive avoids
  # nested .git metadata and ignores the vendored project's own ignore rules.
  git -C "$TMP/$name" archive "$sha" | tar -x -C "$dest"

  local extracted_entries
  extracted_entries="$(find "$dest" \( -type f -o -type l \) | wc -l | tr -d ' ')"
  [[ "$extracted_entries" == "$expected_entries" ]] || {
    echo "$name archive completeness failure: expected $expected_entries tracked entries, got $extracted_entries" >&2
    exit 1
  }

  cat > "$dest/.allfather-origin" <<EOF
repository=$repo
commit=$sha
tree=$expected_tree
tracked_entries=$expected_entries
imported_by=scripts/vendor-engines.sh
EOF

  echo "==> $name baseline refresh complete" >&2
}

for engine in "${engines[@]}"; do
  case "$engine" in
    stockfish|reckless|lc0) import_engine "$engine" ;;
    *)
      echo "unknown engine: $engine" >&2
      exit 2
      ;;
  esac
done

echo "Selected upstream baseline refresh completed." >&2
