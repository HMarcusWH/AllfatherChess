#!/usr/bin/env python3
"""Shadow REFINE planning, configuration, live execution and integrity tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.refinement import (
    RefinementError,
    build_refinement_plan,
    load_refinement_manifest,
    partition_children,
    verify_refinement_integrity,
)
from controller.runtime import (
    RefinementSettings,
    RuntimeError,
    VerificationSettings,
    load_runtime_config,
)
from controller.verification import VerificationRun, build_verification_plan
from tests.controller.test_shadow_runtime import (
    ANCHOR,
    run_shell,
    write_shadow_config,
)


OWNERS = ("stockfish", "reckless", "lc0")
INSTANCES = {
    "stockfish": "stockfish-shadow",
    "reckless": "reckless-shadow",
    "lc0": "lc0-shadow",
}


def completed_verification(
    root: Path,
    *,
    finals: dict[str, str],
) -> VerificationRun:
    settings = VerificationSettings(
        enabled=True,
        nomination_method="owner_bestmove_union_v1",
        dispatch_limit={"nodes": 64},
    )
    plan = build_verification_plan(
        generation=4,
        source_run_id="run-4",
        position_id="pos-4",
        settings=settings,
        owners=OWNERS,
        instance_by_owner=INSTANCES,
        owner_bestmoves={
            "stockfish": "e2e4",
            "reckless": "d2d4",
            "lc0": "g1f3",
        },
        owner_roots={
            "stockfish": ("e2e4",),
            "reckless": ("d2d4",),
            "lc0": ("g1f3",),
        },
    )
    run = VerificationRun(plan=plan, run_dir=root)
    for index, owner in enumerate(OWNERS):
        stage = run.record_dispatch(
            owner=owner,
            instance=INSTANCES[owner],
            family=owner,
            search_id=f"verify-{owner}",
            command="go nodes 64 searchmoves e2e4 d2d4 g1f3",
            dispatched_ms=float(index),
        )
        run.record_completion(
            stage,
            completed_ms=float(index + 1),
            disposition="completed",
            bestmove=finals[owner],
        )
    run.set_disposition("completed")
    return run


class RefinementPlanTests(unittest.TestCase):
    def settings(self, *, max_targets: int = 3) -> RefinementSettings:
        return RefinementSettings(
            enabled=True,
            nomination_method="verify_final_disagreement_union_v1",
            child_partition="child_index_modulo",
            dispatch_limit={"nodes": 32},
            max_targets=max_targets,
        )

    def test_unanimous_verify_produces_no_refinement_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            verification = completed_verification(
                Path(tmp),
                finals={owner: "d2d4" for owner in OWNERS},
            )
            plan = build_refinement_plan(
                settings=self.settings(),
                verification=verification,
            )
        self.assertEqual(plan.targets, ())

    def test_two_one_targets_preserve_verify_candidate_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            verification = completed_verification(
                Path(tmp),
                finals={
                    "stockfish": "e2e4",
                    "reckless": "d2d4",
                    "lc0": "d2d4",
                },
            )
            plan = build_refinement_plan(
                settings=self.settings(),
                verification=verification,
            )
        self.assertEqual(
            [target.root_move for target in plan.targets],
            ["e2e4", "d2d4"],
        )
        self.assertEqual(
            [target.source_owner for target in plan.targets],
            ["stockfish", "reckless"],
        )

    def test_all_different_is_deterministically_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            verification = completed_verification(
                Path(tmp),
                finals={
                    "stockfish": "e2e4",
                    "reckless": "d2d4",
                    "lc0": "g1f3",
                },
            )
            plan = build_refinement_plan(
                settings=self.settings(max_targets=2),
                verification=verification,
            )
        self.assertEqual(
            [target.root_move for target in plan.targets],
            ["e2e4", "d2d4"],
        )

    def test_refinement_requires_completed_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            verification = completed_verification(
                Path(tmp),
                finals={
                    "stockfish": "e2e4",
                    "reckless": "d2d4",
                    "lc0": "g1f3",
                },
            )
            verification.set_disposition("incomplete")
            with self.assertRaises(RefinementError):
                build_refinement_plan(
                    settings=self.settings(),
                    verification=verification,
                )

    def test_child_partition_is_exact_disjoint_and_ordered(self):
        children = ("e7e5", "c7c5", "e7e6", "c7c6", "g8f6")
        partition = partition_children(children, OWNERS)
        self.assertEqual(
            partition,
            {
                "stockfish": ("e7e5", "c7c6"),
                "reckless": ("c7c5", "g8f6"),
                "lc0": ("e7e6",),
            },
        )
        union = [move for owner in OWNERS for move in partition[owner]]
        self.assertEqual(set(union), set(children))
        self.assertEqual(len(union), len(set(union)))


class RefinementConfigTests(unittest.TestCase):
    def test_shipped_refine_profile_loads_as_shadow(self):
        with tempfile.TemporaryDirectory() as tmp:
            shipped = json.loads(
                (ROOT / "config" / "allfather.refine.validation.json").read_text(
                    encoding="utf-8"
                )
            )
            for spec in shipped["instances"].values():
                spec["binary"] = sys.executable
                spec.pop("fallback_glob", None)
            shipped["root"] = "."
            path = Path(tmp) / "refine.json"
            path.write_text(json.dumps(shipped), encoding="utf-8")
            config = load_runtime_config(path)
        self.assertEqual(config.mode, "shadow")
        self.assertIsNotNone(config.verification)
        self.assertIsNotNone(config.refinement)
        self.assertEqual(
            config.refinement.nomination_method,
            "verify_final_disagreement_union_v1",
        )

    def test_refinement_requires_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp))
            document = json.loads(path.read_text(encoding="utf-8"))
            document["refinement"] = {
                "enabled": True,
                "nomination_method": "verify_final_disagreement_union_v1",
                "child_partition": "child_index_modulo",
                "dispatch_limit": {"nodes": 8},
                "max_targets": 3,
            }
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_runtime_config(path)

    def test_refinement_rejects_bad_partition_and_target_cap(self):
        for key, value in (
            ("child_partition", "score_weighted"),
            ("max_targets", 0),
            ("max_targets", True),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                path = write_shadow_config(Path(tmp), refinement=True)
                document = json.loads(path.read_text(encoding="utf-8"))
                document["refinement"][key] = value
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    load_runtime_config(path)


class RefinementLiveArtifactTests(unittest.TestCase):
    def _run(self, root: Path) -> Path:
        config = write_shadow_config(
            root,
            refinement=True,
            dispatch_nodes=64,
            verification_nodes=32,
            refinement_nodes=32,
            instance_args={
                ANCHOR: ["--info-lines", "150", "--info-delay-ms", "10"],
                "stockfish-shadow": ["--leader-schedule", "e2e4"],
                "reckless-shadow": ["--leader-schedule", "d2d4"],
                "lc0-shadow": ["--leader-schedule", "g1f3"],
            },
        )
        run_shell(config, ["go nodes 64", "await:bestmove "], timeout=30.0)
        runs = sorted(path for path in (root / "replays").iterdir() if path.is_dir())
        self.assertEqual(len(runs), 1)
        run_dir = runs[0]
        self.assertTrue((run_dir / "verification" / "manifest.json").is_file())
        self.assertTrue((run_dir / "refinement" / "manifest.json").is_file())
        return run_dir

    def test_live_refinement_is_separate_complete_and_integrity_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run(Path(tmp))
            self.assertEqual(verify_refinement_integrity(run_dir), [])
            manifest = load_refinement_manifest(run_dir)

            self.assertEqual(
                manifest["nomination"]["targets"],
                ["e2e4", "d2d4", "g1f3"],
            )
            self.assertEqual(manifest["disposition"]["run"], "completed")
            self.assertEqual(len(manifest["targets"]), 3)
            for target in manifest["targets"]:
                self.assertEqual(target["disposition"]["target"], "completed")
                self.assertEqual(len(target["child_oracle"]["children"]), 6)
                self.assertEqual(len(target["stages"]), 3)
                self.assertEqual(len(target["streams"]), 3)
                all_children = set(target["child_oracle"]["children"])
                owned: list[str] = []
                for owner in OWNERS:
                    owned.extend(target["child_partition"][owner])
                self.assertEqual(set(owned), all_children)
                self.assertEqual(len(owned), len(set(owned)))

            parent = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(
                any(":refine:" in stage["search_id"] for stage in parent["stages"])
            )
            verification = json.loads(
                (run_dir / "verification" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("REFINE", json.dumps(verification))

    def test_refinement_stream_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run(Path(tmp))
            manifest = load_refinement_manifest(run_dir)
            record = manifest["targets"][0]["streams"][0]
            path = run_dir / "refinement" / record["path"]
            path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            problems = verify_refinement_integrity(run_dir)
            self.assertTrue(any("stream hash mismatch" in item for item in problems))


if __name__ == "__main__":
    unittest.main()
