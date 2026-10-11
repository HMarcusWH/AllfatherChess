#!/usr/bin/env python3
"""Canonical b4 continuity: fail-closed positive and negative admission tests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.engine_opt.continuity_gate import (
    B4, PROTECTED, ENGINE_TREES, RUNTIME_FILES,
    compare_continuity_surface, evaluate_continuity_gate,
)
from tools.engine_opt.promotion_gate import EXPECTED_LC0, PromotionGateError
import importlib.util
SCRIPT = Path(__file__).resolve().parents[2] / "scripts/qualify-engine-opt-v2-canonical-gate.py"
SPEC = importlib.util.spec_from_file_location("online_canonical_gate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
select_mode, evaluate, AUTHORIZED_MANUAL_BASE = (
    MODULE.select_mode, MODULE.evaluate, MODULE.AUTHORIZED_MANUAL_BASE
)

HEAD = "a" * 40
BASE = "b" * 40
POLICY = "c" * 64
DOMAIN = "d" * 64


def selection(profile=B4):
    return {"selected": {"lc0": {**EXPECTED_LC0, "matrix_profile": profile}}}


def good_aggregate():
    return {
        "source_commit": HEAD, "evidence_valid": True,
        "passed": True, "profile_qualified": True,
        "invalid_evidence": [], "qualification_failures": [],
        "qualification_disposition": "QUALIFIED_EXACT_HOST_ONLY",
        "details": {
            "lc0": {"selected_profile": B4, "native_work_policy": "lc0-node-stop-contract-v1"},
            "local1_g3": {"validated_games": 28, "g3_authority_qualified": True,
                          "g3_policy_sha256": POLICY},
            "execution_domain": {"execution_domain_digest": DOMAIN},
        },
    }


def good_surface():
    return {"head_sha": HEAD, "base_sha": BASE,
            "authorized_profile_change": False,
            "canonical_b4_continuity_verified": True,
            "g3_b4_policy_sha256": POLICY}


class ContinuityAdmissionTests(unittest.TestCase):
    def test_distinguish_promotion_continuity_and_unknown_transition(self):
        self.assertEqual(select_mode(selection("b7-p8-c256k-warm64"), selection()), "promotion")
        self.assertEqual(select_mode(selection(), selection()), "continuity")
        with self.assertRaises(PromotionGateError):
            select_mode(selection(), selection("b9-unapproved"))
        with self.assertRaises(PromotionGateError):
            select_mode({}, selection())

    def test_exact_head_b4_continuity_is_not_promotion(self):
        result = evaluate_continuity_gate(aggregate=good_aggregate(), surface=good_surface(),
                                          current_head=HEAD)
        self.assertEqual(result["disposition"], "CANONICAL_B4_CONTINUITY_QUALIFIED")
        self.assertFalse(result["claim_boundary"]["canonical_b4_promotion"])
        self.assertFalse(result["claim_boundary"]["generic_host_portability"])
        self.assertFalse(result["claim_boundary"]["deployment"])

    def test_bad_evidence_never_certifies(self):
        for change in (
            {"source_commit": "f" * 40},
            {"evidence_valid": False},
            {"invalid_evidence": [{"code": "wrong"}]},
            {"qualification_disposition": "NOT_QUALIFIED_HOST_CAPACITY"},
            {"details": {"lc0": {"selected_profile": "b7-p8-c256k-warm64"}}},
            {"details": {"local1_g3": {"validated_games": 23}}},
        ):
            bad = good_aggregate()
            bad.update(change)
            with self.subTest(change=change), self.assertRaises(PromotionGateError):
                evaluate_continuity_gate(aggregate=bad, surface=good_surface(),
                                         current_head=HEAD)
        for change in ({"authorized_profile_change": True},
                       {"g3_b4_policy_sha256": "f" * 64},
                       {"head_sha": "f" * 40}):
            surface = {**good_surface(), **change}
            with self.subTest(change=change), self.assertRaises(PromotionGateError):
                evaluate_continuity_gate(aggregate=good_aggregate(), surface=surface,
                                         current_head=HEAD)

    def test_manual_baseline_not_supplied_or_arbitrary_is_refused(self):
        for ref in ("", BASE, "abc"):
            with self.subTest(ref=ref), self.assertRaises(PromotionGateError):
                evaluate(aggregate=good_aggregate(), host_binding={}, catalog={},
                         root=Path("/unused"), base_sha=ref,
                         event_name="workflow_dispatch")
        with self.assertRaises(PromotionGateError):
            evaluate(aggregate=good_aggregate(), host_binding={}, catalog={},
                     root=Path("/unused"), base_sha=AUTHORIZED_MANUAL_BASE,
                     event_name="unrecognized_event")

    def test_canonical_protected_files_and_engine_trees_are_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selection_file = "qualification/engine-opt-v2-selection.json"
            catalog_file = "qualification/resource-profile-catalog-v1.json"
            base_docs = {}
            for path in PROTECTED:
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                if path == selection_file:
                    content = json.dumps(selection()).encode()
                elif path == catalog_file:
                    content = json.dumps({
                        "selection_enabled": False, "fallback_profile": "engine-opt-v2",
                        "profiles": [{"profile_id": "lc0/specialist-engine-opt-v2",
                                      "options": [{"name": "MinibatchSize", "value": 4},
                                                  {"name": "MaxPrefetch", "value": 0}]}],
                        "profile_runtime": {"lc0/specialist-engine-opt-v2": {"warmup": None}},
                    }).encode()
                else:
                    content = b"{}"
                target.write_bytes(content)
                base_docs[path] = content
            def mock_git(_root, *args, text=True):
                if args[0] == "show":
                    commit_path = args[1]
                    source, path = commit_path.split(":", 1)
                    return base_docs[path] if source == BASE else (root / path).read_bytes()
                if args[0] == "rev-parse" and args[1].split(":")[-1] in ENGINE_TREES:
                    return "f" * 40
                raise AssertionError(args)
            identity = {"base_sha": BASE, "head_sha": HEAD, "parent_sha": "e" * 40}
            with patch("tools.engine_opt.continuity_gate.verify_promotion_git_history", return_value=identity), \
                 patch("tools.engine_opt.continuity_gate._git", side_effect=mock_git), \
                 patch("tools.engine_opt.continuity_gate._base_json", return_value=selection()), \
                 patch("tools.engine_opt.continuity_gate._assert_b4_runtime"), \
                 patch("tools.engine_opt.continuity_gate.validate_b4_policy"), \
                 patch("tools.engine_opt.continuity_gate.file_hash", return_value=POLICY):
                valid = compare_continuity_surface(root, BASE)
                self.assertTrue(valid["canonical_b4_continuity_verified"])
                self.assertEqual(set(valid["guard_files_unchanged"]), set(PROTECTED))
                (root / RUNTIME_FILES[0]).write_text('{"changed":true}')
                with self.assertRaisesRegex(PromotionGateError, "canonical guard changed"):
                    compare_continuity_surface(root, BASE)
                (root / RUNTIME_FILES[0]).write_bytes(base_docs[RUNTIME_FILES[0]])
                (root / selection_file).write_text(json.dumps(selection("b9-unapproved")))
                with self.assertRaises(PromotionGateError):
                    compare_continuity_surface(root, BASE)


if __name__ == "__main__":
    unittest.main()
