#!/usr/bin/env python3
"""M14-J J13 META-1 anchor-control regressions."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.decision import (
    ANCHOR_CONTROL,
    ANCHOR_CONTROL_OUTWARD_MODE,
    AUTHORIZED_HYBRID_OUTWARD_MODE,
    ORCHESTRATED_CLOCKED_AUTHORIZATION_POLICY,
    DecisionAuthorization,
    revoke_final_decision_to_anchor,
    select_final_decision,
)
from controller.orchestration_integrity import (
    runtime_config_matches_frozen,
    runtime_config_matches_meta1_control,
)
from controller.runtime import RuntimeError as ControllerRuntimeError
from controller.runtime import load_runtime_config
from tests.controller.test_orchestrated_authority import evidence, proposal, snapshot


J12 = ROOT / "config/allfather.orchestrated-v1.validation.json"


def _load_document(document: dict):
    raw = copy.deepcopy(document)
    for spec in raw.get("instances", {}).values():
        spec["binary"] = sys.executable
        spec.pop("fallback_glob", None)
    raw["root"] = "."
    directory = tempfile.TemporaryDirectory()
    path = Path(directory.name) / "runtime.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    try:
        return load_runtime_config(path)
    finally:
        directory.cleanup()


class Meta1DecisionTests(unittest.TestCase):
    def _granted(self):
        ev = evidence()
        prop = proposal(ev)
        snap = snapshot()
        auth = DecisionAuthorization(
            policy=ORCHESTRATED_CLOCKED_AUTHORIZATION_POLICY,
            authorized=True,
            move=prop.move,
            reason="test grant",
            snapshot_digest=snap.digest,
        )
        return ev, prop, snap, auth

    def test_same_authorization_live_changes_move_control_does_not(self):
        _, prop, snap, auth = self._granted()
        live = select_final_decision(
            anchor_move="d2d4",
            proposal=prop,
            authorization=auth,
            authorization_snapshot=snap,
            outward_mode=AUTHORIZED_HYBRID_OUTWARD_MODE,
        )
        control = select_final_decision(
            anchor_move="d2d4",
            proposal=prop,
            authorization=auth,
            authorization_snapshot=snap,
            outward_mode=ANCHOR_CONTROL_OUTWARD_MODE,
        )
        self.assertEqual(live.authorization, control.authorization)
        self.assertEqual(live.authorization_snapshot, control.authorization_snapshot)
        self.assertEqual(live.proposal_move, control.proposal_move)
        self.assertEqual(live.authority, "HYBRID")
        self.assertEqual(live.emitted_move, "e2e4")
        self.assertEqual(control.authority, ANCHOR_CONTROL)
        self.assertEqual(control.emitted_move, "d2d4")
        self.assertTrue(control.authorization.authorized)

    def test_control_revocation_preserves_experimental_arm(self):
        _, prop, snap, auth = self._granted()
        control = select_final_decision(
            anchor_move="d2d4",
            proposal=prop,
            authorization=auth,
            authorization_snapshot=snap,
            outward_mode=ANCHOR_CONTROL_OUTWARD_MODE,
        )
        revoked = revoke_final_decision_to_anchor(
            control,
            reason="clock authority revoked before publication",
        )
        self.assertEqual(revoked.authority, ANCHOR_CONTROL)
        self.assertEqual(revoked.emitted_move, "d2d4")
        self.assertFalse(revoked.authorization.authorized)
        self.assertTrue(revoked.authorization_snapshot.authority_blocked)

    def test_control_runtime_is_only_available_to_orchestrated_policy(self):
        doc = json.loads(J12.read_text(encoding="utf-8"))
        doc["hybrid_authority"]["outward_mode"] = ANCHOR_CONTROL_OUTWARD_MODE
        config = _load_document(doc)
        self.assertEqual(config.hybrid_authority.outward_mode, ANCHOR_CONTROL_OUTWARD_MODE)

        historical = copy.deepcopy(doc)
        historical["hybrid_authority"]["policy"] = "clocked_staged_preanchor_v1"
        historical["routing"]["policy"] = "unified_value_v1"
        with self.assertRaises(ControllerRuntimeError):
            _load_document(historical)


class Meta1ConfigIdentityTests(unittest.TestCase):
    def test_control_matcher_allows_exactly_relocation_and_control_bit(self):
        frozen = json.loads(J12.read_text(encoding="utf-8"))
        actual = copy.deepcopy(frozen)
        actual["root"] = "/tmp/meta1"
        actual["shadow"]["replay_root"] = "/tmp/meta1/replays"
        actual["hybrid_authority"]["outward_mode"] = ANCHOR_CONTROL_OUTWARD_MODE

        self.assertFalse(
            runtime_config_matches_frozen(
                actual,
                frozen,
                allow_relocation=True,
            )
        )
        self.assertTrue(
            runtime_config_matches_meta1_control(
                actual,
                frozen,
                allow_relocation=True,
            )
        )

        tampered = copy.deepcopy(actual)
        tampered["routing"]["checkpoint_interval_ms"] += 1
        self.assertFalse(
            runtime_config_matches_meta1_control(
                tampered,
                frozen,
                allow_relocation=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
