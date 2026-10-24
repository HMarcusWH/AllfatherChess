"""Live fork only derives from pinned offline defaults; never engine selection."""
import json
import os
import re
import unittest
from pathlib import Path
from deploy.lichess.live_gateway_policy import (
    GATEWAY_URL,LICHESS_ORIGIN,SYNTHETIC_TOKEN)

ROOT=Path(__file__).resolve().parents[2]

class LiveBridgeContract(unittest.TestCase):
    def test_endpoint_origin_and_bridge_nonsecret(self):
        self.assertEqual(LICHESS_ORIGIN,"https://lichess.org")
        self.assertEqual(GATEWAY_URL,"http://live-gateway:8765/")
        self.assertEqual(SYNTHETIC_TOKEN,"offline-fixture-token")
    def test_controller_and_network_unchanged(self):
        policy=json.loads((ROOT/"qualification/online-live-canary-v1.json").read_text())
        for key in ("public_bot_release","live_game_authorized",
                    "operator_armed","outbound_challenges_allowed",
                    "automatic_matchmaking_allowed",
                    "external_move_sources_allowed","move_retries_allowed"):
            self.assertIs(policy[key],False,key)
        self.assertIsNone(policy["bot_account_username"])
        self.assertIsNone(policy["opponent_username"])
        self.assertEqual((policy["clock_seconds"],policy["increment_seconds"]),(600,5))
    def test_recovery_guard_offline_default_still_present(self):
        guard=(ROOT/"deploy/lichess/guarded_transport.py").read_text()
        self.assertIn('os.getenv("ALLFATHER_TRANSPORT_PROFILE", "offline")',guard)
        self.assertIn('parsed.hostname != "127.0.0.1"',guard)
        self.assertIn('journal.change_status',guard)
        self.assertIn('"UNKNOWN_ACK"',guard)
        self.assertIn('journal.establish_cooldown',guard)
    def test_compose_bridge_effective_memory_is_6144_mebibytes(self):
        # Docker's 6144m is decimal megabytes, NOT 6144 MiB.
        # Use 6g (= 6*1024^3 bytes) for the required minimum.
        import yaml
        for name in ("compose.test.yml", "compose.host.example.yml"):
            with self.subTest(name=name):
                config=yaml.safe_load((ROOT/"deploy/live_canary"/name).read_text())
                bridge=(config["services"].get("live-engine-bridge")
                        or config["services"].get("bridge"))
                self.assertIsNotNone(bridge)
                self.assertEqual(bridge["mem_limit"],"6g")
                self.assertGreaterEqual(int(bridge["cpus"]),4)
                self.assertTrue(config["networks"]["canary_private"]["internal"])
                # A YAML flow list with unquoted commas erroneously treats
                # "nodev" and "nosuid" as separate mount paths in Docker.
                self.assertEqual(len(bridge["tmpfs"]),2)
                self.assertEqual(bridge["tmpfs"][0],
                                 "/tmp:rw,noexec,nosuid,nodev,size=1073741824,mode=1777")
                self.assertEqual(bridge["tmpfs"][1],
                                 "/run/allfather-canary-stage:rw,exec,nosuid,nodev,size=1073741824,mode=0700,uid=10001,gid=10001")

    def test_source_sealed_live_guards_are_staged_without_changing_upstream(self):
        import hashlib,tempfile
        from deploy.lichess.live_session import _stage_live_guards
        with tempfile.TemporaryDirectory() as folder:
            destination=Path(folder)/"bridge"
            modules=destination/"deploy/lichess"
            modules.mkdir(parents=True)
            guard=(modules/"guarded_transport.py")
            guard.write_bytes(b"old verified offline recovery adapter\n")
            before=hashlib.sha256(guard.read_bytes()).hexdigest()
            record={"guard_modules":{"guarded_transport.py":before},
                    "patched_upstream":{}}
            _stage_live_guards(destination,record)
            for name in ("guarded_transport.py","live_gateway_policy.py"):
                self.assertEqual(
                    (modules/name).read_bytes(),
                    (ROOT/"deploy/lichess"/name).read_bytes())
            self.assertEqual(record["live_profile"],"source-locked-live-canary-v1")
            self.assertEqual(record["guard_modules"]["guarded_transport.py"],
                             hashlib.sha256(guard.read_bytes()).hexdigest())
            self.assertEqual(
                json.loads((destination/"recovery-overlay-manifest.json").read_text()),
                record)

    def test_direct_dry_run_script_imports_exact_repository(self):
        # The GitHub Actions command runs the file directly, not "-m".
        # Isolated Python proves the helper inserts its own checkout root.
        import subprocess,sys
        script=ROOT/"scripts/run-live-canary-dry-run.py"
        payload=(
            "import runpy; runpy.run_path("+repr(str(script))+
            ",run_name='safe-import-probe'); "
            "from deploy.live_canary.admission import SourceBinding; "
            "assert SourceBinding.__module__=='deploy.live_canary.admission'"
        )
        proc=subprocess.run([sys.executable,"-I","-c",payload],
                            cwd="/tmp",capture_output=True,text=True,timeout=15)
        self.assertEqual(proc.returncode,0,proc.stderr[:500])

    def test_git_safe_directory_is_exact_and_not_global(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[2]
        compose=(root/"deploy/live_canary/compose.test.yml").read_text()
        host=(root/"deploy/live_canary/compose.host.example.yml").read_text()
        for name,content in (("test",compose),("host",host)):
            with self.subTest(mode=name):
                self.assertIn("GIT_CONFIG_VALUE_0: /work",content)
                self.assertIn("GIT_CONFIG_VALUE_1: /work/build/vendor/lichess-bot",content)
                self.assertNotIn("GIT_CONFIG_VALUE_0: '*'",content)
                self.assertNotIn("GIT_CONFIG_VALUE_1: '*'",content)

    def test_pinned_exact_source_not_mutated(self):
        lock=json.loads((ROOT/"deploy/lichess/bridge.lock.json").read_text())
        self.assertEqual(lock["commit"],"df7e730de58cc3ef2f1415a0dc2eeda842d39167")
        self.assertIs(lock["live_api_permitted"],False)
    def test_no_production_test_origin_override(self):
        source=(ROOT/"deploy/live_canary/entrypoint.py").read_text()
        self.assertNotIn("for_fake_test_only",source)
        self.assertNotIn("fake_lichess_api",source)
        self.assertIn('choices=("verify-only","gateway","bridge")',source)


    def test_synthetic_bridge_trace_hints_cannot_leak_exception_values(self):
        import tempfile
        from tests.lichess.live_canary_fake_bridge import safe_child_trace_hint
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/"bridge-private.log"
            file.write_text(
                'Traceback (most recent call last):\n'
                '  File "/tmp/allfather-live-bridge/bridge/lib/config.py", line 312, in validate_config\n'
                'Exception: Bearer secret-upstream-should-never-be-shared\n')
            hint=safe_child_trace_hint(file)
            self.assertEqual(hint["exception_module"],"config.py")
            self.assertEqual(hint["exception_line"],312)
            self.assertEqual(hint["exception_class"],"Exception")
            self.assertNotIn("secret",str(hint))
            self.assertNotIn("Bearer",str(hint))

    def test_unapproved_traceback_module_is_not_released(self):
        import tempfile
        from tests.lichess.live_canary_fake_bridge import safe_child_trace_hint
        with tempfile.TemporaryDirectory() as tmp:
            private=Path(tmp)/"bridge-private.log"
            private.write_text(
                'Traceback (most recent call last):\n'
                '  File "/tmp/unapproved/credential_reader.py", line 91, in steal\n'
                'RuntimeError: Bearer synthetic-sensitive-value\n')
            hint=safe_child_trace_hint(private)
            self.assertIsNone(hint["exception_module"])
            self.assertIsNone(hint["exception_line"])
            self.assertEqual(hint["exception_class"],"RuntimeError")
            self.assertNotIn("Bearer",str(hint))
            self.assertNotIn("synthetic-sensitive-value",str(hint))


if __name__=="__main__":unittest.main()
