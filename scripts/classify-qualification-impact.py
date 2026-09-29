#!/usr/bin/env python3
"""Classify whether a diff can affect legacy chess execution.

Fail closed by default.

After J2 is merged, only the immutable host-observation implementation/tests are
eligible for control-plane-only treatment.  Workflow/Makefile/classifier edits
are permitted only for the one-time J2 bootstrap from its exact pre-J2 base
commit; they cannot silently exempt later CI changes from legacy qualification.
"""

from __future__ import annotations

import argparse
from pathlib import PurePosixPath


J2_BOOTSTRAP_BASE_SHA = "ebcb7988585142e784a5c4007d415917e0a239e3"

ALWAYS_CONTROL_PLANE = {
    "adapters/resource/linux_host.py",
    "controller/host_capabilities.py",
    "controller/host_pressure.py",
    "tests/adapters/test_linux_host.py",
    "tests/controller/test_host_capabilities.py",
    "tests/controller/test_host_pressure.py",
}

J2_BOOTSTRAP_ONLY = {
    "Makefile",
    "scripts/classify-qualification-impact.py",
    "scripts/lc0-hardware-probe.py",
    "scripts/online-hardware-probe.py",
    "tests/test_qualification_impact.py",
    ".github/workflows/engine-optimization.yml",
    ".github/workflows/full-game-qualification.yml",
    ".github/workflows/lc0-strength-qualification.yml",
    ".github/workflows/online-profile-qualification.yml",
    ".github/workflows/online-hybrid-qualification.yml",
    ".github/workflows/baseline.yml",
}


def _normalize(paths: list[str]) -> list[str] | None:
    normalized: list[str] = []
    for raw in paths:
        path = PurePosixPath(raw)
        value = path.as_posix()
        if value in ("", ".") or value.startswith("../"):
            return None
        normalized.append(value)
    return normalized


def classify(paths: list[str], *, base_sha: str | None = None) -> str:
    normalized = _normalize(paths)
    if not normalized:
        return "runtime_affected"

    changed = set(normalized)
    if changed.issubset(ALWAYS_CONTROL_PLANE):
        return "control_plane_only"

    bootstrap_surface = ALWAYS_CONTROL_PLANE | J2_BOOTSTRAP_ONLY
    if (
        base_sha == J2_BOOTSTRAP_BASE_SHA
        and changed.issubset(bootstrap_surface)
    ):
        return "control_plane_only"

    return "runtime_affected"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-sha")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    print(classify(args.paths, base_sha=args.base_sha))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
