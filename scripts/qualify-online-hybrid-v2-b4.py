#!/usr/bin/env python3
"""Canonical-b4 G3 witness qualification using the frozen 7+16 corpus."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.engine_opt.g3_b4 import (
    B4_POLICY, CANONICAL_REFERENCE, CANONICAL_RUNTIME, validate_b4_policy,
)


def main() -> None:
    validate_b4_policy(ROOT)
    env = os.environ.copy()
    env.update({
        "ALLFATHER_G3_POLICY": B4_POLICY,
        "ALLFATHER_G3_SELECTION": "qualification/engine-opt-v2-selection.json",
        "ALLFATHER_G3_REFERENCE": CANONICAL_REFERENCE,
        "ALLFATHER_G3_CONFIG": CANONICAL_RUNTIME,
        "ALLFATHER_G3_RESULT": "build/test-results/online-hybrid-v2",
    })
    os.execvpe(sys.executable,
               [sys.executable, str(ROOT / "scripts/qualify-online-hybrid-v2.py"), *sys.argv[1:]],
               env)


if __name__ == "__main__":
    main()
