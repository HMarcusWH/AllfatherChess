"""Cross-generation regressions using the repository's existing protocol fakes.

These are NOT chess-strength or real rule-transition evidence. Real legality is
checked by the independent PGN verifier and explicit rules probes in the campaign.
"""
import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.controller.online_helpers import shell_fixture, wait_for
from controller.replay import discover_replay_bundles, load_manifest, verify_bundle_integrity
from tests.harness.uci_session import UciSession
from tools.local_game.common import process_identity
from tools.local_game.proxy import _spawn_registered_child
from tools.local_game.runner import bounded


def terminals(output):
    return [line for line in output.getvalue().splitlines() if line.startswith("bestmove ")]


class GenerationLifecycleTests(unittest.TestCase):
    def test_repeated_finalized_protocol_games_keep_generations_unique(self):
        with shell_fixture(args={"stockfish-anchor": ["--info-lines", "3", "--info-delay-ms", "20"]}) as (shell, manager, shadow, out, tmp):
            expected_manifests = 0
            for game in range(3):
                shell.handle_command("ucinewgame")
                for turn in range(4):
                    count = len(terminals(out))
                    shell.handle_command("position startpos")
                    shell.handle_command("go movetime 500")
                    wait_for(lambda: len(terminals(out)) == count + 1)
                    shell.handle_command("isready")
                    expected_manifests += 1
                    # This regression explicitly tests *finalized* run reuse.
                    # Wait for the replay barrier before admitting the next go;
                    # otherwise the coordinator correctly declines a second
                    # shadow run while replay-only work from the prior
                    # generation is still draining.
                    wait_for(
                        lambda: len(list(tmp.glob("replays/*/manifest.json")))
                        == expected_manifests,
                        timeout=5,
                    )
                    wait_for(lambda: shadow._run is None or shadow._run.finished.is_set())
            self.assertEqual(len(list(tmp.glob("replays/*/manifest.json"))), 12)
            bundles = discover_replay_bundles(tmp / "replays")
            self.assertFalse(bundles.skipped)
            generations = [load_manifest(p)["generation"] for p in bundles.bundles]
            self.assertEqual(len(generations), len(set(generations)))
            self.assertEqual(terminals(out), ["bestmove e2e4"] * 12)
            for run in bundles.bundles:
                self.assertFalse(verify_bundle_integrity(run), run)

    def test_normal_runner_exit_cleans_detached_child_but_retains_failure_evidence(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as tmp:
            cwd = Path(tmp)
            script = (
                "import json,pathlib,subprocess; "
                "child=subprocess.Popen(['sleep','60'],start_new_session=True); "
                "session=pathlib.Path('sessions')/'x'/'y'; session.mkdir(parents=True); "
                "(session/'session.json').write_text(json.dumps({'pgid':child.pid}),encoding='utf-8')"
            )
            result = bounded([sys.executable, "-c", script], cwd, cwd / "runner.log", 10)
            self.assertEqual(result["returncode"], 0)
            self.assertFalse(result["timed_out"])
            self.assertTrue(result["detached_groups_before_cleanup"])
            self.assertFalse(result["detached_groups_after_cleanup"])
            for pgid in result["detached_groups_before_cleanup"]:
                with self.assertRaises(ProcessLookupError):
                    os.killpg(pgid, 0)

    def test_later_shadow_crash_does_not_elect_new_authority(self):
        args = {"stockfish-anchor": ["--info-lines", "5", "--info-delay-ms", "20"],
                "reckless-shadow": ["--exit-on-go-number", "2"]}
        with shell_fixture(args=args) as (shell, manager, shadow, out, tmp):
            for turn in range(3):
                shell.handle_command("position startpos")
                shell.handle_command("go movetime 500")
                wait_for(lambda: len(terminals(out)) == turn + 1)
                shell.handle_command("isready")
            self.assertEqual(terminals(out), ["bestmove e2e4"] * 3)
            self.assertFalse(manager.shadow_available("reckless-shadow"))

    def test_storage_failure_remains_missing_evidence_not_a_valid_bundle(self):
        import controller.replay as replay
        original = replay.atomic_write_text
        failed = threading.Event()
        def fail_manifest(path, content):
            if Path(path).name == "manifest.json":
                failed.set()
                raise OSError("LOCAL-1 injected storage failure")
            return original(path, content)
        with shell_fixture() as (shell, manager, shadow, out, tmp):
            with patch("controller.replay.atomic_write_text", side_effect=fail_manifest):
                shell.handle_command("go movetime 500")
                wait_for(lambda: len(terminals(out)) == 1)
                self.assertTrue(failed.wait(3))
                discovery = discover_replay_bundles(tmp / "replays")
                self.assertTrue(discovery.skipped)
                self.assertFalse(discovery.bundles)
                self.assertEqual(terminals(out), ["bestmove e2e4"])


if __name__ == "__main__":
    unittest.main()
