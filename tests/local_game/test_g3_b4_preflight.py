"""Integration regression: frozen positive G3 prerequisite gates LOCAL-1 games."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tools.local_game.runner as runner
from tools.engine_opt.g3_b4 import B4G3ContractError, B4_POLICY, CANDIDATE_POLICY, LEGACY_POLICY, WITNESS_CORPUS, file_hash

ROOT = Path(__file__).resolve().parents[2]


class CanonicalG3PreflightTests(unittest.TestCase):
    def exercise(self, *, positive: bool):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (B4_POLICY, CANDIDATE_POLICY, LEGACY_POLICY, WITNESS_CORPUS):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / name).read_bytes())
            campaign = root / "build/attempt/prerequisites"
            campaign.mkdir(parents=True)
            replay = root / "build/replays-online-hybrid-v2"
            replay.mkdir(parents=True)
            case = {
                "case": "discovery-base-00-03-g000016",
                "run_id": "g000016",
                "authority": "HYBRID",
                "authorization_policy": "clocked_staged_preanchor_v1",
                "authorization_granted": True,
                "terminal_source": "staged_verification",
                "route_action": "BUY_STAGED_VERIFY",
                "staged_complete": True,
                "anchor_move": "e8d6",
                "proposal_move": "h7h6",
                "emitted_move": "h7h6",
                "envelope_claimed": True,
                "resource_qualified": True,
                "route_resource_qualified": True,
                "clock_outcome": {"output_within_deadline": True},
            }
            g3 = {
                "evidence_valid": True, "authority_qualified": positive,
                "passed": positive,
                "cases": [case], "positive_case": case if positive else None,
                "contracts": {"policy_sha256": file_hash(root, B4_POLICY)},
            }
            def bounded(argv, cwd, log, timeout, **kwargs):
                self.assertTrue(str(log).startswith(str(campaign)))
                name = Path(argv[1]).name
                if name == "qualify-engine-opt-v2.py":
                    target = root / "build/test-results/engine-opt-v2/report.json"
                    data = {"passed": True, "candidate_identity_valid": True}
                elif name == "qualify-online-hybrid-v2-b4.py":
                    target = root / "build/test-results/online-hybrid-v2/report.json"
                    data = g3
                    (replay / case["run_id"]).mkdir()
                else:
                    raise AssertionError(f"unexpected prerequisite {name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(data))
                return {"returncode": 0, "timed_out": False}
            def retain(source_root, destination, run_ids, prior, label):
                self.assertEqual(run_ids, ["g000016"])
                self.assertEqual(label, "g3-v2")
                destination.mkdir()
                (destination / "g000016").mkdir()

            spec = {
                "qualification": {"require_g3_positive_prerequisite": True},
                "prerequisites": [
                    {"id": "engine-opt-v2", "script": "scripts/qualify-engine-opt-v2.py",
                     "report": "build/test-results/engine-opt-v2/report.json"},
                    {"id": "g3-v2", "script": "scripts/qualify-online-hybrid-v2-b4.py",
                     "report": "build/test-results/online-hybrid-v2/report.json",
                     "replay_root": "build/replays-online-hybrid-v2",
                     "retain_case_replays": True},
                ],
            }
            with (mock.patch.object(runner, "ROOT", root),
                  mock.patch.object(runner, "bounded", side_effect=bounded),
                  mock.patch.object(runner, "retain_report_runs", side_effect=retain)):
                if positive:
                    rows = runner.prerequisites(campaign, spec, {"commit": "a" * 40})
                    self.assertEqual([row["id"] for row in rows], ["engine-opt-v2", "g3-v2"])
                else:
                    with self.assertRaisesRegex(B4G3ContractError, "positive authority witness missing"):
                        runner.prerequisites(campaign, spec, {"commit": "a" * 40})
            # The negative attempt must remain auditable when the campaign stops.
            self.assertTrue((campaign / "g3-v2.json").is_file())
            self.assertTrue((campaign / "g3-v2-replays/g000016").is_dir())
            self.assertEqual(len(json.loads((campaign / "prerequisites.json").read_text())), 2)

    def test_negative_g3_stops_before_any_games_after_replay_retention(self):
        self.exercise(positive=False)

    def test_positive_g3_reaches_local1_campaign(self):
        self.exercise(positive=True)


if __name__ == "__main__":
    unittest.main()
