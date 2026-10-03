"""Regression coverage for the legacy LOCAL-1 prerequisite execution branch."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.local_game.common import QualificationError
import tools.local_game.runner as runner


class LegacyPrerequisiteRunnerTests(unittest.TestCase):
    def _exercise(self, *, positive_case=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            (root / "config").mkdir()
            (root / "build/test-results/lc0-strength").mkdir(parents=True)
            (root / "build/test-results/online-profile").mkdir(parents=True)
            (root / "build/test-results/online-hybrid").mkdir(parents=True)

            online_root = root / "replays-online"
            g3_root = root / "replays-g3"
            online_root.mkdir()
            g3_root.mkdir()
            (root / "config/allfather.online.cpu-reference.json").write_text(
                json.dumps({"shadow": {"replay_root": "replays-online"}}),
                encoding="utf-8",
            )
            (root / "config/allfather.online-hybrid.validation.json").write_text(
                json.dumps({"shadow": {"replay_root": "replays-g3"}}),
                encoding="utf-8",
            )

            def write_json(path: Path, value: dict) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding="utf-8")

            def fake_bounded(argv, cwd, log, timeout, **kwargs):
                script = argv[1]
                if script.endswith("lc0-strength-profile-contract.py"):
                    write_json(root / "build/test-results/lc0-strength/report.json", {"passed": True})
                elif script.endswith("qualify-online-profile.py"):
                    run = online_root / "online-run"
                    run.mkdir()
                    write_json(run / "manifest.json", {"run_id": "online-run"})
                    write_json(
                        root / "build/test-results/online-profile/report.json",
                        {"cases": [{"run_id": "online-run"}]},
                    )
                elif script.endswith("qualify-online-hybrid-authority.py"):
                    run = g3_root / "g3-run"
                    run.mkdir()
                    write_json(run / "manifest.json", {"run_id": "g3-run"})
                    write_json(
                        root / "build/test-results/online-hybrid/report.json",
                        {
                            "qualification_mode": "local1-mechanism",
                            "evidence_valid": True,
                            "mechanism_valid": True,
                            "positive_witness_observed": positive_case is not None,
                            "positive_case": positive_case,
                            "cases": [{"run_id": "g3-run"}],
                        },
                    )
                else:
                    raise AssertionError(script)
                return {"returncode": 0, "timed_out": False}

            source = {"commit": "a" * 40, "tree": "b" * 40}
            with (
                mock.patch.object(runner, "ROOT", root),
                mock.patch.object(runner, "bounded", side_effect=fake_bounded),
                mock.patch.object(
                    runner,
                    "verify_local1_g3_prerequisite_report",
                    return_value=None,
                ) as verifier,
            ):
                records = runner.prerequisites(output, {}, source)
                verifier.assert_called_once()
            retained = {
                "records": records,
                "online": (output / "online2-replays/online-run").is_dir(),
                "g3": (output / "g3-replays/g3-run").is_dir(),
            }
            return retained

    def test_mechanism_only_g3_prerequisite_reaches_replay_retention(self):
        retained = self._exercise(positive_case=None)
        self.assertEqual([row["id"] for row in retained["records"]], ["lc0", "online2", "g3"])
        self.assertTrue(retained["online"])
        self.assertTrue(retained["g3"])

    def test_optional_positive_case_must_belong_to_current_g3_report(self):
        with self.assertRaises(QualificationError):
            self._exercise(
                positive_case={
                    "run_id": "orphan-run",
                    "authority": "HYBRID",
                    "emitted_move": "e2e4",
                    "anchor_move": "d2d4",
                }
            )


if __name__ == "__main__":
    unittest.main()
