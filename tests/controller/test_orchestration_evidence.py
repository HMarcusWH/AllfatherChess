#!/usr/bin/env python3
"""Contract tests for M14-J orchestration provenance objects."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.orchestration_evidence import (
    ORCHESTRATION_EVIDENCE_VERSION,
    OrchestrationEvidence,
    ProfileApplicationEvidence,
)
from controller.resource_profiles import MutationBoundary, OrchestrationContractError


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


def application(*, success: bool = True) -> ProfileApplicationEvidence:
    return ProfileApplicationEvidence(
        instance="lc0-shadow",
        profile_id="lc0/cpu-small",
        profile_digest=SHA_A,
        boundary=MutationBoundary.GAME,
        effective_options_digest=SHA_B,
        resource_state_digest=SHA_C,
        success=success,
        faults=() if success else ("profile application failed",),
    )


def evidence(
    *,
    application_ids: tuple[str, ...] | None = None,
    grant_ids: tuple[str, ...] = (SHA_E,),
) -> OrchestrationEvidence:
    if application_ids is None:
        application_ids = (application().application_id,)
    return OrchestrationEvidence(
        evidence_version=ORCHESTRATION_EVIDENCE_VERSION,
        run_id="run-7",
        generation=7,
        position_id="position-7",
        game_environment_digest=SHA_A,
        composition_profile_digest=SHA_B,
        profile_catalog_digest=SHA_C,
        application_ids=application_ids,
        grant_ids=grant_ids,
        faults=(),
    )


class OrchestrationEvidenceContractTests(unittest.TestCase):
    def test_profile_application_round_trip_and_authority_firewall(self):
        item = application()
        raw = item.as_dict()
        restored = ProfileApplicationEvidence.from_dict(raw)
        self.assertEqual(item, restored)
        self.assertEqual(item.application_id, restored.application_id)
        self.assertFalse(raw["authority"]["resource_authorization"])
        self.assertFalse(raw["authority"]["outward_move"])

    def test_failed_application_must_keep_fault_and_success_may_not(self):
        with self.assertRaises(OrchestrationContractError):
            ProfileApplicationEvidence(
                instance="lc0-shadow",
                profile_id="lc0/cpu-small",
                profile_digest=SHA_A,
                boundary="game",
                effective_options_digest=SHA_B,
                resource_state_digest=SHA_C,
                success=False,
                faults=(),
            )
        with self.assertRaises(OrchestrationContractError):
            ProfileApplicationEvidence(
                instance="lc0-shadow",
                profile_id="lc0/cpu-small",
                profile_digest=SHA_A,
                boundary="game",
                effective_options_digest=SHA_B,
                resource_state_digest=SHA_C,
                success=True,
                faults=("should not exist",),
            )

    def test_application_id_changes_when_effective_state_changes(self):
        first = application()
        second = ProfileApplicationEvidence(
            instance=first.instance,
            profile_id=first.profile_id,
            profile_digest=first.profile_digest,
            boundary=first.boundary,
            effective_options_digest=SHA_D,
            resource_state_digest=first.resource_state_digest,
            success=True,
            faults=(),
        )
        self.assertNotEqual(first.application_id, second.application_id)

    def test_orchestration_evidence_round_trip_and_stable_digest(self):
        first = evidence()
        raw = first.as_dict()
        restored = OrchestrationEvidence.from_dict(raw)
        self.assertEqual(first, restored)
        self.assertEqual(first.digest, restored.digest)
        self.assertFalse(raw["authority"]["resource_authorization"])
        self.assertFalse(raw["authority"]["outward_move"])

    def test_grant_order_is_claim_bearing(self):
        first = evidence(grant_ids=(SHA_D, SHA_E))
        second = evidence(grant_ids=(SHA_E, SHA_D))
        self.assertNotEqual(first.digest, second.digest)

    def test_duplicate_grants_or_applications_are_rejected(self):
        app = application().application_id
        with self.assertRaises(OrchestrationContractError):
            evidence(application_ids=(app, app))
        with self.assertRaises(OrchestrationContractError):
            evidence(grant_ids=(SHA_E, SHA_E))

    def test_bool_generation_and_bad_digest_are_rejected(self):
        with self.assertRaises(OrchestrationContractError):
            OrchestrationEvidence(
                evidence_version=ORCHESTRATION_EVIDENCE_VERSION,
                run_id="run-1",
                generation=True,
                position_id="p1",
                game_environment_digest=SHA_A,
                composition_profile_digest=SHA_B,
                profile_catalog_digest=SHA_C,
                application_ids=(),
                grant_ids=(),
            )
        with self.assertRaises(OrchestrationContractError):
            OrchestrationEvidence(
                evidence_version=ORCHESTRATION_EVIDENCE_VERSION,
                run_id="run-1",
                generation=1,
                position_id="p1",
                game_environment_digest="BAD",
                composition_profile_digest=SHA_B,
                profile_catalog_digest=SHA_C,
                application_ids=(),
                grant_ids=(),
            )

    def test_tampered_application_id_or_authority_marker_is_rejected(self):
        raw = application().as_dict()
        tampered = copy.deepcopy(raw)
        tampered["application_id"] = SHA_D
        with self.assertRaises(OrchestrationContractError):
            ProfileApplicationEvidence.from_dict(tampered)

        raw = evidence().as_dict()
        tampered = copy.deepcopy(raw)
        tampered["authority"]["outward_move"] = True
        with self.assertRaises(OrchestrationContractError):
            OrchestrationEvidence.from_dict(tampered)

        hidden = copy.deepcopy(raw)
        hidden["authorized_move"] = "e2e4"
        with self.assertRaises(OrchestrationContractError):
            OrchestrationEvidence.from_dict(hidden)

        application_raw = application().as_dict()
        application_raw.pop("application_id")
        with self.assertRaises(OrchestrationContractError):
            ProfileApplicationEvidence.from_dict(application_raw)


if __name__ == "__main__":
    unittest.main()
