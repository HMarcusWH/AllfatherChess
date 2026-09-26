#!/usr/bin/env python3
"""Tests for the production single-reader UCI process adapter."""

from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.process import UciProcess, UciProcessError


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

    def test_idle_request_collects_perft_and_rejects_active_search(self):
        process = self.make_process()
        info_seen = threading.Event()
        try:
            process.start()
            process.configure({"UCI_Chess960": False})
            lines = process.run_idle_request(
                "go perft 1",
                lambda line: line == "Nodes searched: 3",
                label="fake perft",
                timeout=2.0,
            )
            self.assertEqual(
                [line for line in lines if line.endswith(": 1")],
                ["e2e4: 1", "d2d4: 1", "g1f3: 1"],
            )
            self.assertEqual(lines[-1], "Nodes searched: 3")

            process.start_search(
                "go infinite",
                token=19,
                on_info=lambda token, line: info_seen.set(),
                on_complete=lambda token, line: None,
            )
            self.assertTrue(info_seen.wait(2.0))
            with self.assertRaises(UciProcessError):
                process.run_idle_request(
                    "go perft 1",
                    lambda line: line.startswith("Nodes searched:"),
                    label="perft-during-search",
                    timeout=1.0,
                )
            process.stop()
        finally:
            process.close()

    def test_blocked_stdin_write_is_bounded_and_kills_backend(self):
        class BlockingStdin:
            def __init__(self):
                self.entered = threading.Event()
                self.release = threading.Event()

            def write(self, _value):
                self.entered.set()
                self.release.wait(5.0)
                return 1

            def flush(self):
                return None

        class StubProcess:
            def __init__(self):
                self.stdin = BlockingStdin()
                self.killed = False

            def poll(self):
                return -9 if self.killed else None

            def kill(self):
                self.killed = True
                self.stdin.release.set()

        process = UciProcess(
            name="blocked",
            binary=Path(sys.executable),
            cwd=ROOT,
            timeout=0.05,
        )
        stub = StubProcess()
        process.proc = stub  # type: ignore[assignment]

        started = time.monotonic()
        with self.assertRaisesRegex(UciProcessError, "timeout writing command"):
            process.send("isready", timeout=0.05)
        elapsed = time.monotonic() - started

        self.assertTrue(stub.stdin.entered.is_set())
        self.assertTrue(stub.killed)
        self.assertLess(elapsed, 1.0)

    def test_search_dispatch_gate_rechecks_permit_before_atomic_go_write(self):
        process = self.make_process()
        gate = threading.Lock()
        gate.acquire()
        allowed = {"value": True}
        failures: list[BaseException] = []

        def run_search():
            try:
                process.start_search(
                    "go nodes 1",
                    token=77,
                    on_info=lambda token, line: None,
                    on_complete=lambda token, line: None,
                    permit=lambda: allowed["value"],
                    dispatch_gate=gate,
                    timeout=1.0,
                )
            except BaseException as exc:
                failures.append(exc)

        try:
            process.start()
            process.configure({"UCI_Chess960": False})
            worker = threading.Thread(target=run_search, daemon=True)
            worker.start()

            # The subscription may be staged while the physical go write is
            # waiting at the coordinator's final dispatch boundary.
            time.sleep(0.05)
            allowed["value"] = False
            gate.release()
            worker.join(timeout=1.0)

            self.assertFalse(worker.is_alive())
            self.assertTrue(failures)
            self.assertIsInstance(failures[0], UciProcessError)
            self.assertIn("window closed", str(failures[0]))
            self.assertFalse(
                any(line == ">> go nodes 1" for line in process._transcript),
                "go crossed the pipe after the final permit was revoked",
            )
            self.assertFalse(process.active_search)
        finally:
            if gate.locked():
                gate.release()
            process.close()

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
