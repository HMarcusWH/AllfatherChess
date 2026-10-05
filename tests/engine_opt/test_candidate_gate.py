#!/usr/bin/env python3
from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.engine_opt.candidate_gate import (  # noqa: E402
    CANONICAL_FILES,
    CANONICAL_TREES,
    CandidateGateError,
    compare_canonical_surface,
    evaluate_gate,
)

BOUND = "1" * 64
CURRENT = "2" * 64


def canonical(*, qualified: bool = False, digest: str = CURRENT) -> dict:
    failures = [] if qualified else [
        {
            "code": "NOT_QUALIFIED_REPEATABILITY",
            "message": "LC0 native-work vectors are not repeatable within the bound domain",
        },
        {
            "code": "NOT_QUALIFIED_BEHAVIORAL_EQUIVALENCE",
            "message": "selected LC0 profile changed the frozen baseline bestmove vector",
        },
    ]
    return {
        "evidence_valid": True,
        "invalid_evidence": [],
        "qualified": qualified,
        "passed": qualified,
        "qualification_failures": failures,
        "details": {
            "execution_domain": {
                "execution_domain_digest": digest,
            }
        },
    }


def candidate(*, digest: str = CURRENT) -> dict:
    return {
        "evidence_valid": True,
        "invalid_evidence": [],
        "candidate_qualified": True,
        "promotion_ready": True,
        "canonical_profile_changed": False,
        "passed": True,
        "qualification_disposition": "QUALIFIED_EXACT_HOST_ONLY",
        "qualification_failures": [],
        "details": {
            "execution_domain": {
                "execution_domain_digest": digest,
            }
        },
    }


def host_binding(*, portable: bool = False) -> dict:
    return {
        "host_binding": {
            "generic_host_portability_established": portable,
            "selection_eligible_on_unmatched_host": False,
        },
        "current_domain_bound_qualification": {
            "qualification_disposition": "QUALIFIED_EXACT_HOST_ONLY",
            "binding_scope": "exact_host_observation",
            "execution_domain_digest": BOUND,
            "generic_host_portability_established": False,
        },
    }


def surface(unchanged: bool = True) -> dict:
    return {
        "unchanged": unchanged,
        "changed_files": [] if unchanged else [CANONICAL_FILES[0]],
        "changed_trees": [],
    }


class GateTests(unittest.TestCase):
    def test_unmatched_host_known_diagnostics_can_pass_without_requalifying_canonical(self):
        report = evaluate_gate(
            canonical=canonical(),
            candidate=candidate(),
            host_binding=host_binding(),
            canonical_surface=surface(),
        )
        self.assertTrue(report["passed"])
        self.assertEqual(
            report["disposition"],
            "QUALIFIED_CANDIDATE_WITH_UNMATCHED_CANONICAL_DIAGNOSTIC",
        )
        self.assertEqual(
            report["canonical"]["status"],
            "UNMATCHED_HOST_DIAGNOSTIC_NOT_QUALIFIED",
        )
        self.assertFalse(report["canonical"]["qualified"])
        self.assertFalse(report["canonical"]["authority_on_current_host"])
        self.assertFalse(report["claim_boundary"]["canonical_unmatched_host_requalified"])

    def test_same_bound_host_canonical_failure_blocks(self):
        with self.assertRaisesRegex(CandidateGateError, "exact bound host"):
            evaluate_gate(
                canonical=canonical(digest=BOUND),
                candidate=candidate(digest=BOUND),
                host_binding=host_binding(),
                canonical_surface=surface(),
            )

    def test_changed_canonical_surface_blocks_unmatched_host_exception(self):
        with self.assertRaisesRegex(CandidateGateError, "surface changed"):
            evaluate_gate(
                canonical=canonical(),
                candidate=candidate(),
                host_binding=host_binding(),
                canonical_surface=surface(False),
            )

    def test_invalid_canonical_evidence_blocks(self):
        doc = canonical()
        doc["evidence_valid"] = False
        with self.assertRaisesRegex(CandidateGateError, "evidence is invalid"):
            evaluate_gate(
                canonical=doc,
                candidate=candidate(),
                host_binding=host_binding(),
                canonical_surface=surface(),
            )

    def test_unknown_canonical_failure_code_blocks(self):
        doc = canonical()
        doc["qualification_failures"] = [
            {"code": "NOT_QUALIFIED_SOMETHING_NEW", "message": "new failure"}
        ]
        with self.assertRaisesRegex(CandidateGateError, "non-host-diagnostic"):
            evaluate_gate(
                canonical=doc,
                candidate=candidate(),
                host_binding=host_binding(),
                canonical_surface=surface(),
            )

    def test_generic_portability_claim_makes_unmatched_failure_blocking(self):
        with self.assertRaisesRegex(CandidateGateError, "claims portability"):
            evaluate_gate(
                canonical=canonical(),
                candidate=candidate(),
                host_binding=host_binding(portable=True),
                canonical_surface=surface(),
            )

    def test_candidate_failure_or_canonical_mutation_blocks(self):
        for mutation in (
            ("promotion_ready", False),
            ("candidate_qualified", False),
            ("passed", False),
            ("canonical_profile_changed", True),
        ):
            doc = candidate()
            doc[mutation[0]] = mutation[1]
            with self.subTest(mutation=mutation), self.assertRaisesRegex(
                CandidateGateError,
                "candidate is not independently promotion-ready",
            ):
                evaluate_gate(
                    canonical=canonical(),
                    candidate=doc,
                    host_binding=host_binding(),
                    canonical_surface=surface(),
                )

    def test_candidate_and_canonical_must_share_current_domain(self):
        with self.assertRaisesRegex(CandidateGateError, "domains differ"):
            evaluate_gate(
                canonical=canonical(),
                candidate=candidate(digest="3" * 64),
                host_binding=host_binding(),
                canonical_surface=surface(),
            )

    def test_canonical_pass_remains_the_normal_path(self):
        report = evaluate_gate(
            canonical=canonical(qualified=True, digest=BOUND),
            candidate=candidate(digest=BOUND),
            host_binding=host_binding(),
            canonical_surface=surface(False),
        )
        self.assertEqual(report["disposition"], "QUALIFIED_CANONICAL_AND_CANDIDATE")
        self.assertTrue(report["canonical"]["qualified"])
        self.assertTrue(report["canonical"]["authority_on_current_host"])


class SurfaceTests(unittest.TestCase):
    def _run(self, root: Path, *args: str) -> str:
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

    def test_surface_comparison_detects_file_and_engine_tree_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._run(root, "init")
            self._run(root, "config", "user.email", "test@example.invalid")
            self._run(root, "config", "user.name", "test")
            for rel in CANONICAL_FILES:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(rel + "\n", encoding="utf-8")
            for rel in CANONICAL_TREES:
                path = root / rel / "sentinel.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(rel + "\n", encoding="utf-8")
            self._run(root, "add", ".")
            self._run(root, "commit", "-m", "base")
            base = self._run(root, "rev-parse", "HEAD")

            result = compare_canonical_surface(root, base)
            self.assertTrue(result["unchanged"])

            first = root / CANONICAL_FILES[0]
            first.write_text("changed\n", encoding="utf-8")
            result = compare_canonical_surface(root, base)
            self.assertFalse(result["unchanged"])
            self.assertIn(CANONICAL_FILES[0], result["changed_files"])

            self._run(root, "checkout", "--", CANONICAL_FILES[0])
            tree_file = root / CANONICAL_TREES[0] / "sentinel.txt"
            tree_file.write_text("changed tree\n", encoding="utf-8")
            self._run(root, "add", str(tree_file.relative_to(root)))
            self._run(root, "commit", "-m", "tree change")
            result = compare_canonical_surface(root, base)
            self.assertFalse(result["unchanged"])
            self.assertIn(CANONICAL_TREES[0], result["changed_trees"])


if __name__ == "__main__":
    unittest.main()
