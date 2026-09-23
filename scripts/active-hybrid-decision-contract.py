#!/usr/bin/env python3
"""Contract check for the shipped M14-C hybrid-authority profile."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from controller.runtime import load_runtime_config


def main() -> int:
    source = ROOT / "config" / "allfather.hybrid.validation.json"
    document = json.loads(source.read_text(encoding="utf-8"))

    # Contract CI does not require built vendored engines. Preserve every
    # controller setting while redirecting process paths to a real executable.
    for spec in document.get("instances", {}).values():
        spec["binary"] = sys.executable
        spec.pop("fallback_glob", None)
    document["root"] = "."

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "hybrid.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        config = load_runtime_config(path)

    assert config.mode == "active"
    assert config.crossfeed is not None and config.crossfeed.enabled
    assert config.counterfactual is not None and config.counterfactual.enabled
    assert config.hybrid_authority is not None and config.hybrid_authority.enabled
    assert config.hybrid_authority.policy == "bounded_preanchor_v0"
    assert config.hybrid_authority.request_class == "movetime_v0"
    assert config.resource_measurement is not None
    assert config.resource_measurement.enabled
    assert config.resource_measurement.require_cpu_for_claim
    assert not config.resource_measurement.require_gpu_for_claim

    print(
        json.dumps(
            {
                "status": "ok",
                "mode": config.mode,
                "policy": config.hybrid_authority.policy,
                "request_class": config.hybrid_authority.request_class,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
