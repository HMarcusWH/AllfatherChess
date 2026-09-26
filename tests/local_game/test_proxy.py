"""Real subprocess tests of observer transparency, duplicate detection and cleanup."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.local_game.common import load, save


@unittest.skipUnless(sys.platform == "linux", "procfs reference")
class ProxyTests(unittest.TestCase):
    def exercise(self, *, duplicate=False, leak=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            binary = root / "engine"
            binary.write_text("#!/usr/bin/env python3\nimport sys, subprocess\n" +
                ("subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n" if leak else "") +
                "for raw in sys.stdin:\n"
                " line=raw.strip()\n"
                " if line=='uci': print('uciok', flush=True)\n"
                " elif line=='isready': print('readyok', flush=True)\n"
                " elif line.startswith('go '):\n"
                "  print('bestmove e2e4', flush=True)\n" +
                ("  print('bestmove e2e4', flush=True)\n" if duplicate else "") +
                " elif line=='quit': break\n")
            binary.chmod(0o755)
            save(root / "config/allfather.online-hybrid.validation.json",
                 {"instances": {"stockfish-anchor": {"binary": str(binary)}}})
            spec = root / "spec.json"
            save(spec, {"root": str(root), "arm": "stockfish", "sessions": str(root / "build/sessions")})
            child = subprocess.Popen([sys.executable, "-m", "tools.local_game.proxy", "--spec", str(spec)],
                                     cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     bufsize=0)
            try:
                def send(line):
                    child.stdin.write((line+"\n").encode()); child.stdin.flush()
                def read():
                    ready, _, _ = select.select([child.stdout], [], [], 5)
                    self.assertTrue(ready, "proxy did not return a line")
                    return child.stdout.readline().decode().strip()
                send("uci"); self.assertEqual(read(), "uciok")
                send("ucinewgame"); send("isready"); self.assertEqual(read(), "readyok")
                send("position startpos"); send("go wtime 30000 btime 30000")
                self.assertEqual(read(), "bestmove e2e4")
                if duplicate: self.assertEqual(read(), "bestmove e2e4")
                send("quit")
                child.wait(timeout=15)
                session = next((root / "build/sessions").glob("*/session.json"))
                result = load(session)
                events = [json.loads(x) for x in session.with_name("uci.jsonl").read_text().splitlines()]
                self.assertEqual(len(events), result["event_count"])
                self.assertEqual(sum(e["direction"] == "in" and e["line"].startswith("go ") for e in events), 1)
                self.assertFalse(result["remaining_after_cleanup"])
                self.assertEqual(child.returncode, 2 if (duplicate or leak) else 0)
                return result
            finally:
                if child.poll() is None: child.kill(); child.wait()
                for h in (child.stdin, child.stdout, child.stderr): h.close()

    def test_transparency_and_full_session_cpu(self):
        result = self.exercise()
        self.assertFalse(result["errors"])
        self.assertTrue(result["resources"]["cpu_complete"])
        self.assertGreater(result["resources"]["reaped_subtree_cpu_ms"], 0)

    def test_duplicate_is_not_hidden_from_runner(self):
        result = self.exercise(duplicate=True)
        self.assertIn("unsolicited/duplicate bestmove", result["errors"])

    def test_leak_is_retained_even_after_emergency_cleanup(self):
        result = self.exercise(leak=True)
        self.assertTrue(result["leaked_before_cleanup"])
        self.assertFalse(result["resources"]["cpu_complete"])


if __name__ == "__main__":
    unittest.main()
