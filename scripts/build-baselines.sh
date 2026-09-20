#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JOBS="${JOBS:-2}"

"$ROOT/scripts/verify-vendor.sh"

echo "==> Building Stockfish"
make -C "$ROOT/engines/stockfish/src" -j"$JOBS" build ARCH=x86-64

echo "==> Building Reckless"
cargo build --manifest-path "$ROOT/engines/reckless/Cargo.toml" --release --no-default-features

echo "==> Building LC0 (backend-light validation configuration)"
(
  cd "$ROOT/engines/lc0"
  ./build.sh release -Dbuild_backends=false -Dgtest=false -Db_lto=false
)

echo "==> Baseline builds completed."
