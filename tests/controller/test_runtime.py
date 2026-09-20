#!/usr/bin/env python3
"""Tests for transactional three-backend runtime ownership."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.runtime import BackendManager, RuntimeError


FAKE = ROOT / "tests" / "fixtures" / "fake_uci_engine.py"


def write_config(directory: Path, *, lc0_exit_on_uci: bool = False) -> Path:
    backends = {}
    for name in ("stockfish", "reckless", "lc0"):
        args = [str(FAKE), "--name", f"Fake-{name}"]
        if name == "lc0" and lc0_exit_on_uci:
            args += ["--exit-on", "uci"]
        backends[name] = {
            "binary": sys.executable,
            "cwd": ".",
            "args": args,
            "options": {"UCI_Chess960": False},
        }
    path = directory / "runtime.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "anchor",
                "anchor": "stockfish",
                "root": ".",
                "backends": backends,
            }
        ),
        encoding="utf-8",
    )
    return path


class BackendManagerTests(unittest.TestCase):
    def test_start_sync_and_close_all_three_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = BackendManager.from_path(write_config(Path(tmp)))
            manager.start()
            processes = list(manager.backends.values())
            try:
                self.assertTrue(manager.healthy)
                self.assertEqual(set(manager.backends), {"stockfish", "reckless", "lc0"})
                self.assertTrue(all(process.alive for process in processes))
                manager.new_game()
                manager.set_position("position startpos moves e2e4")
                manager.set_chess960(False)
                manager.ready_all()
            finally:
                manager.close()
            self.assertTrue(all(not process.alive for process in processes))

    def test_partial_startup_failure_closes_already_started_backends(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = BackendManager.from_path(
                write_config(Path(tmp), lc0_exit_on_uci=True)
            )
            with self.assertRaises(RuntimeError):
                manager.start()
            self.assertTrue(manager.backends)
            self.assertTrue(all(not process.alive for process in manager.backends.values()))


if __name__ == "__main__":
    unittest.main()
