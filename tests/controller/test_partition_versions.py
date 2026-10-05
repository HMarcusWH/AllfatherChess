#!/usr/bin/env python3
"""Versioned fixed-reserve semantics and authenticated legacy replay tests."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from common.search_request import parse_position_command
from controller.budget import BudgetLedger, ResourceEnvelope
from controller.online_time import (
    OnlineTimeSettings,
    make_time_plan,
    verify_time_manifest,
)
from controller import replay_history as history


def make_plan():
    return make_time_plan(
        command="go movetime 1200",
        position=parse_position_command("position startpos"),
        generation=1,
        settings=OnlineTimeSettings(),
        envelope=ResourceEnvelope(
            wall_ms=4000.0,
            cpu_ms=12000.0,
            verification_reserve_fraction=0.3,
            controller_overhead_reserve_ms=250.0,
        ),
        received_monotonic=1.0,
        controller_cpu_started_ns=1,
    )


def manifest(plan):
    position = parse_position_command("position startpos")
    return {
        "generation": 1,
        "run_id": "run",
        "time_plan": plan.as_dict(),
        "position": {
            "base_fen": position.base_fen,
            "moves": [],
            "variant": "standard",
            "position_id": position.position_id,
        },
        "external_request": {"command": plan.external_go_command},
        "stages": [{
            "role": "anchor",
            "command": plan.anchor_go_command,
            "bestmove": "e2e4",
        }],
        "clock_outcome": {
            "emitted_line": "bestmove e2e4",
            "emitted_ms": 100.0,
            "failure": None,
            "output_within_deadline": True,
        },
    }


def make_archive(root: Path, doc: dict, *, source: str = "a" * 40):
    archive = root / "history.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(
            "campaign/manifest.json",
            json.dumps({"source": {"commit": source}}),
        )
        handle.writestr(
            "campaign/run/manifest.json",
            json.dumps(doc),
        )
    registry = root / "registry.json"
    registry.write_text(
        json.dumps({
            "schema_version": 1,
            "sources": [{
                "artifact_sha256": history.file_sha256(archive),
                "source_commit": "a" * 40,
                "campaign_path": "campaign",
                "source_manifest": "campaign/manifest.json",
                "partition_policy": history.LEGACY_PROPORTIONAL_V1,
            }],
        }),
        encoding="utf-8",
    )
    return archive, registry


class PartitionTests(unittest.TestCase):
    def test_shared_clamp_preserves_fixed_work_and_fractions(self):
        base = ResourceEnvelope(
            wall_ms=4000,
            cpu_ms=12000,
            verification_reserve_fraction=.3,
            refinement_reserve_fraction=.1,
            controller_overhead_reserve_ms=250,
        )
        for cap in (6384, 7388, 2400, 1800, 400, 100, .5):
            with self.subTest(cap=cap):
                result = base.clamped_cpu_envelope(
                    cpu_ms=cap,
                    wall_ms=1200,
                )
                self.assertLessEqual(result.cpu_ms, base.cpu_ms)
                self.assertEqual(result.wall_ms, 1200)
                self.assertEqual(result.verification_reserve_fraction, .3)
                self.assertEqual(result.refinement_reserve_fraction, .1)
                self.assertAlmostEqual(
                    result.controller_overhead_reserve_ms,
                    min(250, cap * .6),
                )
                self.assertGreaterEqual(result.solver_cpu_ceiling_ms, 0)

    def test_fixed_cap_does_not_authorize_overspend(self):
        plan = make_plan()
        ledger = BudgetLedger(plan.envelope, clock=lambda: 0, started=0)
        ledger.charge_elapsed(
            "qualification",
            cpu_ms=251,
            purpose="controller",
        )
        self.assertFalse(ledger.within_partition_caps())

    def test_new_time_plan_has_explicit_partition_semantics(self):
        plan = make_plan()
        raw = plan.as_dict()
        self.assertEqual(
            raw["resource_partition_policy"],
            history.ABSOLUTE_CONTROLLER_V2,
        )
        self.assertEqual(verify_time_manifest(manifest(plan)), [])

    def test_stripping_or_changing_new_marker_is_not_legacy_fallback(self):
        for marker in (None, "proportional", "absolute-controller-v3"):
            doc = manifest(make_plan())
            if marker is None:
                doc["time_plan"].pop("resource_partition_policy")
            else:
                doc["time_plan"]["resource_partition_policy"] = marker
            self.assertTrue(verify_time_manifest(doc))

    def test_historical_manifest_requires_exact_archive_membership(self):
        base = make_plan()
        old = replace(
            base,
            envelope=history.historical_envelope(
                base.declared_envelope,
                cpu_ms=base.envelope.cpu_ms,
                wall_ms=base.envelope.wall_ms,
                policy=history.LEGACY_PROPORTIONAL_V1,
            ),
            resource_partition_policy=history.LEGACY_PROPORTIONAL_V1,
        )
        doc = manifest(old)
        self.assertNotEqual(
            old.envelope.controller_overhead_reserve_ms,
            250,
        )
        self.assertTrue(verify_time_manifest(doc))
        with tempfile.TemporaryDirectory() as tmp:
            archive, registry = make_archive(Path(tmp), doc)
            with patch.object(history, "REGISTRY", registry):
                with history.historical_replay_scope(archive) as binding:
                    self.assertFalse(binding["runtime_authority"])
                    self.assertEqual(verify_time_manifest(doc), [])
                    changed = copy.deepcopy(doc)
                    changed["unexpected"] = "tamper"
                    self.assertTrue(verify_time_manifest(changed))
                    changed = copy.deepcopy(doc)
                    changed["time_plan"]["envelope"][
                        "controller_overhead_reserve_ms"
                    ] = 250
                    self.assertTrue(verify_time_manifest(changed))
                self.assertTrue(verify_time_manifest(doc))

    def test_wrong_archive_or_source_is_rejected(self):
        base = make_plan()
        old = replace(
            base,
            envelope=history.historical_envelope(
                base.declared_envelope,
                cpu_ms=base.envelope.cpu_ms,
                wall_ms=base.envelope.wall_ms,
                policy=history.LEGACY_PROPORTIONAL_V1,
            ),
            resource_partition_policy=history.LEGACY_PROPORTIONAL_V1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, registry = make_archive(
                root,
                manifest(old),
                source="b" * 40,
            )
            with patch.object(history, "REGISTRY", registry):
                with self.assertRaises(ValueError):
                    with history.historical_replay_scope(archive):
                        pass

    def test_golden_discovery_plan_id_reconstructs_without_rewriting(self):
        moves = (
            "g1f3 g8f6 f3g1 f6g8 e2e4 e7e5 g1f3 b8c6 f1b5 "
            "g8f6 e1g1 f6e4 f1e1 e4d6 f3e5 f8e7 b5f1 c6e5 "
            "e1e5 e8g8 d2d4 e7f6 e5e1 f8e8 c2c3 e8e1 d1e1 "
            "d6e8 c1f4 d7d5 b1d2 c8f5 e1e2 c7c6 a1e1"
        )
        plan = make_time_plan(
            command="go wtime 16604 btime 21499 winc 1000 binc 1000",
            position=parse_position_command(
                "position startpos moves " + moves
            ),
            generation=16,
            settings=OnlineTimeSettings(
                max_move_ms=4000,
                stop_grace_ms=300,
                output_margin_ms=50,
                prepare_budget_ms=100,
                quiesce_budget_ms=250,
            ),
            envelope=ResourceEnvelope(
                wall_ms=4000.0,
                cpu_ms=12000.0,
                gpu_ms=0.0,
                verification_reserve_fraction=.3,
                controller_overhead_reserve_ms=250.0,
            ),
            received_monotonic=2173.490034259,
            controller_cpu_started_ns=1721013420,
        )
        old = replace(
            plan,
            envelope=history.historical_envelope(
                plan.declared_envelope,
                cpu_ms=7252.0,
                wall_ms=1813.0,
                policy=history.LEGACY_PROPORTIONAL_V1,
            ),
            resource_partition_policy=history.LEGACY_PROPORTIONAL_V1,
        )
        self.assertEqual(
            old.as_dict()["plan_id"],
            "time-2a98d0fb557e43baa7cf148b4042743bfc6a893876c650777dd25e432c1b326e",
        )
        self.assertEqual(
            old.envelope.controller_overhead_reserve_ms,
            151.08333333333334,
        )
        self.assertEqual(plan.envelope.controller_overhead_reserve_ms, 250.0)
        self.assertNotEqual(
            plan.as_dict()["plan_id"],
            old.as_dict()["plan_id"],
        )


if __name__ == "__main__":
    unittest.main()
