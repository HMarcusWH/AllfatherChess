"""Regressions for direct log capture and bounded bridge subprocess cleanup."""
from __future__ import annotations
import hashlib
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy.lichess.log_capture import publish_log, shutdown_bridge


@unittest.skipUnless(os.name == "posix", "requires POSIX process groups")
class BridgeLogLifecycleTests(unittest.TestCase):
    def test_normal_exit_and_token_redaction(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            raw = root / "raw.log"
            output = root / "bridge.log"
            with raw.open("wb") as f:
                proc = subprocess.Popen([
                    sys.executable, "-c",
                    "import sys; print('uci bestmove e2e4 secret_fixture_token', flush=True)",
                ], stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
                self.assertEqual(proc.wait(timeout=5), 0)
            evidence = publish_log(raw, output, token="secret_fixture_token",
                                   process_exited=proc.poll() is not None)
            self.assertTrue(evidence["capture_complete"])
            self.assertEqual(evidence["sanitized_sha256"],
                             hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertIn("bestmove e2e4", output.read_text())
            self.assertNotIn("secret_fixture_token", output.read_text())

    def test_late_diagnostics_are_written_before_shutdown(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            raw = root / "raw.log"
            with raw.open("wb") as f:
                proc = subprocess.Popen([
                    sys.executable, "-c",
                    "import time; print('first', flush=True); time.sleep(0.1);"
                    "print('bestmove g1f3', flush=True)",
                ], stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
                self.assertEqual(proc.wait(timeout=5), 0)
            result = publish_log(raw, root / "bridge.log",
                                 token="fixture", process_exited=True)
            self.assertTrue(result["capture_complete"])
            self.assertIn("first", (root / "bridge.log").read_text())
            self.assertIn("bestmove g1f3", (root / "bridge.log").read_text())

    def test_forced_cleanup_of_sigint_ignoring_bridge(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            raw = root / "raw.log"
            with raw.open("wb") as f:
                proc = subprocess.Popen([
                    sys.executable, "-c",
                    "import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN);"
                    "print('ready', flush=True); time.sleep(60)",
                ], stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
                deadline = time.monotonic() + 4
                while b"ready" not in raw.read_bytes() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertIn(b"ready", raw.read_bytes())
                code = shutdown_bridge(proc, graceful_s=0.1, forced_s=3)
                self.assertEqual(code, -signal.SIGKILL)
                self.assertIsNotNone(proc.poll())
            result = publish_log(raw, root / "bridge.log",
                                 token="fixture", process_exited=True)
            self.assertTrue(result["bridge_process_exited"])

    def test_unfinished_or_oversized_log_never_qualifies(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            raw, out = root / "raw", root / "public"
            raw.write_bytes(b"uci bestmove e2e4")
            with self.assertRaisesRegex(RuntimeError, "before bridge exit"):
                publish_log(raw, out, token="fixture", process_exited=False)
            self.assertFalse(out.exists())
            with patch("deploy.lichess.log_capture.MAX_LOG_BYTES", 4):
                with self.assertRaisesRegex(RuntimeError, "bounded size"):
                    publish_log(raw, out, token="fixture", process_exited=True)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
