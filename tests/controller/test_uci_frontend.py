#!/usr/bin/env python3
"""End-to-end fast tests for the external Allfather UCI shell using fake backends."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.uci_frontend import ShellState, UciFrontend
from tests.harness.uci_session import UciSession


FAKE = ROOT / "tests" / "fixtures" / "fake_uci_engine.py"


def write_config(directory: Path, *, stockfish_exit_on_go: bool = False) -> Path:
    backends = {}
    for name in ("stockfish", "reckless", "lc0"):
        args = [str(FAKE), "--name", f"Fake-{name}"]
        if name == "stockfish" and stockfish_exit_on_go:
            args += ["--exit-on", "go"]
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


class _RuntimeStub:
    def __init__(self):
        self.healthy = True
        self.failure_handler = None

    def set_failure_handler(self, callback):
        self.failure_handler = callback


class _DecisionShadowStub:
    def __init__(self, decision):
        self.decision = decision
        self.emitted = []

    def note_anchor_complete(self, token, line):
        return self.decision

    def note_anchor_emitted(self, token, final_decision=None):
        self.emitted.append(token)


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

                session.send("position startpos moves d2d4")
                position_rejected = session.read_until(
                    lambda line: "rejected" in line and "position startpos" in line,
                    label="position-during-search rejection",
                    timeout=3.0,
                )
                self.assertTrue(any("rejected" in line for line in position_rejected))

                session.send("stop")
                stop_lines = session.read_until(
                    lambda line: line.startswith("bestmove "),
                    label="bestmove after stop",
                    timeout=3.0,
                )
                self.assertEqual(stop_lines[-1], "bestmove e2e4")

    def test_clocked_hybrid_authority_suppresses_anchor_score_and_pv_info(self):
        runtime = _RuntimeStub()
        runtime.config = SimpleNamespace(
            online_time=object(),
            hybrid_authority=SimpleNamespace(
                policy="clocked_staged_preanchor_v1"
            ),
        )
        output = io.StringIO()
        frontend = UciFrontend(runtime, output=output)
        frontend._state = ShellState.SEARCHING
        frontend._active_generation = 7

        frontend._on_search_info(
            7,
            "info depth 12 score cp 31 pv d2d4 d7d5 c2c4",
        )

        self.assertEqual(
            output.getvalue(),
            "",
            "Stockfish anchor evaluation leaked onto a potentially overridden move",
        )

    def test_different_hybrid_root_drops_anchor_ponder_and_emits_once(self):
        runtime = _RuntimeStub()
        output = io.StringIO()
        decision = SimpleNamespace(
            authority="HYBRID",
            emitted_move="e2e4",
            anchor_move="d2d4",
        )
        shadow = _DecisionShadowStub(decision)
        frontend = UciFrontend(runtime, output=output, shadow=shadow)
        frontend._state = ShellState.SEARCHING
        frontend._active_generation = 7

        frontend._on_search_complete(7, "bestmove d2d4 ponder d7d5")

        self.assertEqual(output.getvalue().splitlines(), ["bestmove e2e4"])
        self.assertEqual(shadow.emitted, [7])
        self.assertEqual(frontend.state, ShellState.READY)

    def test_anchor_fallback_preserves_original_bestmove_line_byte_for_byte(self):
        runtime = _RuntimeStub()
        output = io.StringIO()
        decision = SimpleNamespace(
            authority="ANCHOR_FALLBACK",
            emitted_move="d2d4",
            anchor_move="d2d4",
        )
        shadow = _DecisionShadowStub(decision)
        frontend = UciFrontend(runtime, output=output, shadow=shadow)
        frontend._state = ShellState.SEARCHING
        frontend._active_generation = 8

        frontend._on_search_complete(8, "bestmove d2d4 ponder d7d5")

        self.assertEqual(
            output.getvalue().splitlines(),
            ["bestmove d2d4 ponder d7d5"],
        )
        self.assertEqual(shadow.emitted, [8])

    def test_anchor_failure_fails_closed_without_backend_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = write_config(Path(tmp), stockfish_exit_on_go=True)
            with UciSession(
                Path(sys.executable),
                cwd=ROOT,
                timeout=5.0,
                args=["-m", "controller", "--config", str(config)],
            ) as session:
                session.set_position({"startpos_moves": []})
                session.send("go nodes 1")
                lines = session.read_until(
                    lambda line: line == "bestmove 0000",
                    label="fail-closed null bestmove",
                    timeout=3.0,
                )
                self.assertTrue(any("runtime failure" in line for line in lines))
                self.assertFalse(any(line == "bestmove e2e4" for line in lines))


if __name__ == "__main__":
    unittest.main()
