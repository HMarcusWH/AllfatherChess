#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="$ROOT/qualification/lc0-strength-profile.json"

python3 - <<'PY' "$PROFILE"
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
assert p["schema_version"] == 2
assert p["build"]["backend"] == "blas"
assert p["runtime"]["Backend"] == "blas"
PY

mapfile -t MESON_OPTIONS < <(
  python3 - <<'PY' "$PROFILE"
import json, sys
for item in json.load(open(sys.argv[1], encoding="utf-8"))["build"]["meson_options"]:
    print(item)
PY
)

echo "==> Building LC0 real-inference reference backend"
rm -rf "$ROOT/engines/lc0/build/release"
(
  cd "$ROOT/engines/lc0"
  ./build.sh release "${MESON_OPTIONS[@]}"
)

LC0_BIN="$ROOT/engines/lc0/build/release/lc0"
[[ -x "$LC0_BIN" ]] || {
  echo "LC0 strength binary not built: $LC0_BIN" >&2
  exit 1
}
sha256sum "$LC0_BIN"
