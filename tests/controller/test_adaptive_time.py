#!/usr/bin/env python3
"""Pure J8 adaptive-time planner and replay-integrity tests."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common.search_request import parse_position_command
from controller.adaptive_time import (
    AdaptiveTimeSettings,
    build_move_resource_plan,
    game_environment_from_time_plan,
    verify_move_resource_plan_manifest,
)
from controller.budget import ResourceEnvelope
from controller.host_capabilities import HOST_CAPABILITIES_VERSION, HostCapabilities
from controller.online_time import OnlineTimeSettings, make_time_plan
from controller.resource_profile_catalog import load_resource_profile_catalog
from tests.controller.online_helpers import shell_fixture, wait_for


CATALOG = load_resource_profile_catalog(
    ROOT / "qualification/resource-profile-catalog-v1.json"
)
SETTINGS = AdaptiveTimeSettings()


def host(
    cpus: int = 4,
    *,
    quota: float | None = None,
    quota_unknown: bool = False,
    memory_mib: int = 8192,
) -> HostCapabilities:
    allowed = tuple(range(cpus))
    memory = memory_mib * 1024 * 1024
    if quota_unknown:
        quota_status, quota_value = "unknown", None
    elif quota is None:
        quota_status, quota_value = "unlimited", None
    else:
        quota_status, quota_value = "limited", float(quota)
    return HostCapabilities(
        version=HOST_CAPABILITIES_VERSION,
        provider_id="j8-test-host",
        platform="linux",
        architecture="x86_64",
        os_visible_logical_cpus=cpus,
        affinity_cpus=allowed,
        cgroup_cpuset_effective=allowed,
        allowed_cpus=allowed,
        cpu_vendor_id=None,
        cpu_family=None,
        cpu_model=None,
        cpu_stepping=None,
        cpu_model_name=None,
        cpu_microcode=None,
        cpu_flags_intersection=(),
        cpu_feature_digest=None,
        cpu_identity_complete=False,
        cpu_quota_status=quota_status,
        cpu_quota_equivalents=quota_value,
        cpu_quota_observations=(),
        physical_core_count=None,
        smt_width=None,
        topology_complete=False,
        numa_nodes=(),
        numa_complete=False,
        physical_memory_bytes=max(memory, 1024**3),
        cgroup_memory_status="limited",
        cgroup_memory_limit_bytes=memory,
        effective_memory_limit_bytes=memory,
        memory_limit_observations=(),
        accelerator_detection_complete=False,
        accelerators=(),
        capacity_complete=quota_status != "unknown",
        qualification_domain_complete=False,
        faults=(),
    )


def time_plan(
    command: str = "go wtime 600000 btime 600000 winc 5000 binc 5000",
):
    return make_time_plan(
        command=command,
        position=parse_position_command("position startpos"),
        generation=4,
        settings=OnlineTimeSettings(
            max_move_ms=4000,
            network_reserve_ms=100,
            stop_grace_ms=300,
            output_margin_ms=50,
            prepare_budget_ms=100,
            cpu_parallelism=4,
        ),
        envelope=ResourceEnvelope(
            wall_ms=4000,
            cpu_ms=12000,
            verification_reserve_fraction=0.3,
            controller_overhead_reserve_ms=250,
        ),
        received_monotonic=10.0,
        controller_cpu_started_ns=100,
    )


def resource_plan(base=None, observed_host=None):
    base = time_plan() if base is None else base
    observed_host = host() if observed_host is None else observed_host
    return build_move_resource_plan(
        baseline=base,
        settings=SETTINGS,
        host=observed_host,
        composition=CATALOG.default_composition,
        catalog_id=CATALOG.catalog_id,
        catalog_digest=CATALOG.digest,
    )


def manifest(base, plan):
    return {
        "generation": base.generation,
        "time_plan": base.as_dict(),
        "move_resource_plan": plan.as_dict(),
        "position": {"position_id": base.position_id},
    }


class AdaptiveTimeTests(unittest.TestCase):
    def test_adaptive_plan_is_clamp_only(self):
        base = time_plan()
        item = resource_plan(base)
        self.assertEqual(item.disposition, "ADAPTIVE")
        self.assertEqual(item.soft_budget_ms, base.soft_budget_ms)
        self.assertEqual(item.hard_ceiling_ms, base.hard_budget_ms)
        self.assertLessEqual(item.resource_envelope.wall_ms, base.envelope.wall_ms)
        self.assertLessEqual(item.resource_envelope.cpu_ms, base.envelope.cpu_ms)
        self.assertEqual(item.resource_envelope.gpu_ms, 0)
        self.assertEqual(item.effective_parallelism, 4.0)

    def test_more_host_cpus_do_not_escape_composition_or_clock_cap(self):
        base = time_plan("go movetime 1200")
        item = resource_plan(base, host(8))
        self.assertEqual(item.effective_parallelism, 4.0)
        self.assertLessEqual(item.resource_envelope.cpu_ms, base.envelope.cpu_ms)

    def test_smaller_and_fractional_capacity_clamp_cpu_only(self):
        base = time_plan("go movetime 1200")
        two = resource_plan(base, host(2))
        self.assertEqual(two.effective_parallelism, 2.0)
        self.assertEqual(two.resource_envelope.wall_ms, base.envelope.wall_ms)
        self.assertLessEqual(
            two.resource_envelope.cpu_ms,
            base.envelope.wall_ms * 2,
        )
        self.assertEqual(
            two.resource_envelope.controller_overhead_reserve_ms,
            base.envelope.controller_overhead_reserve_ms,
        )

        fractional = resource_plan(base, host(4, quota=1.5))
        self.assertEqual(fractional.effective_parallelism, 1.5)
        self.assertLessEqual(
            fractional.resource_envelope.cpu_ms,
            base.envelope.wall_ms * 1.5,
        )
        self.assertEqual(
            fractional.resource_envelope.controller_overhead_reserve_ms,
            base.envelope.controller_overhead_reserve_ms,
        )

        tiny = resource_plan(base, host(4, quota=0.1))
        self.assertEqual(tiny.effective_parallelism, 0.1)
        self.assertAlmostEqual(tiny.resource_envelope.cpu_ms, 120.0)
        self.assertAlmostEqual(tiny.resource_envelope.verification_reserve_ms, 36.0)
        self.assertAlmostEqual(tiny.resource_envelope.controller_overhead_reserve_ms, 84.0)
        self.assertAlmostEqual(tiny.resource_envelope.solver_cpu_ceiling_ms, 0.0)

    def test_unknown_quota_and_low_memory_fall_back(self):
        base = time_plan("go movetime 1200")
        unknown = resource_plan(base, host(4, quota_unknown=True))
        self.assertEqual(unknown.disposition, "FALLBACK")
        self.assertEqual(unknown.fallback_reason, "HOST_CPU_QUOTA_UNKNOWN")
        self.assertEqual(unknown.resource_envelope, base.envelope)

        low = resource_plan(base, host(4, memory_mib=512))
        self.assertEqual(low.disposition, "FALLBACK")
        self.assertEqual(low.fallback_reason, "HOST_MEMORY_INSUFFICIENT")
        self.assertEqual(low.resource_envelope, base.envelope)

    def test_missing_increments_become_explicit_zero(self):
        base = time_plan("go wtime 30000 btime 25000")
        env = game_environment_from_time_plan(
            base,
            settings=SETTINGS,
            host=host(),
            composition=CATALOG.default_composition,
        )
        self.assertEqual(env.source.value, "uci_observed")
        self.assertEqual(env.white_increment_ms, 0)
        self.assertEqual(env.black_increment_ms, 0)
        self.assertEqual(env.white_time_ms, 30000)
        self.assertEqual(env.black_time_ms, 25000)

    def test_movetime_does_not_fabricate_game_clock(self):
        base = time_plan("go movetime 1200")
        env = game_environment_from_time_plan(
            base,
            settings=SETTINGS,
            host=host(),
            composition=CATALOG.default_composition,
        )
        self.assertEqual(env.source.value, "unknown")
        self.assertIsNone(env.white_time_ms)
        self.assertIsNone(env.black_time_ms)

    def test_huge_increment_cannot_be_borrowed(self):
        base = time_plan(
            "go wtime 500 btime 500 winc 1000000 binc 1000000"
        )
        item = resource_plan(base)
        self.assertLessEqual(base.hard_budget_ms, 400)
        self.assertEqual(item.hard_ceiling_ms, base.hard_budget_ms)

    def test_manifest_reconstructs_plan(self):
        base = time_plan("go movetime 1200")
        item = resource_plan(base)
        doc = manifest(base, item)
        self.assertEqual(verify_move_resource_plan_manifest(doc), [])

        for mutate in (
            lambda d: d["move_resource_plan"]["resource_envelope"].__setitem__(
                "cpu_ms", d["time_plan"]["envelope"]["cpu_ms"] + 1
            ),
            lambda d: d["move_resource_plan"].__setitem__(
                "baseline_time_plan_id", "time-" + "f" * 64
            ),
            lambda d: d["move_resource_plan"]["host_capabilities"].__setitem__(
                "allowed_cpus", [0, 1]
            ),
            lambda d: d["move_resource_plan"]["composition"].__setitem__(
                "declared_cpu_slots", 8
            ),
            lambda d: d["move_resource_plan"]["authority"].__setitem__(
                "resource_authorization", True
            ),
        ):
            bad = copy.deepcopy(doc)
            mutate(bad)
            self.assertTrue(verify_move_resource_plan_manifest(bad))

    def test_frontend_replay_and_router_carry_additive_plan(self):
        with shell_fixture() as (shell, manager, shadow, output, tmp):
            def planner(base):
                return build_move_resource_plan(
                    baseline=base,
                    settings=SETTINGS,
                    host=host(2),
                    composition=CATALOG.default_composition,
                    catalog_id=CATALOG.catalog_id,
                    catalog_digest=CATALOG.digest,
                )

            with patch.object(
                manager,
                "make_move_resource_plan",
                side_effect=planner,
            ):
                shell.handle_command("go movetime 500")
                wait_for(
                    lambda: any(
                        line.startswith("bestmove ")
                        for line in output.getvalue().splitlines()
                    ),
                    timeout=3,
                )
                wait_for(
                    lambda: bool(list(tmp.glob("replays/*/route.json"))),
                    timeout=5,
                )
                wait_for(
                    lambda: bool(list(tmp.glob("replays/*/manifest.json"))),
                    timeout=5,
                )

            run = next(tmp.glob("replays/*"))
            replay = json.loads((run / "manifest.json").read_text())
            route = json.loads((run / "route.json").read_text())
            self.assertIn("time_plan", replay)
            self.assertIn("move_resource_plan", replay)
            self.assertEqual(
                replay["move_resource_plan"],
                route["move_resource_plan"],
            )
            self.assertEqual(
                replay["move_resource_plan"]["baseline_time_plan_id"],
                replay["time_plan"]["plan_id"],
            )
            self.assertLessEqual(
                replay["move_resource_plan"]["resource_envelope"]["cpu_ms"],
                replay["time_plan"]["envelope"]["cpu_ms"],
            )
            self.assertEqual(
                verify_move_resource_plan_manifest(replay),
                [],
            )

    def test_frontend_fallback_plan_preserves_baseline_envelope(self):
        with shell_fixture() as (shell, manager, shadow, output, tmp):
            def planner(base):
                return build_move_resource_plan(
                    baseline=base,
                    settings=SETTINGS,
                    host=None,
                    composition=CATALOG.default_composition,
                    catalog_id=CATALOG.catalog_id,
                    catalog_digest=CATALOG.digest,
                )

            with patch.object(
                manager,
                "make_move_resource_plan",
                side_effect=planner,
            ):
                shell.handle_command("go movetime 500")
                wait_for(
                    lambda: bool(list(tmp.glob("replays/*/route.json"))),
                    timeout=5,
                )
                wait_for(
                    lambda: bool(list(tmp.glob("replays/*/manifest.json"))),
                    timeout=5,
                )

            run = next(tmp.glob("replays/*"))
            replay = json.loads((run / "manifest.json").read_text())
            item = replay["move_resource_plan"]
            self.assertEqual(item["disposition"], "FALLBACK")
            self.assertEqual(
                item["resource_envelope"],
                {
                    key: replay["time_plan"]["envelope"][key]
                    for key in (
                        "wall_ms",
                        "cpu_ms",
                        "gpu_ms",
                        "verification_reserve_fraction",
                        "refinement_reserve_fraction",
                        "controller_overhead_reserve_ms",
                    )
                },
            )

    def test_j7_operating_points_are_not_runtime_consumed(self):
        runtime = json.loads(
            (ROOT / "config/allfather.m14-j-j8.validation.json").read_text(
                encoding="utf-8"
            )
        )
        selection = json.loads(
            (ROOT / "qualification/resource-profile-selection-v1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(selection["claim_boundary"]["runtime_profile_selection"])
        self.assertFalse(selection["claim_boundary"]["adaptive_allocation"])
        stockfish = [
            spec
            for spec in runtime["instances"].values()
            if spec["family"] == "stockfish"
        ]
        self.assertTrue(all(spec["options"]["Hash"] == 16 for spec in stockfish))
        lc0 = next(
            spec for spec in runtime["instances"].values()
            if spec["family"] == "lc0"
        )
        self.assertEqual(lc0["options"]["MaxPrefetch"], 0)


if __name__ == "__main__":
    unittest.main()
