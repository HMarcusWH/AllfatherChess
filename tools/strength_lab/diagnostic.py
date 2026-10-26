"""S1: source-only G3 anchor-output control contract; does not run games.

Actual real-engine paired diagnostics and cgroup participant accounting are
separate qualification stages, not implied by a positive output-mode test.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import subprocess

from tools.local_game.common import ROOT, load, require, runtime_config

POLICY_PATH = ROOT / "qualification/strength/g3-output-control-v1.json"


def contract() -> dict:
    p = load(POLICY_PATH)
    require(p.get("schema_version") == 1
            and p.get("profile_id") == "strength-s1-g3-output-only-v1"
            and p.get("status") == "EXPERIMENTAL_CONTROL_IMPLEMENTED_NOT_MATCH_QUALIFIED",
            "unknown S1 source contract")
    for flag in ("same_compute_claim", "whole_participant_resource_qualified",
                 "equal_compute", "strength_qualified", "live_game_authorized",
                 "public_bot_release", "artifact_distribution_authorized"):
        require(p.get(flag) is False,
                "S1 output-only control cannot promote experimental claim")
    source_path = ROOT / p["source_runtime"]
    blob = subprocess.check_output(
        ["git", "-C", str(ROOT), "hash-object", "--", str(source_path)],
        text=True,
    ).strip()
    require(blob == p["source_runtime_git_blob"],
            "G3 diagnostic source config no longer has frozen Git blob")
    source = load(source_path)
    require(source.get("hybrid_authority", {}).get("policy")
            == p["source_policy"], "source has wrong G3 move-authority policy")
    require(source.get("hybrid_authority", {}).get("outward_mode",
            "authorized_hybrid_v1") == "authorized_hybrid_v1",
            "original G3 policy already has an experimental outward mode")
    root = ROOT / "build" / "strength" / "s1" / "isolated"
    replay = root / "replays"
    original = runtime_config(source, p["comparison_arm"], root, replay)
    control = runtime_config(source, p["control_arm"], root, replay)
    assert original is not control
    left, right = copy.deepcopy(original), copy.deepcopy(control)
    require(left["hybrid_authority"].get("outward_mode",
            "authorized_hybrid_v1") == "authorized_hybrid_v1",
            "comparison arm was not G3 live-authority source")
    require(right["hybrid_authority"].pop("outward_mode", None)
            == p["control_outward_mode"],
            "control arm did not suppress outward authority")
    require(left == right and p["only_runtime_difference"]
            == "hybrid_authority.outward_mode"
            and p["underlying_authorization_unchanged"] is True
            and p["outward_authority"] == "ANCHOR_CONTROL",
            "G3 diagnostic changed more than outbound selection mode")
    return {
        "schema_version": 1,
        "status": "S1_OUTPUT_ONLY_SOURCE_CONTRACT_CONFIRMED",
        "source_git_blob": blob,
        "control_arm": p["control_arm"],
        "source_runtime": p["source_runtime"],
        "only_effective_difference": p["only_runtime_difference"],
        "real_games_completed": 0,
        "resource_domain_qualified": False,
        "strength_qualified": False,
        "live_game_authorized": False,
        "public_bot_release": False,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", choices=["contracts"], default="contracts")
    args = ap.parse_args()
    del args
    print(json.dumps(contract(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
