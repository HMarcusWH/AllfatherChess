#!/usr/bin/env python3
"""Counterfactual decision persistence and runtime integration tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.counterfactual import (
    load_counterfactual_artifact,
    replay_counterfactual_inputs,
    verify_counterfactual_integrity,
)
from controller.crossfeed import verify_crossfeed_integrity
from controller.runtime import RuntimeError, load_runtime_config
from controller.verification_analysis import (
    analyze_verification_bundle,
    load_verification_bundle,
)
from tests.controller.test_shadow_runtime import (
    ANCHOR,
    SHADOWS,
    run_shell,
    write_shadow_config,
)


class CounterfactualConfigTests(unittest.TestCase):
    def test_counterfactual_requires_crossfeed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_shadow_config(root, verification=True)
            doc = json.loads(path.read_text())
            doc["counterfactual"] = {
                "enabled": True,
                "policy": "unanimous_verify_v1",
            }
            path.write_text(json.dumps(doc))
            with self.assertRaises(RuntimeError) as ctx:
                load_runtime_config(path)
            self.assertIn("counterfactual requires crossfeed.enabled", str(ctx.exception))

    def test_counterfactual_rejects_unknown_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write_shadow_config(
                root,
                verification=True,
                counterfactual=True,
            )
            doc = json.loads(path.read_text())
            doc["counterfactual"]["policy"] = "majority_vote_v1"
            path.write_text(json.dumps(doc))
            with self.assertRaises(RuntimeError) as ctx:
                load_runtime_config(path)
            self.assertIn(
                "counterfactual.policy currently supports exactly",
                str(ctx.exception),
            )


class CounterfactualEndToEndTests(unittest.TestCase):
    def _run(
        self,
        root: Path,
        *,
        anchor_lines: int,
        shadow_lines: int,
        anchor_delay_ms: int = 25,
        shadow_delay_ms: int = 25,
    ) -> tuple[Path, list[str], dict]:
        instance_args = {
            ANCHOR: [
                "--info-lines",
                str(anchor_lines),
                "--info-delay-ms",
                str(anchor_delay_ms),
            ],
        }
        for instance in SHADOWS:
            instance_args[instance] = [
                "--info-lines",
                str(shadow_lines),
                "--info-delay-ms",
                str(shadow_delay_ms),
            ]

        config = write_shadow_config(
            root,
            verification=True,
            crossfeed=True,
            counterfactual=True,
            dispatch_nodes=96,
            verification_nodes=64,
            instance_args=instance_args,
            slow_anchor=False,
        )
        lines = run_shell(
            config,
            ["go nodes 64", "await:bestmove "],
            timeout=40.0,
        )
        run_dir = next(path for path in (root / "replays").iterdir() if path.is_dir())
        artifact = load_counterfactual_artifact(run_dir)
        return run_dir, lines, artifact

    def test_pre_anchor_proposal_is_sealed_and_outward_move_stays_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, lines, artifact = self._run(
                Path(tmp),
                anchor_lines=30,
                shadow_lines=2,
            )
            outward = [line for line in lines if line.startswith("bestmove ")]
            self.assertEqual(len(outward), 1)

            parent = json.loads((run_dir / "manifest.json").read_text())
            anchor = next(
                stage for stage in parent["stages"] if stage["role"] == "anchor"
            )
            self.assertEqual(outward[0], f"bestmove {anchor['bestmove']}")

            proposal = artifact["proposal"]
            self.assertTrue(proposal["frozen_before_anchor"])
            self.assertEqual(proposal["disposition"]["code"], "PROPOSED")
            self.assertEqual(artifact["counterfactual"]["outward_authority"], "stockfish-anchor")
            self.assertEqual(verify_crossfeed_integrity(run_dir), [])
            self.assertEqual(verify_counterfactual_integrity(run_dir), [])

            # Deterministic replay rebuilds the exact policy semantics from the
            # sealed cross-feed + VERIFY terminal facts.
            _, _, evidence, evaluation = replay_counterfactual_inputs(run_dir)
            self.assertEqual(evaluation.evidence_digest, proposal["evidence_digest"])
            self.assertEqual(evaluation.move, proposal["move"])

            # The existing independent VERIFY analyzer must agree about the raw
            # terminal unanimity fact without becoming a decision authority.
            analysis = analyze_verification_bundle(load_verification_bundle(run_dir))
            self.assertEqual(analysis["final_pattern"], "unanimous")
            self.assertEqual(
                analysis["final_unanimous_move"],
                proposal["move"],
            )

            # No decision/counterfactual search phase was introduced.
            all_search_ids = [
                str(stage.get("search_id", ""))
                for stage in parent["stages"]
            ]
            verification = json.loads(
                (run_dir / "verification" / "manifest.json").read_text()
            )
            all_search_ids += [
                str(stage.get("search_id", ""))
                for stage in verification["stages"]
            ]
            self.assertFalse(
                any(
                    "decision" in value.lower() or "counterfactual" in value.lower()
                    for value in all_search_ids
                )
            )

    def test_inflight_verify_can_freeze_post_anchor_without_gaining_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, lines, artifact = self._run(
                Path(tmp),
                anchor_lines=8,
                shadow_lines=4,
            )
            proposal = artifact["proposal"]
            self.assertFalse(proposal["frozen_before_anchor"])
            self.assertEqual(verify_counterfactual_integrity(run_dir), [])

            parent = json.loads((run_dir / "manifest.json").read_text())
            anchor = next(
                stage for stage in parent["stages"] if stage["role"] == "anchor"
            )
            outward = [line for line in lines if line.startswith("bestmove ")]
            self.assertEqual(outward, [f"bestmove {anchor['bestmove']}"])
            self.assertEqual(
                artifact["counterfactual"]["outward_authority"],
                "stockfish-anchor",
            )

    def test_tampered_proposal_semantics_fail_deterministic_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir, _, artifact = self._run(
                Path(tmp),
                anchor_lines=20,
                shadow_lines=2,
            )
            path = run_dir / "decision" / "counterfactual.json"
            artifact["proposal"]["move"] = "a2a3"
            path.write_text(
                json.dumps(artifact, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            problems = verify_counterfactual_integrity(run_dir)
            self.assertTrue(problems)
            self.assertTrue(
                any(
                    "deterministic policy replay" in item
                    or "content digest mismatch" in item
                    or "proposal/anchor relation" in item
                    for item in problems
                )
            )


if __name__ == "__main__":
    unittest.main()
