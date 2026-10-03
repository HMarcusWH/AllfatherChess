#!/usr/bin/env python3
from __future__ import annotations

import copy
import unittest
from pathlib import Path

from tools.local_game.common import (
    QualificationError,
    ROOT,
    load,
    runtime_config,
    verify_controller_derivation,
)
from tools.local_game.validate import expected_session_launch
from tools.meta1.common import command, policy, schedule
from tools.meta1.layout import ProducerLayout


class Meta1RelocationTests(unittest.TestCase):
    def setUp(self):
        self.p = policy()
        self.source = load(ROOT / self.p["source_runtime"])
        self.campaign_id = "aaaaaaaaaaaa-123-1"
        self.producer_root = Path(
            "/srv/self-hosted/_work/AllfatherChess/AllfatherChess"
        )
        self.campaign_root = (
            self.producer_root / "build/test-results/meta1" / self.campaign_id
        )
        self.fastchess = (
            self.producer_root / "build/tools/fastchess/bin/fastchess"
        )
        self.python = "/opt/python/bin/python3"
        self.layout = ProducerLayout.create(
            repo_root=self.producer_root,
            campaign_root=self.campaign_root,
            fastchess=self.fastchess,
            python_executable=self.python,
            campaign_id=self.campaign_id,
        )

    def test_layout_reconstructs_producer_command_from_different_workspace(self):
        plan = schedule(self.p)[0]
        producer_directory = self.layout.block_directory(plan["id"])
        producer_opening = producer_directory / plan["opening"]
        argv = command(
            plan,
            producer_directory,
            self.p,
            self.source,
            Path(self.layout.fastchess),
            producer_opening,
            write_specs=False,
            root=Path(self.layout.repo_root),
            python_executable=self.layout.python_executable,
        )
        self.assertEqual(argv[0], str(self.fastchess))
        self.assertIn(f"dir={self.producer_root}", argv)
        self.assertIn(f"cmd={self.python}", argv)
        self.assertIn(f"file={producer_opening}", argv)
        self.assertNotIn(str(ROOT), " ".join(argv))

    def test_session_launch_reconstructs_producer_paths(self):
        plan = schedule(self.p)[0]
        producer_directory = self.layout.block_directory(plan["id"])
        session_name = "session-001"
        spec, launch, producer_session = expected_session_launch(
            "allfather-orchestrated",
            self.source,
            self.p["source_runtime"],
            session_name=session_name,
            producer_root=self.producer_root,
            producer_directory=producer_directory,
            producer_python_executable=self.python,
        )
        self.assertEqual(spec["root"], str(self.producer_root))
        self.assertEqual(
            spec["sessions"],
            str(producer_directory / "sessions/allfather-orchestrated"),
        )
        self.assertEqual(
            launch,
            [
                self.python,
                "-m",
                "controller",
                "--config",
                str(producer_session / "runtime.json"),
            ],
        )
        _, stock_launch, _ = expected_session_launch(
            "stockfish",
            self.source,
            self.p["source_runtime"],
            session_name=session_name,
            producer_root=self.producer_root,
            producer_directory=producer_directory,
            producer_python_executable=self.python,
        )
        self.assertEqual(
            stock_launch[0],
            str(
                self.producer_root
                / self.source["instances"]["stockfish-anchor"]["binary"]
            ),
        )

    def test_runtime_accepts_only_declared_root_replay_relocation(self):
        producer_replay = (
            self.campaign_root
            / "meta1-00/sessions/allfather-anchor-control/session-001/replays"
        )
        config = runtime_config(
            self.source,
            "allfather-anchor-control",
            self.producer_root,
            producer_replay,
        )
        verify_controller_derivation(
            self.source,
            config,
            "allfather-anchor-control",
            self.producer_root,
            producer_replay,
        )
        tampered = copy.deepcopy(config)
        tampered["routing"]["checkpoint_interval_ms"] += 1
        with self.assertRaises(QualificationError):
            verify_controller_derivation(
                self.source,
                tampered,
                "allfather-anchor-control",
                self.producer_root,
                producer_replay,
            )

    def test_layout_rejects_forged_roots_and_executables(self):
        cases = [
            dict(
                repo_root=self.producer_root,
                campaign_root=self.producer_root / "tmp" / self.campaign_id,
                fastchess=self.fastchess,
                python_executable=self.python,
            ),
            dict(
                repo_root=self.producer_root,
                campaign_root=self.campaign_root,
                fastchess=self.producer_root / "bin/fastchess",
                python_executable=self.python,
            ),
            dict(
                repo_root="relative/repo",
                campaign_root=self.campaign_root,
                fastchess=self.fastchess,
                python_executable=self.python,
            ),
            dict(
                repo_root=self.producer_root,
                campaign_root=self.campaign_root,
                fastchess=self.fastchess,
                python_executable="python3",
            ),
        ]
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(QualificationError):
                    ProducerLayout.create(
                        **case,
                        campaign_id=self.campaign_id,
                    )


if __name__ == "__main__":
    unittest.main()
