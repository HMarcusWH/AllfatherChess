"""The pilot must never let Fastchess write source files in the checkout."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.g3_pilot_health import source_hygiene, require_clean_hygiene
from tools.local_game.common import QualificationError
from tools.local_game.runner import bounded
from tools import g3_pilot as pilot


class PilotWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root/".gitignore").write_text("build/\n")
        (self.root/"tracked.txt").write_text("source content\n")
        subprocess.run(["git", "-C", str(self.root), "add",
                        ".gitignore", "tracked.txt"], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_subprocess_config_lives_in_match_dir_not_checkout_root(self):
        folder = self.root/"build/pilot/pair-00"
        folder.mkdir(parents=True)
        command = [sys.executable, "-c",
                   "from pathlib import Path; Path('config.json').write_text('fixture')"]
        observed = bounded(command, folder, folder/"runner.log", 10)
        self.assertEqual(observed["returncode"], 0)
        self.assertFalse(observed["timed_out"])
        self.assertFalse(observed["descendants_before_cleanup"])
        self.assertFalse(observed["descendants_after_cleanup"])
        self.assertTrue((folder/"config.json").is_file())
        self.assertFalse((self.root/"config.json").exists())
        require_clean_hygiene(source_hygiene(self.root))

    def test_untracked_root_config_is_hashed_not_excused_or_uploaded(self):
        secret = self.root/"config.json"
        secret.write_text('DO_NOT_LOG_SOURCE_CONFIG_CONTENTS')
        snapshot = source_hygiene(self.root)
        self.assertEqual(snapshot["unexpected_count"], 1)
        self.assertEqual(snapshot["entries"][0]["path"], "config.json")
        self.assertEqual(snapshot["entries"][0]["status"], "bounded_regular_hash_only")
        self.assertEqual(len(snapshot["entries"][0]["sha256"]), 64)
        self.assertNotIn("DO_NOT_LOG", repr(snapshot))
        self.assertFalse(snapshot["raw_contents_retained"])
        with self.assertRaisesRegex(QualificationError, "untracked"):
            require_clean_hygiene(snapshot)
        self.assertTrue(secret.is_file(), "repair may not silently delete source evidence")

    def test_generated_command_uses_absolute_match_paths(self):
        policy = pilot.pilot_policy()
        plan = pilot.planned_games(policy)[0][0]
        folder = pilot.ROOT / "build/test-results/g3-pilot/synthetic-pair-00"
        opening = folder / plan["opening"]
        fastchess = pilot.ROOT / "build/tools/fastchess/bin/fastchess"
        runtime = pilot.load(pilot.ROOT / policy["source_runtime"])
        command = pilot.frozen_command(
            plan, folder, policy, runtime, fastchess, opening,
            write_specs=False,
        )
        self.assertTrue(folder.is_absolute())
        self.assertTrue(any(arg == f"file={folder/'games.pgn'}"
                            for arg in command))
        self.assertTrue(any(arg == f"file={opening}" for arg in command))
        self.assertTrue(any(arg == f"file={folder/'runner.log'}"
                            for arg in command))
        self.assertTrue(any(arg.startswith("args=-m tools.local_game.proxy --spec ")
                            and str(folder) in arg for arg in command))
        self.assertNotIn("config.json", str(command))


if __name__ == "__main__":
    unittest.main()
