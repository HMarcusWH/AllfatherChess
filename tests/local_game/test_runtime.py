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
from tools.local_game.faults import _copy_replays, _wait_replay_finalization
from tools.local_game.proxy import _registered_child_scope, _spawn_registered_child
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
                    # Do not wait for replay sealing here. Natural Fastchess
                    # pacing may submit the next move after the measured engine
                    # boundary but before the prior manifest is sealed.
            wait_for(
                lambda: len(list(tmp.glob("replays/*/manifest.json")))
                == expected_manifests,
                timeout=10,
            )
            self.assertEqual(len(list(tmp.glob("replays/*/manifest.json"))), 12)
            bundles = discover_replay_bundles(tmp / "replays")
            self.assertFalse(bundles.skipped)
            generations = [load_manifest(p)["generation"] for p in bundles.bundles]
            self.assertEqual(len(generations), len(set(generations)))
            self.assertEqual(terminals(out), ["bestmove e2e4"] * 12)
            for run in bundles.bundles:
                self.assertFalse(verify_bundle_integrity(run), run)

    def test_fault_evidence_retention_waits_for_replay_finalization(self):
        entered = threading.Event()
        release = threading.Event()

        with shell_fixture() as (shell, manager, shadow, out, tmp):
            original = manager._observer

            def observe(instance, token, *args):
                if instance == "stockfish-anchor" and token == 1:
                    entered.set()
                    release.wait(5)
                original(instance, token, *args)

            manager.set_instance_observer(observe)
            errors = []
            retained = threading.Event()
            destination = tmp / "retained-replays"

            def retain():
                try:
                    _wait_replay_finalization(shadow, timeout=4)
                    _copy_replays(tmp / "replays", destination)
                except Exception as exc:
                    errors.append(exc)
                finally:
                    retained.set()

            try:
                shell.handle_command("go movetime 500")
                self.assertTrue(entered.wait(1))
                wait_for(lambda: len(terminals(out)) == 1, timeout=1)
                wait_for(lambda: 1 in shadow._finalizing, timeout=3)
                worker = threading.Thread(target=retain, daemon=True)
                worker.start()
                self.assertFalse(
                    retained.wait(0.15),
                    "evidence retention crossed a live replay finalizer",
                )
            finally:
                release.set()

            self.assertTrue(retained.wait(4))
            self.assertEqual(errors, [])
            self.assertTrue(destination.is_dir())
            self.assertEqual(
                len(list(destination.glob("*/manifest.json"))),
                1,
            )

    def test_normal_runner_exit_cleans_same_group_child_without_killing_unrelated(self):
        (ROOT / "build").mkdir(exist_ok=True)
        unrelated = subprocess.Popen(["sleep", "60"], start_new_session=True)
        try:
            with tempfile.TemporaryDirectory(dir=ROOT / "build") as tmp:
                cwd = Path(tmp)
                script = "import subprocess; subprocess.Popen(['sleep','60'])"
                result = bounded(
                    [sys.executable, "-c", script],
                    cwd,
                    cwd / "runner.log",
                    10,
                )
                self.assertEqual(result["returncode"], 0)
                self.assertFalse(result["timed_out"])
                self.assertTrue(result["descendants_before_cleanup"])
                self.assertFalse(result["descendants_after_cleanup"])
                self.assertIsNone(
                    process_identity(result["descendants_before_cleanup"][0]["pid"])
                )
                self.assertIsNone(unrelated.poll(), "token cleanup killed unrelated process")
        finally:
            unrelated.kill()
            unrelated.wait(timeout=5)

    def test_proxy_registration_failure_cleans_child_without_session_evidence(self):
        captured = {}

        def fail_registration(process, pgid, identity):
            captured["identity"] = identity
            raise OSError("LOCAL-1 injected initial session write failure")

        with self.assertRaises(OSError):
            _spawn_registered_child(
                ["sleep", "60"],
                cwd=ROOT,
                environment=os.environ.copy(),
                register=fail_registration,
            )
        self.assertIsNone(process_identity(captured["identity"]["pid"]))

    def test_proxy_post_registration_setup_failure_cleans_child(self):
        captured={}
        def register(process,pgid,identity): captured["identity"]=identity
        with self.assertRaises(OSError):
            with _registered_child_scope(["sleep","60"],cwd=ROOT,environment=os.environ.copy(),register=register):
                raise OSError("LOCAL-1 injected logger/reader initialization failure")
        self.assertIsNone(process_identity(captured["identity"]["pid"]))

    def test_managed_uci_session_cleans_controller_group_and_records_leak(self):
        script = (
            "import subprocess,sys\n"
            "subprocess.Popen(['sleep','60'])\n"
            "for raw in sys.stdin:\n"
            "    line=raw.strip()\n"
            "    if line=='uci':\n"
            "        print('id name managed-fake'); print('uciok'); sys.stdout.flush()\n"
            "    elif line=='isready':\n"
            "        print('readyok'); sys.stdout.flush()\n"
            "    elif line=='quit':\n"
            "        break\n"
        )
        shell = UciSession(
            Path(sys.executable),
            cwd=ROOT,
            timeout=5,
            args=["-c", script],
            start_new_session=True,
        )
        with shell:
            shell.ready()
        self.assertTrue(shell.leaked_before_cleanup)
        self.assertFalse(shell.remaining_after_cleanup)
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
