#!/usr/bin/env python3
"""Classify whether a PR diff can affect legacy chess execution.

This is intentionally narrow. Only the J2 host-observation surface and its
contract tests/workflow plumbing are eligible for control-plane-only treatment.
Anything else is conservatively classified as runtime_affected.
"""

from __future__ import annotations

import argparse
from pathlib import PurePosixPath


CONTROL_PLANE_ONLY = {
    "Makefile",
    "adapters/resource/linux_host.py",
    "controller/host_capabilities.py",
    "controller/host_pressure.py",
    "tests/adapters/test_linux_host.py",
    "tests/controller/test_host_capabilities.py",
    "tests/controller/test_host_pressure.py",
    "scripts/classify-qualification-impact.py",
    "tests/test_qualification_impact.py",
    ".github/workflows/engine-optimization.yml",
    ".github/workflows/full-game-qualification.yml",
}


def classify(paths: list[str]) -> str:
    normalized = []
    for raw in paths:
        path = PurePosixPath(raw)
        value = path.as_posix()
        if value in ("", ".") or value.startswith("../"):
            return "runtime_affected"
        normalized.append(value)
    if normalized and all(path in CONTROL_PLANE_ONLY for path in normalized):
        return "control_plane_only"
    return "runtime_affected"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    print(classify(args.paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
