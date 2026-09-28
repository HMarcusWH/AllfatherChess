#!/usr/bin/env python3
"""Contract tests for immutable M14-J engine/composition profiles."""

from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.resource_profiles import (
    AcceleratorKind,
    ArtifactIdentity,
    CompositionBinding,
    CompositionProfile,
    CompositionRole,
    EnforcementMode,
    EngineResourceProfile,
    MutationBoundary,
    OrchestrationContractError,
    ProcessIdentity,
    ProfileOption,
    QualificationIdentity,
)


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
COMMIT = "1" * 40


def qualification() -> QualificationIdentity:
    return QualificationIdentity(
        source_commit=COMMIT,
        evidence_sha256=SHA_C,
        evidence_id="engine-opt-v2",
        host_domain="cpu-x86_64",
    )


def process_identity(*, reverse: bool = False) -> ProcessIdentity:
    artifacts = (
        ArtifactIdentity("network", SHA_B),
        ArtifactIdentity("binary-meta", SHA_C),
    )
    env = (("OMP_NUM_THREADS", "1"), ("OPENBLAS_NUM_THREADS", "1"))
    if reverse:
        artifacts = tuple(reversed(artifacts))
        env = tuple(reversed(env))
    return ProcessIdentity(
        binary_sha256=SHA_A,
        artifacts=artifacts,
        backend="blas",
        args=("--show-hidden",),
        environment=env,
    )


def profile(
    profile_id: str = "lc0/cpu-small-v1",
    *,
    family: str = "lc0",
    cpu_slots: int = 1,
    options: tuple[ProfileOption, ...] | None = None,
    work_chunk_ids: tuple[str, ...] = ("lc0/explore-small",),
) -> EngineResourceProfile:
    if options is None:
        options = (
            ProfileOption(
                "Backend",
                "blas",
                MutationBoundary.PROCESS,
            ),
            ProfileOption(
                "NNCacheSize",
                262144,
                MutationBoundary.GAME,
            ),
            ProfileOption(
                "MultiPV",
                1,
                MutationBoundary.SEARCH,
                "EXPLORE",
            ),
            ProfileOption(
                "MultiPV",
                3,
                MutationBoundary.SEARCH,
                "VERIFY",
            ),
        )
    return EngineResourceProfile(
        profile_id=profile_id,
        family=family,
        process_identity=process_identity(),
        options=options,
        cpu_slots=cpu_slots,
        expected_memory_mib=256,
        accelerator=AcceleratorKind.CPU,
        accelerator_memory_mib=0,
        work_chunk_ids=work_chunk_ids,
        qualification=qualification(),
    )


class ResourceProfileContractTests(unittest.TestCase):
    def test_round_trip_and_digest_are_canonical(self):
        first = profile()
        restored = EngineResourceProfile.from_dict(first.as_dict())
        self.assertEqual(restored, first)
        self.assertEqual(restored.digest, first.digest)

        reordered = EngineResourceProfile(
            profile_id=first.profile_id,
            family=first.family,
            process_identity=process_identity(reverse=True),
            options=tuple(reversed(first.options)),
            cpu_slots=first.cpu_slots,
            expected_memory_mib=first.expected_memory_mib,
            accelerator=first.accelerator,
            accelerator_memory_mib=0,
            work_chunk_ids=tuple(reversed(first.work_chunk_ids)),
            qualification=first.qualification,
        )
        self.assertEqual(first.as_dict(), reordered.as_dict())
        self.assertEqual(first.digest, reordered.digest)
        self.assertEqual(
            json.dumps(first.as_dict(), sort_keys=True),
            json.dumps(reordered.as_dict(), sort_keys=True),
        )

    def test_one_claim_bearing_change_changes_digest(self):
        first = profile()
        changed = profile(
            options=first.options
            + (
                ProfileOption(
                    "MultiPV",
                    3,
                    MutationBoundary.SEARCH,
                    "STAGED_VERIFY",
                ),
            )
        )
        self.assertNotEqual(first.digest, changed.digest)

    def test_bool_and_nonfinite_numeric_claims_are_rejected(self):
        with self.assertRaises(OrchestrationContractError):
            EngineResourceProfile(
                profile_id="lc0/bad",
                family="lc0",
                process_identity=process_identity(),
                options=(),
                cpu_slots=True,
                expected_memory_mib=256,
                accelerator="cpu",
                accelerator_memory_mib=0,
                work_chunk_ids=(),
                qualification=qualification(),
            )
        with self.assertRaises(OrchestrationContractError):
            ProfileOption(
                "SomeFloat",
                math.inf,
                MutationBoundary.GAME,
            )

    def test_uci_option_names_with_spaces_are_supported(self):
        item = ProfileOption(
            "Move Overhead",
            10,
            MutationBoundary.GAME,
        )
        self.assertEqual(item.name, "Move Overhead")
        with self.assertRaises(OrchestrationContractError):
            ProfileOption(
                " Move Overhead",
                10,
                MutationBoundary.GAME,
            )

    def test_search_option_requires_phase_and_heavy_option_may_not_claim_one(self):
        with self.assertRaises(OrchestrationContractError):
            ProfileOption("MultiPV", 1, MutationBoundary.SEARCH)
        with self.assertRaises(OrchestrationContractError):
            ProfileOption(
                "Hash",
                16,
                MutationBoundary.GAME,
                "VERIFY",
            )

    def test_same_option_cannot_cross_mutation_boundaries(self):
        with self.assertRaises(OrchestrationContractError):
            profile(
                options=(
                    ProfileOption("Hash", 16, MutationBoundary.GAME),
                    ProfileOption(
                        "Hash",
                        32,
                        MutationBoundary.SEARCH,
                        "VERIFY",
                    ),
                )
            )

    def test_duplicate_phase_option_is_rejected(self):
        option = ProfileOption(
            "MultiPV",
            1,
            MutationBoundary.SEARCH,
            "EXPLORE",
        )
        with self.assertRaises(OrchestrationContractError):
            profile(options=(option, option))

    def test_malformed_sha_and_artifact_identity_are_rejected(self):
        with self.assertRaises(OrchestrationContractError):
            ProcessIdentity(binary_sha256="ABC", backend="blas")
        with self.assertRaises(OrchestrationContractError):
            ProcessIdentity(
                binary_sha256=SHA_A,
                artifacts=(
                    ArtifactIdentity("network", SHA_B),
                    ArtifactIdentity("network", SHA_C),
                ),
            )

    def test_cpu_profile_cannot_claim_gpu_memory(self):
        with self.assertRaises(OrchestrationContractError):
            EngineResourceProfile(
                profile_id="lc0/bad-gpu-memory",
                family="lc0",
                process_identity=process_identity(),
                options=(),
                cpu_slots=1,
                expected_memory_mib=256,
                accelerator="cpu",
                accelerator_memory_mib=1,
                work_chunk_ids=(),
                qualification=qualification(),
            )

    def test_unknown_serialized_profile_fields_and_missing_authority_are_rejected(self):
        raw = profile().as_dict()

        hidden = copy.deepcopy(raw)
        hidden["authorized_move"] = "e2e4"
        with self.assertRaises(OrchestrationContractError):
            EngineResourceProfile.from_dict(hidden)

        missing = copy.deepcopy(raw)
        missing.pop("authority")
        with self.assertRaises(OrchestrationContractError):
            EngineResourceProfile.from_dict(missing)

    def test_profile_has_no_move_authority(self):
        item = profile()
        self.assertFalse(item.as_dict()["authority"]["outward_move"])
        self.assertFalse(item.as_dict()["authority"]["resource_authorization"])
        self.assertNotIn("move", item.as_dict())
        self.assertFalse(hasattr(item, "authorized_move"))

    def test_composition_checks_concurrency_groups_not_naive_total(self):
        sf = profile("stockfish/cpu2", family="stockfish", cpu_slots=2)
        rr = profile("reckless/cpu2", family="reckless", cpu_slots=2)
        comp = CompositionProfile(
            composition_id="composition/cpu2-scheduled",
            declared_cpu_slots=2,
            expected_memory_mib=1024,
            enforcement_required=EnforcementMode.AFFINITY,
            bindings=(
                CompositionBinding(
                    "stockfish-anchor",
                    CompositionRole.ANCHOR,
                    "stockfish",
                    sf.profile_id,
                    2,
                    "anchor-window",
                ),
                CompositionBinding(
                    "reckless-shadow",
                    CompositionRole.SPECIALIST,
                    "reckless",
                    rr.profile_id,
                    2,
                    "specialist-window",
                ),
            ),
            qualification=qualification(),
        )
        comp.validate_against({sf.profile_id: sf, rr.profile_id: rr})
        self.assertEqual(
            CompositionProfile.from_dict(comp.as_dict()).digest,
            comp.digest,
        )

    def test_composition_rejects_group_oversubscription(self):
        with self.assertRaises(OrchestrationContractError):
            CompositionProfile(
                composition_id="composition/oversubscribed",
                declared_cpu_slots=2,
                expected_memory_mib=1024,
                enforcement_required="affinity",
                bindings=(
                    CompositionBinding(
                        "anchor",
                        "anchor",
                        "stockfish",
                        "stockfish/cpu2",
                        2,
                        "same",
                    ),
                    CompositionBinding(
                        "shadow",
                        "specialist",
                        "reckless",
                        "reckless/cpu1",
                        1,
                        "same",
                    ),
                ),
                qualification=qualification(),
            )

    def test_composition_cross_check_rejects_family_slot_and_memory_mismatch(self):
        sf = profile("stockfish/cpu1", family="stockfish", cpu_slots=1)
        comp = CompositionProfile(
            composition_id="composition/check",
            declared_cpu_slots=2,
            expected_memory_mib=256,
            enforcement_required="observed",
            bindings=(
                CompositionBinding(
                    "anchor",
                    "anchor",
                    "reckless",
                    sf.profile_id,
                    1,
                    "move",
                ),
            ),
            qualification=qualification(),
        )
        with self.assertRaises(OrchestrationContractError):
            comp.validate_against({sf.profile_id: sf})

        memory = CompositionProfile(
            composition_id="composition/memory",
            declared_cpu_slots=2,
            expected_memory_mib=128,
            enforcement_required="observed",
            bindings=(
                CompositionBinding(
                    "anchor",
                    "anchor",
                    "stockfish",
                    sf.profile_id,
                    1,
                    "move",
                ),
            ),
            qualification=qualification(),
        )
        with self.assertRaises(OrchestrationContractError):
            memory.validate_against({sf.profile_id: sf})

    def test_composition_requires_exactly_one_anchor(self):
        with self.assertRaises(OrchestrationContractError):
            CompositionProfile(
                composition_id="composition/no-anchor",
                declared_cpu_slots=1,
                expected_memory_mib=256,
                enforcement_required="observed",
                bindings=(
                    CompositionBinding(
                        "lc0-shadow",
                        "specialist",
                        "lc0",
                        "lc0/cpu1",
                        1,
                        "move",
                    ),
                ),
                qualification=qualification(),
            )


if __name__ == "__main__":
    unittest.main()
