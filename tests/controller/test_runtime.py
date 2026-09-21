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

from controller.runtime import BackendManager, RuntimeError, load_runtime_config
from tests.controller.test_shadow_runtime import write_shadow_config


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
                self.assertEqual(
                    manager.legal_root_moves(),
                    ("e2e4", "d2d4", "g1f3"),
                )
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


class VerificationRuntimeConfigTests(unittest.TestCase):
    def test_verify_settings_are_shadow_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp), verification=True)
            config = load_runtime_config(path)
            self.assertEqual(config.mode, "shadow")
            self.assertIsNotNone(config.verification)
            self.assertEqual(config.verification.dispatch_limit, {"nodes": 80})

    def test_disabled_verification_block_does_not_enable_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp))
            document = json.loads(path.read_text(encoding="utf-8"))
            document["verification"] = {"enabled": False}
            path.write_text(json.dumps(document), encoding="utf-8")
            config = load_runtime_config(path)
            self.assertIsNone(config.verification)


if __name__ == "__main__":
    unittest.main()
