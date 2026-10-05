#!/usr/bin/env python3
"""Validate frozen calibration evidence; --archive reconstructs original ZIPs."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.engine_opt.legacy_calibration import (  # noqa: E402
    CALIBRATION,
    CONFIG,
    POLICY,
    CalibrationError,
    main,
    validate,
)

if __name__ == "__main__":
    raise SystemExit(main())
