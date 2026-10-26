"""S1: strictly separate G3 output-only control from J12 anchor control."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path
import unittest

from controller.decision import (
    ANCHOR_CONTROL, AUTHORIZED_HYBRID_OUTWARD_MODE,
    G3_ANCHOR_CONTROL_OUTWARD_MODE, ANCHOR_CONTROL_OUTWARD_MODE,
    CLOCKED_AUTHORIZATION_POLICY, DecisionAuthorization,
    select_final_decision, revoke_final_decision_to_anchor,
)
from controller.runtime import load_runtime_config, RuntimeError as RuntimeConfigError
from tools.local_game.common import (
    ROOT, QualificationError, runtime_config, verify_controller_derivation,
)
from tests.controller.test_online_hybrid_authority import evidence, proposal, snapshot


CONFIG = ROOT / "config/allfather.online-hybrid-v2.validation.json"
J12 = ROOT / "config/allfather.orchestrated-v1.validation.json"


def parse_effective(source):
    doc = copy.deepcopy(source)
    doc["root"] = "."
    for spec in doc["instances"].values():
        spec["binary"] = sys.executable
        spec.pop("fallback_glob", None)
    with tempfile.TemporaryDirectory() as td:
        path = Path(td)/"diag.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return load_runtime_config(path)


class G3DiagnosticControlTests(unittest.TestCase):
    def setUp(self):
        self.original = json.loads(CONFIG.read_text(encoding="utf-8"))

    def test_output_mode_has_narrow_G3_policy_only(self):
        good = copy.deepcopy(self.original)
        good["hybrid_authority"]["outward_mode"] = G3_ANCHOR_CONTROL_OUTWARD_MODE
        loaded = parse_effective(good)
        self.assertEqual(loaded.hybrid_authority.policy,
                         CLOCKED_AUTHORIZATION_POLICY)
        self.assertEqual(loaded.hybrid_authority.outward_mode,
                         G3_ANCHOR_CONTROL_OUTWARD_MODE)
        broken = copy.deepcopy(good)
        broken["hybrid_authority"]["policy"] = "bounded_preanchor_v0"
        with self.assertRaises(RuntimeConfigError):
            parse_effective(broken)
        j12 = json.loads(J12.read_text(encoding="utf-8"))
        j12["hybrid_authority"]["outward_mode"] = G3_ANCHOR_CONTROL_OUTWARD_MODE
        with self.assertRaises(RuntimeConfigError):
            parse_effective(j12)

    def test_J12_anchor_mode_still_requires_J12(self):
        original_j12 = json.loads(J12.read_text(encoding="utf-8"))
        original_j12["hybrid_authority"]["outward_mode"] = ANCHOR_CONTROL_OUTWARD_MODE
        self.assertEqual(parse_effective(original_j12).hybrid_authority.outward_mode,
                         ANCHOR_CONTROL_OUTWARD_MODE)
        bad = copy.deepcopy(self.original)
        bad["hybrid_authority"]["outward_mode"] = ANCHOR_CONTROL_OUTWARD_MODE
        with self.assertRaises(RuntimeConfigError):
            parse_effective(bad)

    def test_control_outward_only_with_identical_G3_proposal_and_authorization(self):
        ev = evidence()
        prop = proposal(ev)
        snap = snapshot()
        authorization = DecisionAuthorization(
            policy=CLOCKED_AUTHORIZATION_POLICY,
            authorized=True, move=prop.move,
            reason="test positive", snapshot_digest=snap.digest,
        )
        anchor = "d2d4"
        live = select_final_decision(
            anchor_move=anchor, proposal=prop, authorization=authorization,
            authorization_snapshot=snap,
            outward_mode=AUTHORIZED_HYBRID_OUTWARD_MODE,
        )
        control = select_final_decision(
            anchor_move=anchor, proposal=prop, authorization=authorization,
            authorization_snapshot=snap,
            outward_mode=G3_ANCHOR_CONTROL_OUTWARD_MODE,
        )
        self.assertEqual(live.emitted_move, "e2e4")
        self.assertEqual(control.emitted_move, anchor)
        self.assertEqual(live.authority, "HYBRID")
        self.assertEqual(control.authority, ANCHOR_CONTROL)
        for label in ("proposal_move", "authorization", "authorization_snapshot",
                      "proposal_evidence_digest", "anchor_move"):
            self.assertEqual(getattr(live, label), getattr(control, label))
        revoked = revoke_final_decision_to_anchor(
            control, reason="test late clock invalidation")
        self.assertEqual(revoked.authority, ANCHOR_CONTROL)
        self.assertEqual(revoked.emitted_move, anchor)
        self.assertFalse(revoked.authorization.authorized)

    def test_run_config_derivation_changes_only_output_mode_and_paths(self):
        root = ROOT / "build" / "strength-test"
        replay = root / "replays"
        derived = runtime_config(self.original, "allfather-g3-anchor-control",
                                 root, replay)
        live = runtime_config(self.original, "allfather-g3", root, replay)
        old_authority = dict(live["hybrid_authority"])
        expected_authority = dict(derived["hybrid_authority"])
        self.assertEqual(expected_authority.pop("outward_mode"),
                         G3_ANCHOR_CONTROL_OUTWARD_MODE)
        self.assertEqual(expected_authority, old_authority)
        verify_controller_derivation(self.original, derived,
                                     "allfather-g3-anchor-control",
                                     root, replay)
        modified = copy.deepcopy(derived)
        modified["routing"]["checkpoint_interval_ms"] += 1
        with self.assertRaises(QualificationError):
            verify_controller_derivation(self.original, modified,
                                         "allfather-g3-anchor-control", root, replay)

    def test_no_silent_coercion_of_the_source_runtime(self):
        altered = copy.deepcopy(self.original)
        altered["hybrid_authority"]["policy"] = "orchestrated_clocked_staged_preanchor_v1"
        with self.assertRaises(QualificationError):
            runtime_config(altered, "allfather-g3-anchor-control",
                           ROOT / "build/test", ROOT / "build/test/replays")
        altered = copy.deepcopy(self.original)
        altered["hybrid_authority"]["outward_mode"] = "anchor_control_v1"
        with self.assertRaises(QualificationError):
            runtime_config(altered, "allfather-g3-anchor-control",
                           ROOT / "build/test", ROOT / "build/test/replays")


if __name__ == "__main__":
    unittest.main()
