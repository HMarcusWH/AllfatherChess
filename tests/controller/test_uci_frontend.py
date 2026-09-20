#!/usr/bin/env python3
"""End-to-end fast tests for the external Allfather UCI shell using fake backends."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.harness.uci_session import UciSession


FAKE = ROOT / "tests" / "fixtures" / "fake_uci_engine.py"


def write_config(directory: Path) -> Path:
    backends = {
        name: {
            "binary": sys.executable,
            "cwd": ".",
            "args": [str(FAKE), "--name", f"Fake-{name}"],
            "options": {"UCI_Chess960": False},
        }
        for name in ("stockfish", "reckless", "lc0")
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


class UciFrontendTests(unittest.TestCase):
    def test_external_identity_anchor_search_and_isready_during_infinite(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = write_config(Path(tmp))
            with UciSession(
                Path(sys.executable),
                cwd=ROOT,
                timeout=5.0,
                args=["-m", "controller", "--config", str(config)],
            ) as session:
                transcript = "\n".join(session.transcript)
                self.assertIn("id name AllfatherChess", transcript)
                self.assertNotIn("Fake-stockfish", transcript)
                self.assertNotIn("Fake-reckless", transcript)
                self.assertNotIn("Fake-lc0", transcript)

                session.configure({"UCI_Chess960": False})
                session.new_game()
                session.set_position({"startpos_moves": []})
                lines = session.search_nodes(64, timeout=5.0)
                self.assertEqual(
                    [line for line in lines if line.startswith("bestmove ")],
                    ["bestmove e2e4"],
                )

                session.set_position({"startpos_moves": []})
                session.send("go infinite")
                session.read_until(
                    lambda line: line.startswith("info depth "),
                    label="infinite-search info",
                    timeout=3.0,
                )

                session.send("isready")
                ready_lines = session.read_until(
                    lambda line: line == "readyok",
                    label="readyok during active search",
                    timeout=3.0,
                )
                self.assertFalse(any(line.startswith("bestmove ") for line in ready_lines))

                session.send("go nodes 1")
                rejected = session.read_until(
                    lambda line: "rejected" in line and "anchor search is active" in line,
                    label="duplicate-go rejection",
                    timeout=3.0,
                )
                self.assertTrue(any("rejected" in line for line in rejected))

                session.send("stop")
                stop_lines = session.read_until(
                    lambda line: line.startswith("bestmove "),
                    label="bestmove after stop",
                    timeout=3.0,
                )
                self.assertEqual(stop_lines[-1], "bestmove e2e4")


if __name__ == "__main__":
    unittest.main()
