#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="$ROOT/scripts/vendor-lock.py"

"$LOCK" validate >/dev/null

filename="$("$LOCK" artifact reckless default_nnue filename)"
url="$("$LOCK" artifact reckless default_nnue url)"
expected_size="$("$LOCK" artifact reckless default_nnue size)"
expected_sha="$("$LOCK" artifact reckless default_nnue sha256)"

artifact_dir="${ALLFATHER_ARTIFACT_DIR:-$ROOT/build/artifacts/reckless}"
target="$artifact_dir/$filename"

verify_file() {
  local path="$1"
  local actual_size actual_sha
  [[ -f "$path" ]] || return 1
  actual_size="$(wc -c < "$path" | tr -d ' ')"
  [[ "$actual_size" == "$expected_size" ]] || return 1
  actual_sha="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$actual_sha" == "$expected_sha" ]] || return 1
}

mkdir -p "$artifact_dir"

if [[ -f "$target" ]]; then
  if verify_file "$target"; then
    echo "Reckless NNUE cache verified: $filename" >&2
    printf '%s\n' "$target"
    exit 0
  fi
  echo "Cached Reckless NNUE failed verification; replacing it." >&2
  rm -f "$target"
fi

tmp="$(mktemp "$artifact_dir/.${filename}.tmp.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

echo "Downloading pinned Reckless NNUE: $filename" >&2
curl --fail --silent --show-error --location --retry 3 --output "$tmp" "$url"

if ! verify_file "$tmp"; then
  actual_size="$(wc -c < "$tmp" | tr -d ' ')"
  actual_sha="$(sha256sum "$tmp" | awk '{print $1}')"
  echo "Reckless NNUE verification failed." >&2
  echo "expected size=$expected_size sha256=$expected_sha" >&2
  echo "actual   size=$actual_size sha256=$actual_sha" >&2
  exit 1
fi

mv "$tmp" "$target"
trap - EXIT

echo "Reckless NNUE verified and installed: $target" >&2
printf '%s\n' "$target"
