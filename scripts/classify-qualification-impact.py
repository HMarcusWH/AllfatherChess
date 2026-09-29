#!/usr/bin/env python3
"""Classify whether a diff can affect legacy chess execution.

The classifier is intentionally fail-closed. Only two narrow evidence/control
surfaces are exempt from expensive legacy requalification:
- J2 host observation contracts;
- J2-R qualification/evidence plumbing.

Anything else is runtime_affected.
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

QUALIFICATION_INFRA_ONLY = {
    "controller/runtime_substrate.py",
    "tests/controller/test_runtime_substrate.py",
    "scripts/lc0-hardware-probe.py",
    "scripts/online-hardware-probe.py",
    "scripts/lc0-strength-profile-contract.py",
    "scripts/qualify-online-profile.py",
    "tools/engine_opt/report.py",
    "scripts/engine-opt-matrix.py",
    "scripts/qualify-online-hybrid-v2.py",
    "tools/local_game/runner.py",
    "tools/local_game/validate.py",
    "scripts/qualify-engine-opt-evidence.py",
    "qualification/engine-opt-v2-host-binding.json",
    "tools/engine_opt/domain.py",
    "tests/engine_opt/test_execution_domain.py",
    "scripts/engine-opt-binary-diagnostic.py",
}


def classify(paths: list[str]) -> str:
    normalized = []
    for raw in paths:
        path = PurePosixPath(raw)
        value = path.as_posix()
        if value in ("", ".") or value.startswith("../"):
            return "runtime_affected"
        normalized.append(value)
    if not normalized:
        return "runtime_affected"
    if all(path in CONTROL_PLANE_ONLY for path in normalized):
        return "control_plane_only"
    allowed = CONTROL_PLANE_ONLY | QUALIFICATION_INFRA_ONLY
    if all(path in allowed for path in normalized):
        return "qualification_infra_only"
    return "runtime_affected"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    print(classify(args.paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
