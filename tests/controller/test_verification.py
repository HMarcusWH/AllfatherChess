#!/usr/bin/env python3
"""VERIFY-v1 planning, config and raw-artifact regressions."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.runtime import RuntimeError, VerificationSettings, load_runtime_config
from controller.verification import (
    VerificationError,
    build_verification_plan,
    load_verification_manifest,
    verify_verification_integrity,
)
from tests.controller.test_shadow_runtime import ANCHOR, run_shell, write_shadow_config


class VerificationPlanTests(unittest.TestCase):
    def _plan(self, **overrides):
        kwargs = {
            "generation": 7,
            "source_run_id": "run-7",
            "position_id": "pos-7",
            "settings": VerificationSettings(
                enabled=True,
                nomination_method="owner_bestmove_union_v1",
                dispatch_limit={"nodes": 80},
            ),
            "owners": ("stockfish", "reckless", "lc0"),
            "instance_by_owner": {
                "stockfish": "stockfish-shadow",
                "reckless": "reckless-shadow",
                "lc0": "lc0-shadow",
            },
            "owner_bestmoves": {
                "stockfish": "e2e4",
                "reckless": "d2d4",
                "lc0": "g1f3",
            },
            "owner_roots": {
                "stockfish": ("e2e4", "b1c3"),
                "reckless": ("d2d4", "c2c4"),
                "lc0": ("g1f3", "g2g3"),
            },
        }
        kwargs.update(overrides)
        return build_verification_plan(**kwargs)

    def test_nomination_is_exact_owner_order_union(self):
        plan = self._plan()
        self.assertEqual(plan.candidate_roots, ("e2e4", "d2d4", "g1f3"))
        self.assertEqual(
            plan.participants,
            {
                "stockfish": "stockfish-shadow",
                "reckless": "reckless-shadow",
                "lc0": "lc0-shadow",
            },
        )

    def test_duplicate_nominee_is_an_invariant_failure_not_consensus(self):
        with self.assertRaises(VerificationError) as ctx:
            self._plan(
                owner_bestmoves={
                    "stockfish": "e2e4",
                    "reckless": "e2e4",
                    "lc0": "g1f3",
                },
                owner_roots={
                    "stockfish": ("e2e4",),
                    "reckless": ("e2e4",),
                    "lc0": ("g1f3",),
                },
            )
        self.assertIn("not distinct", str(ctx.exception))

    def test_nominee_must_stay_inside_its_explore_region(self):
        with self.assertRaises(VerificationError):
            self._plan(
                owner_bestmoves={
                    "stockfish": "a2a3",
                    "reckless": "d2d4",
                    "lc0": "g1f3",
                }
            )


class VerificationConfigTests(unittest.TestCase):
    def test_shipped_verify_profile_loads_as_shadow_not_a_new_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            shipped = json.loads(
                (ROOT / "config" / "allfather.verify.validation.json").read_text(
                    encoding="utf-8"
                )
            )
            for spec in shipped["instances"].values():
                spec["binary"] = sys.executable
                spec.pop("fallback_glob", None)
            shipped["root"] = "."
            path = Path(tmp) / "verify.json"
            path.write_text(json.dumps(shipped), encoding="utf-8")
            config = load_runtime_config(path)
            self.assertEqual(config.mode, "shadow")
            self.assertIsNotNone(config.verification)
            self.assertEqual(
                config.verification.nomination_method,
                "owner_bestmove_union_v1",
            )

    def test_boolean_verify_nodes_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp), verification=True)
            document = json.loads(path.read_text(encoding="utf-8"))
            document["verification"]["dispatch_limit"]["nodes"] = True
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_runtime_config(path)

    def test_unknown_nomination_method_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp), verification=True)
            document = json.loads(path.read_text(encoding="utf-8"))
            document["verification"]["nomination_method"] = "vote_v0"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_runtime_config(path)


class VerificationArtifactTests(unittest.TestCase):
    def _run(self, root: Path) -> Path:
        config = write_shadow_config(
            root,
            verification=True,
            dispatch_nodes=80,
            verification_nodes=80,
            instance_args={ANCHOR: ["--info-lines", "50", "--info-delay-ms", "10"]},
        )
        run_shell(config, ["go nodes 64", "await:bestmove "], timeout=30.0)
        runs = sorted(path for path in (root / "replays").iterdir() if path.is_dir())
        self.assertEqual(len(runs), 1)
        self.assertTrue((runs[0] / "verification" / "manifest.json").is_file())
        return runs[0]

    def test_parent_hash_tamper_breaks_verification_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run(Path(tmp))
            self.assertEqual(verify_verification_integrity(run_dir), [])
            parent = run_dir / "manifest.json"
            parent.write_text(parent.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            problems = verify_verification_integrity(run_dir)
            self.assertTrue(any("source manifest hash mismatch" in p for p in problems))

    def test_stream_tamper_breaks_verification_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self._run(Path(tmp))
            manifest = load_verification_manifest(run_dir)
            target = run_dir / "verification" / manifest["streams"][0]["path"]
            target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            problems = verify_verification_integrity(run_dir)
            self.assertTrue(any("stream hash mismatch" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
