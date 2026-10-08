#!/usr/bin/env python3
"""Regression tests for promotion-vs-candidate evidence path and J12 identity gates."""
from __future__ import annotations

import copy
import runpy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
api = runpy.run_path(str(ROOT / "scripts/qualify-engine-opt-evidence.py"))
find_one = api["find_one"]
local1_campaign_pattern = api["local1_campaign_pattern"]
j12_campaign_pattern = api["j12_campaign_pattern"]
validate_j12_campaign_metadata = api["validate_j12_campaign_metadata"]
QualificationError = api["QualificationError"]

SOURCE = "a" * 40
POLICY = "b" * 64
BUNDLE = {"source_commit": SOURCE, "build_manifest_sha256": "c" * 64}


def fixtures():
    local = {
        "passed": True, "errors": [], "execution_scope": "required_local1",
        "validated_games": 28, "observed_games": 28,
        "claim_boundary": {"full_game_lifecycle": True},
        "candidate_bundle": copy.deepcopy(BUNDLE),
    }
    manifest = {
        "source": {"commit": SOURCE}, "status": "completed",
        "failures": [], "mode": "required",
        "policy": {
            "path": "qualification/local-full-game-orchestrated-v1.json",
            "sha256": POLICY,
        },
        "candidate_bundle": copy.deepcopy(BUNDLE),
        "prerequisites": [
            {"id": "engine-opt-v2", "returncode": 0, "timed_out": False},
            {"id": "j12", "returncode": 0, "timed_out": False},
        ],
    }
    prerequisite = {
        "schema_version": 1, "profile_id": "allfather.orchestrated-v1",
        "mechanism_valid": True, "source_commit": SOURCE,
        "candidate_bundle": copy.deepcopy(BUNDLE),
        "work_grants_authorized": 9, "work_grants_settled": 9,
        "open_reservations": 0, "allocation_action": "BUY_BUNDLE",
        "authority_qualified": False, "qualification_disposition": "NOT_QUALIFIED_HOST_CAPACITY",
    }
    return local, manifest, prerequisite


class EngineOptAggregatePathTests(unittest.TestCase):
    def test_candidate_and_canonical_campaign_paths_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for dir_name in ("local1", "canonical-local1", "j12-local1"):
                report = root / "engine-opt-v2-profile-domain" / "artifact" / dir_name / "campaign" / "report.json"
                report.parent.mkdir(parents=True)
                report.write_text("{}", encoding="utf-8")
            self.assertIn(
                "canonical-local1",
                str(find_one(root, local1_campaign_pattern(candidate_mode=False))),
            )
            self.assertIn(
                "/local1/",
                str(find_one(root, local1_campaign_pattern(candidate_mode=True))),
            )
            self.assertIn("j12-local1", str(find_one(root, j12_campaign_pattern())))
            (root / "engine-opt-v2-profile-domain" / "artifact" / "local1" / "campaign" / "report.json").unlink()
            with self.assertRaisesRegex(QualificationError, "expected one file, found 0"):
                find_one(root, local1_campaign_pattern(candidate_mode=True))
            duplicate = root / "engine-opt-v2-profile-domain" / "artifact2" / "canonical-local1" / "campaign" / "report.json"
            duplicate.parent.mkdir(parents=True)
            duplicate.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(QualificationError, "expected one file, found 2"):
                find_one(root, local1_campaign_pattern(candidate_mode=False))

    def test_qualified_j12_lifecycle_does_not_require_non_anchor_hybrid(self):
        validate_j12_campaign_metadata(
            *fixtures(), source=SOURCE, candidate_bundle=BUNDLE, policy_sha256=POLICY,
        )

    def test_wrong_exact_head_bundle_or_frozen_policy_rejected(self):
        mutations = [
            ("source", lambda l, m, j: m["source"].__setitem__("commit", "d" * 40)),
            ("bundle", lambda l, m, j: j["candidate_bundle"].__setitem__("source_commit", "d" * 40)),
            ("policy", lambda l, m, j: m["policy"].__setitem__("sha256", "d" * 64)),
            ("games", lambda l, m, j: l.__setitem__("validated_games", 27)),
            ("errors", lambda l, m, j: l["errors"].append("invalid")),
            ("authority", lambda l, m, j: j.__setitem__("mechanism_valid", False)),
            ("grants", lambda l, m, j: j.__setitem__("work_grants_settled", 8)),
            ("prerequisite", lambda l, m, j: m["prerequisites"][1].__setitem__("timed_out", True)),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name):
                local, manifest, prerequisite = fixtures()
                mutate(local, manifest, prerequisite)
                with self.assertRaises(QualificationError):
                    validate_j12_campaign_metadata(
                        local, manifest, prerequisite,
                        source=SOURCE, candidate_bundle=BUNDLE, policy_sha256=POLICY,
                    )


if __name__ == "__main__":
    unittest.main()
