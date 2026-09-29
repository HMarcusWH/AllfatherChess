#!/usr/bin/env python3
"""Runtime application tests for the J3/J4 resource-profile catalog."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.resource.linux_affinity import LinuxAffinityError, LinuxAffinityProvider
from controller.decision import canonical_digest
from controller.host_capabilities import HOST_CAPABILITIES_VERSION, HostCapabilities, NumaNodeObservation
from controller.resource_control import ResourceControlError, ResourceController
from controller.resource_profile_catalog import ResourceProfileCatalog
from controller.runtime import BackendManager, RuntimeError
from tests.controller.test_shadow_runtime import write_shadow_config


DOMAIN_DIGEST = "d" * 64
DOMAIN_ID = f"exec-domain/{DOMAIN_DIGEST[:20]}"
SOURCE = "1" * 40
EVIDENCE = "e" * 64


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def qualified() -> dict:
    return {
        "source_commit": SOURCE,
        "evidence_sha256": EVIDENCE,
        "evidence_id": "synthetic-j4",
        "execution_domain_id": DOMAIN_ID,
        "execution_domain_digest": DOMAIN_DIGEST,
        "binding_scope": "exact_host_observation",
    }


def profile(
    profile_id: str,
    family: str,
    binary_sha: str,
    options: list[dict],
    *,
    backend: str | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "profile_id": profile_id,
        "family": family,
        "process_identity": {
            "binary_sha256": binary_sha,
            "artifacts": [],
            "backend": backend,
            "args": [],
            "environment": {},
        },
        "options": options,
        "cpu_slots": 1,
        "expected_memory_mib": 64,
        "accelerator": "cpu",
        "accelerator_memory_mib": 0,
        "work_chunk_ids": [],
        "qualification": qualified(),
        "authority": {
            "resource_profile": True,
            "resource_authorization": False,
            "outward_move": False,
        },
    }


def option(name: str, value, boundary: str, phase: str | None = None) -> dict:
    return {"name": name, "value": value, "boundary": boundary, "phase": phase}


def prepare_config(directory: Path) -> Path:
    path = write_shadow_config(directory)
    raw = json.loads(path.read_text(encoding="utf-8"))
    for name, spec in raw["instances"].items():
        spec["options"]["Threads"] = 1
        spec["options"]["Hash"] = 16
        spec["options"]["MultiPV"] = 1
        if spec["role"] == "shadow":
            spec["phase_options"] = {
                "EXPLORE": {"MultiPV": 1},
                "VERIFY": {"MultiPV": 3},
                "STAGED_VERIFY": {"MultiPV": 3},
            }
    raw["instances"]["lc0-shadow"]["options"]["Backend"] = "blas"
    raw["instances"]["lc0-shadow"]["warmup"] = {
        "enabled": True,
        "nodes": 2,
        "position": "startpos",
        "reset_after": True,
    }
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def make_catalog(config_path: Path, *, binary_override: str | None = None) -> ResourceProfileCatalog:
    binary = file_sha(Path(sys.executable))
    if binary_override is not None:
        binary = binary_override
    common_game = [
        option("Threads", 1, "game"),
        option("Hash", 16, "game"),
    ]
    search = [
        option("MultiPV", 1, "search", "EXPLORE"),
        option("MultiPV", 3, "search", "VERIFY"),
        option("MultiPV", 3, "search", "STAGED_VERIFY"),
    ]
    profiles = [
        profile(
            "stockfish/anchor-test",
            "stockfish",
            binary,
            common_game + [option("MultiPV", 1, "game")],
        ),
        profile(
            "stockfish/specialist-test",
            "stockfish",
            binary,
            common_game + search,
        ),
        profile(
            "reckless/specialist-test",
            "reckless",
            binary,
            common_game + search,
        ),
        profile(
            "lc0/specialist-test",
            "lc0",
            binary,
            [option("Backend", "blas", "process")] + common_game + search,
            backend="blas",
        ),
    ]
    bindings = [
        ("stockfish-anchor", "anchor", "stockfish", "stockfish/anchor-test"),
        ("stockfish-shadow", "specialist", "stockfish", "stockfish/specialist-test"),
        ("reckless-shadow", "specialist", "reckless", "reckless/specialist-test"),
        ("lc0-shadow", "specialist", "lc0", "lc0/specialist-test"),
    ]
    raw = {
        "schema_version": 1,
        "catalog_version": "resource-profile-catalog-v1",
        "catalog_id": "resource-profile-catalog-test",
        "source_profile": "engine-opt-v2",
        "selection_enabled": False,
        "fallback_profile": "engine-opt-v2",
        "profiles": profiles,
        "compositions": [
            {
                "schema_version": 1,
                "composition_id": "composition/test",
                "declared_cpu_slots": 4,
                "expected_memory_mib": 256,
                "enforcement_required": "observed",
                "bindings": [
                    {
                        "instance": instance,
                        "role": role,
                        "family": family,
                        "profile_id": profile_id,
                        "cpu_slots": 1,
                        "concurrency_group": "move",
                    }
                    for instance, role, family, profile_id in bindings
                ],
                "qualification": qualified(),
                "authority": {
                    "resource_profile": True,
                    "resource_authorization": False,
                    "outward_move": False,
                },
            }
        ],
        "default_composition_id": "composition/test",
        "profile_runtime": {
            "stockfish/anchor-test": {
                "frozen_options": {"UCI_Chess960": False},
                "warmup": None,
            },
            "stockfish/specialist-test": {
                "frozen_options": {"UCI_Chess960": False},
                "warmup": None,
            },
            "reckless/specialist-test": {
                "frozen_options": {"UCI_Chess960": False},
                "warmup": None,
            },
            "lc0/specialist-test": {
                "frozen_options": {
                    "UCI_Chess960": False,
                    "ScoreType": "centipawn",
                },
                "warmup": {
                    "enabled": True,
                    "nodes": 2,
                    "position": "startpos",
                    "reset_after": True,
                },
            },
        },
        "qualification_snapshot": {
            "qualified_head": SOURCE,
            "merge_commit": "2" * 40,
            "workflow_run": 1,
            "aggregate_artifact_id": 1,
            "aggregate_artifact_sha256": "a" * 64,
            "aggregate_report_sha256": "b" * 64,
            "qualification_disposition": "QUALIFIED_EXACT_HOST_ONLY",
            "execution_domain_id": DOMAIN_ID,
            "execution_domain_digest": DOMAIN_DIGEST,
            "binding_scope": "exact_host_observation",
        },
        "legacy_policy": {
            "dispatch_limits": {
                "EXPLORE": {"nodes": 16},
                "VERIFY": {"nodes": 16},
                "STAGED_VERIFY": {"nodes": 32},
            },
            "resource_estimates_ms": {
                "explore": {"stockfish": 100, "reckless": 100, "lc0": 600},
                "verify": {"stockfish": 100, "reckless": 100, "lc0": 800},
            },
        },
        "authority": {
            "resource_profile_catalog": True,
            "resource_authorization": False,
            "outward_move": False,
        },
    }

    # Process argv is part of identity. Derive the exact args from RuntimeConfig.
    manager = BackendManager.from_path(config_path)
    for item in raw["profiles"]:
        instance = next(
            instance
            for instance, _, _, profile_id in bindings
            if profile_id == item["profile_id"]
        )
        spec = manager.spec(instance)
        item["process_identity"]["args"] = list(spec.args)
        item["process_identity"]["environment"] = dict(spec.environment)
    return ResourceProfileCatalog.from_dict(raw)


def observed_host() -> HostCapabilities:
    flags = ("avx", "avx2", "fpu", "sse", "sse2")
    cpus = (0, 1, 2, 3)
    return HostCapabilities(
        version=HOST_CAPABILITIES_VERSION,
        provider_id="linux-host-v2",
        platform="linux",
        architecture="x86_64",
        os_visible_logical_cpus=4,
        affinity_cpus=cpus,
        cgroup_cpuset_effective=cpus,
        allowed_cpus=cpus,
        cpu_vendor_id="AuthenticAMD",
        cpu_family=25,
        cpu_model=1,
        cpu_stepping=1,
        cpu_model_name="AMD test",
        cpu_microcode="0x1",
        cpu_flags_intersection=flags,
        cpu_feature_digest=canonical_digest(list(flags)),
        cpu_identity_complete=True,
        cpu_quota_status="unknown",
        cpu_quota_equivalents=None,
        cpu_quota_observations=(),
        physical_core_count=4,
        smt_width=1,
        topology_complete=True,
        numa_nodes=(NumaNodeObservation(0, cpus),),
        numa_complete=True,
        physical_memory_bytes=8 * 1024**3,
        cgroup_memory_status="unknown",
        cgroup_memory_limit_bytes=None,
        effective_memory_limit_bytes=8 * 1024**3,
        memory_limit_observations=(),
        accelerator_detection_complete=False,
        accelerators=(),
        capacity_complete=False,
        qualification_domain_complete=False,
        faults=("cpu.max:root:OSError:2",),
    )


class FailingObservedProvider:
    provider_id = "failing-observed-v1"

    def inspect_tree_affinity(self, pid: int):
        raise LinuxAffinityError("synthetic placement observation failure")


class RuntimeProfileTests(unittest.TestCase):
    def bind(self, manager: BackendManager, catalog: ResourceProfileCatalog) -> None:
        manager.bind_resource_catalog(
            catalog,
            execution_domain_id=DOMAIN_ID,
            execution_domain_digest=DOMAIN_DIGEST,
            binding_scope="exact_host_observation",
        )

    def test_catalog_is_adopted_after_existing_startup_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = prepare_config(Path(tmp))
            manager = BackendManager.from_path(config)
            catalog = make_catalog(config)
            self.bind(manager, catalog)
            manager.start()
            try:
                for instance in manager.config.backends:
                    self.assertEqual(
                        manager.effective_profile_id(instance),
                        catalog.profile_for_instance(instance).profile_id,
                    )
                    assertion = manager.assert_effective_profile(instance)
                    self.assertEqual(
                        assertion["profile_catalog_digest"],
                        catalog.digest,
                    )
                    self.assertFalse(assertion["authority"]["outward_move"])
                    self.assertFalse(
                        assertion["authority"]["resource_authorization"]
                    )
            finally:
                manager.close()

    def test_missing_or_wrong_domain_is_rejected_before_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = prepare_config(Path(tmp))
            catalog = make_catalog(config)
            manager = BackendManager.from_path(config)
            with self.assertRaises(RuntimeError):
                manager.bind_resource_catalog(
                    catalog,
                    execution_domain_id="exec-domain/" + "f" * 20,
                    execution_domain_digest="f" * 64,
                    binding_scope="exact_host_observation",
                )
            self.assertIsNone(manager.effective_profile_id("lc0-shadow"))

    def test_binary_identity_mismatch_is_rejected_before_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = prepare_config(Path(tmp))
            manager = BackendManager.from_path(config)
            bad = make_catalog(config, binary_override="f" * 64)
            with self.assertRaises(RuntimeError):
                self.bind(manager, bad)

    def test_catalog_must_be_bound_before_process_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = prepare_config(Path(tmp))
            manager = BackendManager.from_path(config)
            catalog = make_catalog(config)
            manager.start()
            try:
                with self.assertRaises(RuntimeError):
                    self.bind(manager, catalog)
            finally:
                manager.close()

    def test_phase_profiles_generalize_shadow_phase_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = prepare_config(Path(tmp))
            manager = BackendManager.from_path(config)
            catalog = make_catalog(config)
            self.bind(manager, catalog)
            manager.start()
            try:
                result = manager.configure_phase_profile("lc0-shadow", "VERIFY")
                self.assertEqual(result["phase"], "VERIFY")
                self.assertEqual(manager.effective_options("lc0-shadow")["MultiPV"], 3)

                effective = manager.configure_shadow_phase("lc0-shadow", "EXPLORE")
                self.assertEqual(effective["MultiPV"], 1)
                self.assertEqual(
                    manager.assert_effective_profile("lc0-shadow")["phase"],
                    "EXPLORE",
                )
            finally:
                manager.close()

    def test_game_profile_stays_pending_until_global_game_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = prepare_config(Path(tmp))
            manager = BackendManager.from_path(config)
            catalog = make_catalog(config)
            self.bind(manager, catalog)
            manager.start()
            try:
                profile_id = manager.effective_profile_id("lc0-shadow")
                assert profile_id is not None
                manager.configure_phase_profile("lc0-shadow", "VERIFY")
                pending = manager.configure_game_profile("lc0-shadow", profile_id)
                self.assertTrue(pending["pending_game_reset"])
                self.assertEqual(manager.effective_options("lc0-shadow")["MultiPV"], 1)
                with self.assertRaises(RuntimeError):
                    manager.assert_effective_profile("lc0-shadow")
                with self.assertRaises(RuntimeError):
                    manager.set_position("position startpos")
                with self.assertRaises(RuntimeError):
                    manager.start_shadow_search(
                        "lc0-shadow",
                        "go nodes 16 searchmoves e2e4",
                        token=1,
                        on_info=lambda *_: None,
                        on_complete=lambda *_: None,
                    )

                manager.new_game()
                assertion = manager.assert_effective_profile("lc0-shadow")
                self.assertEqual(assertion["profile_id"], profile_id)
                self.assertEqual(assertion["phase"], "EXPLORE")
            finally:
                manager.close()

    def test_game_profile_change_is_rejected_while_any_engine_searches(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = prepare_config(Path(tmp))
            manager = BackendManager.from_path(config)
            catalog = make_catalog(config)
            self.bind(manager, catalog)
            manager.start()
            try:
                started = manager.start_shadow_search(
                    "lc0-shadow",
                    "go infinite searchmoves e2e4",
                    token=7,
                    on_info=lambda *_: None,
                    on_complete=lambda *_: None,
                )
                self.assertTrue(started)
                deadline = time.monotonic() + 1
                while (
                    not manager.process("lc0-shadow").active_search
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.005)
                with self.assertRaises(RuntimeError):
                    manager.configure_game_profile(
                        "stockfish-shadow",
                        manager.effective_profile_id("stockfish-shadow") or "",
                    )
                manager.stop_instance_for("lc0-shadow", 7, timeout=1)
            finally:
                manager.close()

    def test_effective_profile_assertion_detects_tracked_state_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = prepare_config(Path(tmp))
            manager = BackendManager.from_path(config)
            catalog = make_catalog(config)
            self.bind(manager, catalog)
            manager.start()
            try:
                manager._effective_options["lc0-shadow"]["MultiPV"] = 7
                with self.assertRaises(RuntimeError):
                    manager.assert_effective_profile("lc0-shadow")
            finally:
                manager.close()

    def test_observed_resource_control_binds_verified_state_to_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = prepare_config(Path(tmp))
            manager = BackendManager.from_path(config)
            catalog = make_catalog(config)
            self.bind(manager, catalog)
            control = ResourceController.from_catalog(
                host=observed_host(),
                catalog=catalog,
                provider=LinuxAffinityProvider(),
            )
            manager.bind_resource_control(control)
            manager.start()
            try:
                for instance in manager.config.backends:
                    state = manager.resource_state(instance)
                    self.assertIsNotNone(state)
                    assert state is not None
                    self.assertFalse(state.enforced)
                    assertion = manager.assert_effective_profile(instance)
                    self.assertEqual(
                        assertion["resource_state_digest"],
                        state.digest,
                    )
                verified = manager.verify_resource_layout()
                self.assertEqual(set(verified), set(manager.config.backends))
            finally:
                manager.close()

    def test_resource_control_failure_aborts_composition_before_profile_seal(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = prepare_config(Path(tmp))
            manager = BackendManager.from_path(config)
            catalog = make_catalog(config)
            self.bind(manager, catalog)
            control = ResourceController.from_catalog(
                host=observed_host(),
                catalog=catalog,
                provider=FailingObservedProvider(),
            )
            manager.bind_resource_control(control)
            with self.assertRaises(ResourceControlError):
                manager.start()
            self.assertIsNone(manager.effective_profile_id("stockfish-anchor"))
            self.assertIsNone(manager.resource_state("stockfish-anchor"))

    def test_close_discards_pid_bound_resource_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = prepare_config(Path(tmp))
            manager = BackendManager.from_path(config)
            catalog = make_catalog(config)
            self.bind(manager, catalog)
            manager.bind_resource_control(
                ResourceController.from_catalog(
                    host=observed_host(),
                    catalog=catalog,
                    provider=LinuxAffinityProvider(),
                )
            )
            manager.start()
            self.assertIsNotNone(manager.resource_state_digest("stockfish-anchor"))
            manager.close()
            self.assertIsNone(manager.resource_state("stockfish-anchor"))

    def test_legacy_runtime_requires_no_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = prepare_config(Path(tmp))
            manager = BackendManager.from_path(config)
            manager.start()
            try:
                self.assertIsNone(manager.effective_profile_id("lc0-shadow"))
                effective = manager.configure_shadow_phase("lc0-shadow", "VERIFY")
                self.assertEqual(effective["MultiPV"], 3)
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
