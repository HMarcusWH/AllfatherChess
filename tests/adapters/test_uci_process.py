#!/usr/bin/env python3
"""Tests for the production single-reader UCI process adapter."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.process import UciProcess


FAKE = ROOT / "tests" / "fixtures" / "fake_uci_engine.py"


class UciProcessTests(unittest.TestCase):
    def make_process(self, *, exit_on: str | None = None, on_exit=None) -> UciProcess:
        args = [str(FAKE), "--name", "FakeStockfish"]
        if exit_on is not None:
            args += ["--exit-on", exit_on]
        return UciProcess(
            name="fake",
            binary=Path(sys.executable),
            cwd=ROOT,
            args=args,
            timeout=3.0,
            on_exit=on_exit,
        )

    def test_ready_waiter_and_search_share_one_reader(self):
        process = self.make_process()
        infos: list[str] = []
        info_seen = threading.Event()
        complete_seen = threading.Event()
        completions: list[str] = []
        try:
            process.start()
            self.assertIn("UCI_Chess960", process.options)
            process.configure({"UCI_Chess960": False})

            process.start_search(
                "go infinite",
                token=11,
                on_info=lambda token, line: (infos.append(line), info_seen.set()),
                on_complete=lambda token, line: (completions.append(line), complete_seen.set()),
            )
            self.assertTrue(info_seen.wait(2.0), "fake engine emitted no search info")

            # This would race/hang if ready() and search had competing stdout readers.
            process.ready(timeout=2.0)
            self.assertTrue(process.active_search)

            process.stop()
            self.assertTrue(complete_seen.wait(2.0), "stop did not produce bestmove")
            self.assertEqual(completions, ["bestmove e2e4"])
            self.assertFalse(process.active_search)
            self.assertTrue(infos)
        finally:
            process.close()
        self.assertFalse(process.alive)

    def test_unexpected_exit_reports_active_token(self):
        exit_seen = threading.Event()
        exits: list[tuple[str, int | None, int | None]] = []

        def on_exit(name: str, rc: int | None, token: int | None) -> None:
            exits.append((name, rc, token))
            exit_seen.set()

        process = self.make_process(exit_on="go", on_exit=on_exit)
        try:
            process.start()
            process.start_search(
                "go nodes 1",
                token=42,
                on_info=lambda token, line: None,
                on_complete=lambda token, line: None,
            )
            self.assertTrue(exit_seen.wait(2.0), "unexpected exit callback did not fire")
            self.assertEqual(exits[0][0], "fake")
            self.assertEqual(exits[0][1], 7)
            self.assertEqual(exits[0][2], 42)
        finally:
            process.close()


if __name__ == "__main__":
    unittest.main()
