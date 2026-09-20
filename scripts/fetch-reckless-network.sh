#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HELPER="$ROOT/scripts/fetch-reckless-network.py"

if [[ -n "${PYTHON:-}" ]]; then
  exec "$PYTHON" "$HELPER" "$@"
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 "$HELPER" "$@"
fi

if command -v python >/dev/null 2>&1; then
  exec python "$HELPER" "$@"
fi

echo "Python 3 is required to fetch the pinned Reckless NNUE." >&2
exit 1
