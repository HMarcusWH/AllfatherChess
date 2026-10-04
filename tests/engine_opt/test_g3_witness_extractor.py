#!/usr/bin/env python3
"""Destructive trust-boundary tests for retained G3 discovery extraction."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "g3_witness_extractor",
    ROOT / "scripts/extract-g3-candidate-witnesses.py",
)
assert SPEC and SPEC.loader
extractor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extractor)

SOURCE = "a" * 40
POLICY_SHA = "b" * 64


class WitnessExtractorTests(unittest.TestCase):
    def _build_tree(self, root: Path, *, passed=True, source_commit=SOURCE, omit_first_ply=False):
        campaign = root / "local1/campaign"
        campaign.mkdir(parents=True)
        common_domain = {"source_commit": SOURCE}
        common_bundle = {"source_commit": SOURCE}
        manifest = {
            "campaign_id": "campaign",
            "status": "completed",
            "source": {"commit": source_commit},
            "execution_domain": common_domain,
            "candidate_bundle": common_bundle,
            "policy": {
                "path": "qualification/local-full-game-v2-candidate.json",
                "sha256": POLICY_SHA,
            },
        }
        (campaign / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        plies = []
        for job_index in range(4):
            job = f"base-00-{job_index:02d}"
            for generation in range(1, 5):
                run_id = f"run-{job_index}-{generation}"
                run = campaign / job / "sessions/allfather-g3/session/replays" / run_id
                (run / "decision").mkdir(parents=True)
                position_id = f"pos-{job_index}-{generation}"
                anchor = "e2e4"
                proposal = "d2d4"
                run_manifest = {
                    "run_id": run_id,
                    "generation": generation,
                    "position": {
                        "position_id": position_id,
                        "command": "position startpos moves e2e4",
                        "moves": ["e2e4"],
                    },
                    "external_request": {
                        "command": "go wtime 20000 btime 20000 winc 1000 binc 1000"
                    },
                    "time_plan": {
                        "available_clock_ms": 20000,
                        "hard_budget_ms": 1500,
                    },
                    "clock_outcome": {"output_within_deadline": True},
                }
                (run / "manifest.json").write_text(json.dumps(run_manifest), encoding="utf-8")
                final = {
                    "decision": {
                        "authority": "HYBRID",
                        "anchor_move": anchor,
                        "proposal_move": proposal,
                        "emitted_move": proposal,
                        "authorization": {"authorized": True},
                        "authorization_snapshot": {
                            "route_action": "BUY_STAGED_VERIFY",
                            "route_buy_extension": True,
                            "staged_complete": True,
                            "terminal_source": "staged_verification",
                        },
                    }
                }
                (run / "decision/final.json").write_text(json.dumps(final), encoding="utf-8")
                (run / "decision/counterfactual.json").write_text("{}", encoding="utf-8")
                (run / "route.json").write_text(
                    json.dumps(
                        {
                            "resource_measurement": {"qualified": True},
                            "envelope_claim": {"claimed": True},
                        }
                    ),
                    encoding="utf-8",
                )
                (run / "resource.json").write_text(json.dumps({"qualified": True}), encoding="utf-8")
                plies.append(
                    {
                        "replay_id": run_id,
                        "replay_manifest_sha256": extractor.sha256_file(run / "manifest.json"),
                        "job": job,
                        "arm": "allfather-g3",
                        "authority": "HYBRID",
                        "authorization_granted": True,
                        "authorized_non_anchor": True,
                        "override": True,
                        "resource_qualified": True,
                        "anchor_move": anchor,
                        "proposal_move": proposal,
                        "move": proposal,
                    }
                )
        if omit_first_ply:
            plies = plies[1:]
        report = {
            "campaign_id": "campaign",
            "passed": passed,
            "validated_games": 28,
            "errors": [],
            "execution_scope": "required_local1",
            "execution_domain": common_domain,
            "candidate_bundle": common_bundle,
            "plies": plies,
        }
        (campaign / "report.json").write_text(json.dumps(report), encoding="utf-8")

    def _archive(self, source_root: Path, archive: Path):
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
            for path in sorted(source_root.rglob("*")):
                if path.is_file():
                    handle.write(path, path.relative_to(source_root).as_posix())

    def _metadata(self, archive: Path, path: Path, *, digest=None):
        value = {
            "schema_version": 1,
            "repository": "HMarcusWH/AllfatherChess",
            "workflow_run": 1,
            "artifact_id": 2,
            "artifact_name": "artifact",
            "artifact_sha256": digest or extractor.sha256_file(archive),
            "source_commit": SOURCE,
            "campaign_path": "local1/campaign",
            "campaign_policy_path": "qualification/local-full-game-v2-candidate.json",
            "campaign_policy_sha256": POLICY_SHA,
        }
        path.write_text(json.dumps(value), encoding="utf-8")

    def _run(self, root: Path, *, passed=True, source_commit=SOURCE, omit_first_ply=False,
             bundle_problems=None, final_problems=None, counterfactual_problems=None,
             digest=None):
        tree = root / "tree"
        self._build_tree(
            tree,
            passed=passed,
            source_commit=source_commit,
            omit_first_ply=omit_first_ply,
        )
        archive = root / "artifact.zip"
        self._archive(tree, archive)
        metadata = root / "source.json"
        self._metadata(archive, metadata, digest=digest)
        output = root / "witnesses.json"
        with (
            mock.patch.object(extractor, "verify_bundle_integrity",
                              return_value=bundle_problems or []),
            mock.patch.object(extractor, "verify_final_decision_integrity",
                              return_value=final_problems or []),
            mock.patch.object(extractor, "verify_counterfactual_integrity",
                              return_value=counterfactual_problems or []),
        ):
            return extractor.extract(archive, metadata, output)

    def test_happy_path_selects_sixteen_verified_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = self._run(Path(tmp))
        self.assertEqual(len(doc["cases"]), 16)
        self.assertEqual(len({row["discovery"]["position_id"] for row in doc["cases"]}), 16)

    def test_wrong_artifact_bytes_fail_digest_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(extractor.ExtractionError):
                self._run(Path(tmp), digest="0" * 64)

    def test_wrong_head_campaign_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(extractor.ExtractionError):
                self._run(Path(tmp), source_commit="c" * 40)

    def test_failed_campaign_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(extractor.ExtractionError):
                self._run(Path(tmp), passed=False)

    def test_modified_replay_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(extractor.ExtractionError):
                self._run(Path(tmp), bundle_problems=["tampered"])

    def test_fabricated_final_or_counterfactual_is_rejected(self):
        for field in ("final", "counterfactual"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                kwargs = (
                    {"final_problems": ["bad final"]}
                    if field == "final"
                    else {"counterfactual_problems": ["bad counterfactual"]}
                )
                with self.assertRaises(extractor.ExtractionError):
                    self._run(Path(tmp), **kwargs)

    def test_replay_missing_from_validated_game_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(extractor.ExtractionError):
                self._run(Path(tmp), omit_first_ply=True)

    def test_archive_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../escape", "no")
            with self.assertRaises(extractor.ExtractionError):
                extractor.safe_extract_zip(archive, root / "out")


if __name__ == "__main__":
    unittest.main()
