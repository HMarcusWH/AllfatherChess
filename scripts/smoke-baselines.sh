#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

smoke_uci() {
  local name="$1"
  local bin="$2"
  test -x "$bin" || { echo "$name binary not executable: $bin" >&2; exit 1; }
  printf 'uci\nquit\n' | "$bin" | grep -q 'uciok'
  echo "$name: UCI smoke OK"
}

smoke_uci "Stockfish" "$ROOT/engines/stockfish/src/stockfish"
smoke_uci "Reckless"  "$ROOT/engines/reckless/target/release/reckless"

LC0_BIN="$ROOT/engines/lc0/build/release/lc0"
if [[ ! -x "$LC0_BIN" ]]; then
  LC0_BIN="$(find "$ROOT/engines/lc0/build" -type f -name lc0 -perm -111 2>/dev/null | head -n1 || true)"
fi
test -n "$LC0_BIN" || { echo "LC0 binary not found under engines/lc0/build" >&2; exit 1; }
smoke_uci "LC0" "$LC0_BIN"
