#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOBS="${JOBS:-2}"

"$ROOT/scripts/verify-vendor.sh"

echo "==> Resolving pinned Stockfish NNUE"
STOCKFISH_NET="$("$ROOT/scripts/fetch-stockfish-network.sh")"
[[ -f "$STOCKFISH_NET" ]] || {
  echo "verified Stockfish network path does not exist: $STOCKFISH_NET" >&2
  exit 1
}
STOCKFISH_NET_NAME="$("$ROOT/scripts/vendor-lock.py" artifact stockfish default_nnue filename)"
ln -sfn "$STOCKFISH_NET" "$ROOT/engines/stockfish/src/$STOCKFISH_NET_NAME"

echo "==> Resolving pinned Reckless NNUE"
RECKLESS_NET="$("$ROOT/scripts/fetch-reckless-network.sh")"
[[ -f "$RECKLESS_NET" ]] || {
  echo "verified Reckless network path does not exist: $RECKLESS_NET" >&2
  exit 1
}

echo "==> Building Stockfish"
make -C "$ROOT/engines/stockfish/src" -j"$JOBS" build ARCH=x86-64

echo "==> Building Reckless with verified EVALFILE"
EVALFILE="$RECKLESS_NET" \
  cargo build \
    --manifest-path "$ROOT/engines/reckless/Cargo.toml" \
    --release \
    --no-default-features

echo "==> Building LC0 (backend-light validation configuration)"
(
  cd "$ROOT/engines/lc0"
  ./build.sh release -Dbuild_backends=false -Dgtest=false -Db_lto=false
)

echo "==> Baseline builds completed."
