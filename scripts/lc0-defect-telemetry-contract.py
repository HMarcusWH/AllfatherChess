#!/usr/bin/env python3
"""Compile and run the LC0 defect-telemetry provenance state-machine contract."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULT_DIR = ROOT / "build" / "test-results" / "lc0-defect-telemetry"
SOURCE = ROOT / "tests" / "lc0" / "defect_telemetry_tracker_test.cc"
BINARY = RESULT_DIR / "defect-telemetry-tracker-test"


def main() -> int:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    cxx = os.environ.get("CXX", "g++")
    compile_cmd = [
        cxx, "-std=c++20", "-Wall", "-Wextra", "-Werror",
        "-I", str(ROOT / "engines" / "lc0" / "src"),
        str(SOURCE), "-o", str(BINARY),
    ]
    subprocess.run(compile_cmd, cwd=ROOT, check=True)
    completed = subprocess.run(
        [str(BINARY)], cwd=ROOT, check=True, text=True, capture_output=True
    )
    manifest = {
        "schema_version": 1,
        "source": str(SOURCE.relative_to(ROOT)),
        "compiler": cxx,
        "stdout": completed.stdout.strip(),
        "claims": {
            "completed_generation_can_be_consumed": True,
            "completed_generation_retires_on_miss": True,
            "inflight_generation_invalidates_on_miss": True,
            "new_generation_can_be_consumed": True,
            "duplicate_submissions_are_independent": True,
            "position_generations_are_isolated": True,
        },
    }
    (RESULT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(completed.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
