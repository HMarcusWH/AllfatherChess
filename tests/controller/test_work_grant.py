#!/usr/bin/env python3
"""Contract tests for M14-J WorkChunk and WorkGrant."""

from __future__ import annotations

import copy
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.resource_profiles import (
    AcceleratorKind,
    ArtifactIdentity,
    EngineResourceProfile,
    MutationBoundary,
    OrchestrationContractError,
    ProcessIdentity,
    ProfileOption,
    QualificationIdentity,
)
from controller.work_grant import NativeLimit, WorkChunk, WorkGrant


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
COMMIT = "1" * 40
DOMAIN_DIGEST = "e" * 64
DOMAIN_ID = f"exec-domain/{DOMAIN_DIGEST[:20]}"


def profile(
    *,
    family: str = "lc0",
    profile_id: str = "lc0/cpu-small",
    chunk_ids: tuple[str, ...] = ("lc0/explore-small",),
) -> EngineResourceProfile:
    return EngineResourceProfile(
        profile_id=profile_id,
        family=family,
        process_identity=ProcessIdentity(
            binary_sha256=SHA_A,
            artifacts=(ArtifactIdentity("network", SHA_B),),
            backend="blas" if family == "lc0" else "native",
        ),
        options=(
            ProfileOption(
                "MultiPV",
                1,
                MutationBoundary.SEARCH,
                "EXPLORE",
            ),
        ),
        cpu_slots=1,
        expected_memory_mib=256,
        accelerator=AcceleratorKind.CPU,
        accelerator_memory_mib=0,
        work_chunk_ids=chunk_ids,
        qualification=QualificationIdentity(
            source_commit=COMMIT,
            evidence_sha256=SHA_C,
            evidence_id="evidence-v1",
            execution_domain_id=DOMAIN_ID,
            execution_domain_digest=DOMAIN_DIGEST,
            binding_scope="exact_host_observation",
        ),
    )


def chunk(
    *,
    family: str = "lc0",
    chunk_id: str = "lc0/explore-small",
    semantics: str = "lc0.uci_nodes",
) -> WorkChunk:
    return WorkChunk(
        chunk_id=chunk_id,
        family=family,
        phase="EXPLORE",
        purpose="solver",
        native_limit=NativeLimit("nodes", 128, semantics),
        reserved_cpu_ms=500.0,
        reserved_gpu_ms=0.0,
        wall_bound_ms=600.0,
        cost_evidence_digest=SHA_D,
    )


def grant() -> WorkGrant:
    return WorkGrant.from_chunk(
        profile=profile(),
        chunk=chunk(),
        generation=7,
        position_id="position-7",
        owner="lc0",
        instance="lc0-shadow",
        wall_deadline_ms=950.0,
        effective_options_digest=SHA_A,
        allocator_decision_digest=SHA_B,
    )


class WorkGrantContractTests(unittest.TestCase):
    def test_chunk_round_trip_and_native_semantics(self):
        item = chunk()
        self.assertEqual(WorkChunk.from_dict(item.as_dict()), item)
        self.assertFalse(item.as_dict()["authority"]["outward_move"])

        with self.assertRaises(OrchestrationContractError):
            chunk(semantics="stockfish.uci_nodes")

    def test_lc0_nodes_remain_lc0_native_not_alpha_beta_currency(self):
        item = chunk()
        self.assertEqual(item.native_limit.semantics, "lc0.uci_nodes")
        self.assertNotEqual(item.native_limit.semantics, "stockfish.uci_nodes")
        self.assertNotEqual(item.native_limit.semantics, "reckless.uci_nodes")

    def test_phase_purpose_mismatch_is_rejected(self):
        with self.assertRaises(OrchestrationContractError):
            WorkChunk(
                chunk_id="lc0/bad-verify",
                family="lc0",
                phase="VERIFY",
                purpose="solver",
                native_limit=NativeLimit("nodes", 16, "lc0.uci_nodes"),
                reserved_cpu_ms=100.0,
                reserved_gpu_ms=0.0,
                wall_bound_ms=200.0,
                cost_evidence_digest=SHA_D,
            )

    def test_nonfinite_or_zero_resource_claims_are_rejected(self):
        with self.assertRaises(OrchestrationContractError):
            WorkChunk(
                chunk_id="lc0/nan",
                family="lc0",
                phase="EXPLORE",
                purpose="solver",
                native_limit=NativeLimit("nodes", 16, "lc0.uci_nodes"),
                reserved_cpu_ms=math.nan,
                reserved_gpu_ms=0.0,
                wall_bound_ms=200.0,
                cost_evidence_digest=SHA_D,
            )
        with self.assertRaises(OrchestrationContractError):
            WorkChunk(
                chunk_id="lc0/zero",
                family="lc0",
                phase="EXPLORE",
                purpose="solver",
                native_limit=NativeLimit("nodes", 16, "lc0.uci_nodes"),
                reserved_cpu_ms=0.0,
                reserved_gpu_ms=0.0,
                wall_bound_ms=200.0,
                cost_evidence_digest=SHA_D,
            )

    def test_negative_zero_is_canonicalized_for_resource_identity(self):
        first = chunk()
        second = WorkChunk(
            chunk_id=first.chunk_id,
            family=first.family,
            phase=first.phase,
            purpose=first.purpose,
            native_limit=first.native_limit,
            reserved_cpu_ms=first.reserved_cpu_ms,
            reserved_gpu_ms=-0.0,
            wall_bound_ms=first.wall_bound_ms,
            cost_evidence_digest=first.cost_evidence_digest,
        )
        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertEqual(first.digest, second.digest)

    def test_grant_round_trip_and_identity_are_stable(self):
        first = grant()
        raw = first.as_dict()
        restored = WorkGrant.from_dict(raw)
        self.assertEqual(first, restored)
        self.assertEqual(first.grant_id, restored.grant_id)
        self.assertEqual(first.digest, first.grant_id)
        self.assertTrue(raw["authority"]["resource_authorization"])
        self.assertFalse(raw["authority"]["outward_move"])
        self.assertNotIn("move", raw)

    def test_one_claim_bearing_change_changes_grant_id(self):
        first = grant()
        changed = WorkGrant(
            generation=first.generation,
            position_id=first.position_id,
            owner=first.owner,
            instance=first.instance,
            profile_id=first.profile_id,
            profile_digest=first.profile_digest,
            phase=first.phase,
            purpose=first.purpose,
            work_chunk_id=first.work_chunk_id,
            work_chunk_digest=first.work_chunk_digest,
            native_limit=first.native_limit,
            reserved_cpu_ms=first.reserved_cpu_ms + 1.0,
            reserved_gpu_ms=first.reserved_gpu_ms,
            wall_deadline_ms=first.wall_deadline_ms,
            effective_options_digest=first.effective_options_digest,
            allocator_decision_digest=first.allocator_decision_digest,
        )
        self.assertNotEqual(first.grant_id, changed.grant_id)

    def test_tampered_grant_id_and_authority_marker_fail_closed(self):
        raw = grant().as_dict()
        tampered = copy.deepcopy(raw)
        tampered["grant_id"] = SHA_D
        with self.assertRaises(OrchestrationContractError):
            WorkGrant.from_dict(tampered)

        tampered = copy.deepcopy(raw)
        tampered["authority"]["outward_move"] = True
        with self.assertRaises(OrchestrationContractError):
            WorkGrant.from_dict(tampered)

        missing = copy.deepcopy(raw)
        missing.pop("grant_id")
        with self.assertRaises(OrchestrationContractError):
            WorkGrant.from_dict(missing)

        hidden = copy.deepcopy(raw)
        hidden["authorized_move"] = "e2e4"
        with self.assertRaises(OrchestrationContractError):
            WorkGrant.from_dict(hidden)

    def test_generation_must_be_positive_integer_not_bool(self):
        item = grant()
        for bad in (0, -1, 1.5, True):
            with self.subTest(bad=bad):
                with self.assertRaises(OrchestrationContractError):
                    WorkGrant(
                        generation=bad,
                        position_id=item.position_id,
                        owner=item.owner,
                        instance=item.instance,
                        profile_id=item.profile_id,
                        profile_digest=item.profile_digest,
                        phase=item.phase,
                        purpose=item.purpose,
                        work_chunk_id=item.work_chunk_id,
                        work_chunk_digest=item.work_chunk_digest,
                        native_limit=item.native_limit,
                        reserved_cpu_ms=item.reserved_cpu_ms,
                        reserved_gpu_ms=item.reserved_gpu_ms,
                        wall_deadline_ms=item.wall_deadline_ms,
                        effective_options_digest=item.effective_options_digest,
                        allocator_decision_digest=item.allocator_decision_digest,
                    )

    def test_profile_family_or_chunk_membership_mismatch_is_rejected(self):
        sf_profile = profile(
            family="stockfish",
            profile_id="stockfish/cpu-small",
            chunk_ids=("stockfish/explore-small",),
        )
        with self.assertRaises(OrchestrationContractError):
            WorkGrant.from_chunk(
                profile=sf_profile,
                chunk=chunk(),
                generation=1,
                position_id="p1",
                owner="stockfish",
                instance="stockfish-shadow",
                wall_deadline_ms=500,
                effective_options_digest=SHA_A,
                allocator_decision_digest=SHA_B,
            )

        lc0_profile = profile(chunk_ids=("lc0/other",))
        with self.assertRaises(OrchestrationContractError):
            WorkGrant.from_chunk(
                profile=lc0_profile,
                chunk=chunk(),
                generation=1,
                position_id="p1",
                owner="lc0",
                instance="lc0-shadow",
                wall_deadline_ms=500,
                effective_options_digest=SHA_A,
                allocator_decision_digest=SHA_B,
            )


if __name__ == "__main__":
    unittest.main()
