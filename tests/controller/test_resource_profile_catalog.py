#!/usr/bin/env python3
"""Contracts for the frozen J3 resource-profile catalog."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.resource_profile_catalog import (
    ResourceProfileCatalog,
    ResourceProfileCatalogError,
    load_resource_profile_catalog,
)
from controller.resource_profiles import OrchestrationContractError


CATALOG = ROOT / "qualification/resource-profile-catalog-v1.json"
RUNTIME = ROOT / "config/allfather.online-hybrid-v2.validation.json"
SELECTION = ROOT / "qualification/engine-opt-v2-selection.json"
HOST_BINDING = ROOT / "qualification/engine-opt-v2-host-binding.json"
CANDIDATE_RUNTIME = ROOT / "config/allfather.online-hybrid-v2.candidate.validation.json"
CANDIDATE_SELECTION = ROOT / "qualification/engine-opt-v2-candidate-selection.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ResourceProfileCatalogTests(unittest.TestCase):
    def test_seed_catalog_is_canonical_and_exact_v2_equivalent(self):
        catalog = load_resource_profile_catalog(CATALOG)
        result = catalog.validate_v2_equivalence(load(RUNTIME), load(SELECTION))
        self.assertEqual(result["catalog_digest"], catalog.digest)
        self.assertEqual(len(result["profiles"]), 4)
        self.assertEqual(
            result["composition_id"],
            "composition/engine-opt-v2-exact-host",
        )
        self.assertFalse(catalog.selection_enabled)
        self.assertEqual(catalog.fallback_profile, "engine-opt-v2")
        restored = ResourceProfileCatalog.from_dict(catalog.as_dict())
        self.assertEqual(restored.as_dict(), catalog.as_dict())
        self.assertEqual(restored.digest, catalog.digest)

    def test_equivalence_contract_accepts_explicit_no_warmup_selection(self):
        raw = load(CATALOG)
        runtime = load(CANDIDATE_RUNTIME)
        selection = load(CANDIDATE_SELECTION)
        lc0 = next(
            item
            for item in raw["profiles"]
            if item["profile_id"] == "lc0/specialist-engine-opt-v2"
        )
        for option in lc0["options"]:
            if option["name"] == "MinibatchSize":
                option["value"] = 4
            elif option["name"] == "MaxPrefetch":
                option["value"] = 0
        raw["profile_runtime"]["lc0/specialist-engine-opt-v2"]["warmup"] = None
        catalog = ResourceProfileCatalog.from_dict(raw)
        result = catalog.validate_v2_equivalence(runtime, selection)
        self.assertEqual(
            result["composition_id"],
            "composition/engine-opt-v2-exact-host",
        )
        self.assertIsNone(catalog.warmup("lc0/specialist-engine-opt-v2"))

    def test_seed_is_bound_to_pr49_exact_host_qualification(self):
        catalog = load_resource_profile_catalog(CATALOG)
        snapshot = catalog.qualification_snapshot
        self.assertEqual(
            snapshot["qualified_head"],
            "3de528e5a1a5a3609e0c293a80296ddc4a006875",
        )
        self.assertEqual(
            snapshot["qualification_disposition"],
            "QUALIFIED_EXACT_HOST_ONLY",
        )
        self.assertEqual(snapshot["binding_scope"], "exact_host_observation")
        self.assertFalse(catalog.selection_enabled)
        for binding in catalog.default_composition.bindings:
            profile = catalog.profile(binding.profile_id)
            self.assertEqual(
                profile.qualification.execution_domain_id,
                snapshot["execution_domain_id"],
            )
            self.assertEqual(
                profile.qualification.execution_domain_digest,
                snapshot["execution_domain_digest"],
            )
            self.assertEqual(profile.qualification.binding_scope, snapshot["binding_scope"])
            self.assertEqual(profile.work_chunk_ids, ())

        history = load(HOST_BINDING)
        current = history["current_domain_bound_qualification"]
        self.assertEqual(current["qualified_head"], snapshot["qualified_head"])
        self.assertEqual(
            current["aggregate_report_sha256"],
            snapshot["aggregate_report_sha256"],
        )
        self.assertEqual(
            current["execution_domain_digest"],
            snapshot["execution_domain_digest"],
        )

    def test_catalog_records_measured_seed_memory_without_enforcement_claim(self):
        catalog = load_resource_profile_catalog(CATALOG)
        observed = {
            profile.profile_id: profile.expected_memory_mib
            for profile in (
                catalog.profile("stockfish/anchor-engine-opt-v2"),
                catalog.profile("stockfish/specialist-engine-opt-v2"),
                catalog.profile("reckless/specialist-engine-opt-v2"),
                catalog.profile("lc0/specialist-engine-opt-v2"),
            )
        }
        self.assertEqual(
            observed,
            {
                "stockfish/anchor-engine-opt-v2": 261,
                "stockfish/specialist-engine-opt-v2": 261,
                "reckless/specialist-engine-opt-v2": 97,
                "lc0/specialist-engine-opt-v2": 154,
            },
        )
        self.assertEqual(catalog.default_composition.expected_memory_mib, 773)
        self.assertEqual(
            catalog.default_composition.enforcement_required.value,
            "observed",
        )

    def test_wrong_execution_domain_is_rejected(self):
        catalog = load_resource_profile_catalog(CATALOG)
        profile = catalog.profile("lc0/specialist-engine-opt-v2")
        with self.assertRaises(ResourceProfileCatalogError):
            catalog.require_execution_domain(
                profile.profile_id,
                execution_domain_id="exec-domain/" + "f" * 20,
                execution_domain_digest="f" * 64,
                binding_scope="exact_host_observation",
            )

    def test_unknown_hidden_field_is_rejected(self):
        raw = load(CATALOG)
        raw["authorized_move"] = "e2e4"
        with self.assertRaises(OrchestrationContractError):
            ResourceProfileCatalog.from_dict(raw)

    def test_one_unit_profile_or_policy_drift_breaks_v2_equivalence(self):
        runtime = load(RUNTIME)
        selection = load(SELECTION)
        raw = load(CATALOG)

        mutations = []

        profile = copy.deepcopy(raw)
        lc0 = next(
            item
            for item in profile["profiles"]
            if item["profile_id"] == "lc0/specialist-engine-opt-v2"
        )
        max_prefetch = next(
            item for item in lc0["options"] if item["name"] == "MaxPrefetch"
        )
        max_prefetch["value"] = 9
        mutations.append(profile)

        warmup = copy.deepcopy(raw)
        warmup["profile_runtime"]["lc0/specialist-engine-opt-v2"]["warmup"]["nodes"] = 63
        mutations.append(warmup)

        reservation = copy.deepcopy(raw)
        reservation["legacy_policy"]["resource_estimates_ms"]["explore"]["lc0"] = 599
        mutations.append(reservation)

        multipv = copy.deepcopy(raw)
        lc0 = next(
            item
            for item in multipv["profiles"]
            if item["profile_id"] == "lc0/specialist-engine-opt-v2"
        )
        verify = next(
            item
            for item in lc0["options"]
            if item["name"] == "MultiPV" and item["phase"] == "VERIFY"
        )
        verify["value"] = 2
        mutations.append(multipv)

        for index, candidate in enumerate(mutations):
            with self.subTest(index=index):
                catalog = ResourceProfileCatalog.from_dict(candidate)
                with self.assertRaises(ResourceProfileCatalogError):
                    catalog.validate_v2_equivalence(runtime, selection)

    def test_frozen_invariants_are_not_managed_options(self):
        catalog = load_resource_profile_catalog(CATALOG)
        lc0 = catalog.profile("lc0/specialist-engine-opt-v2")
        managed = {item.name for item in lc0.options}
        frozen = dict(catalog.runtime_metadata(lc0.profile_id).frozen_options)
        self.assertNotIn("ScoreType", managed)
        self.assertNotIn("DefectTelemetry", managed)
        self.assertNotIn("UCI_Chess960", managed)
        self.assertEqual(frozen["ScoreType"], "WDL_mu")
        self.assertFalse(frozen["DefectTelemetry"])
        self.assertFalse(frozen["UCI_Chess960"])

    def test_search_dynamic_option_is_not_duplicated_as_game_static(self):
        catalog = load_resource_profile_catalog(CATALOG)
        for profile_id in (
            "stockfish/specialist-engine-opt-v2",
            "reckless/specialist-engine-opt-v2",
            "lc0/specialist-engine-opt-v2",
        ):
            profile = catalog.profile(profile_id)
            multipv = [item for item in profile.options if item.name == "MultiPV"]
            self.assertEqual({item.boundary.value for item in multipv}, {"search"})
            self.assertEqual(
                {item.phase: item.value for item in multipv},
                {"EXPLORE": 1, "VERIFY": 3, "STAGED_VERIFY": 3},
            )


if __name__ == "__main__":
    unittest.main()
