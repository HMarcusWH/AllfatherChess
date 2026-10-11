"""Independent OFFLINE-4B evidence checks: actual SQLite bytes, not producer flags."""
from __future__ import annotations
import copy
import importlib.util
import tempfile
import unittest
from pathlib import Path

from deploy.lichess.session_owner import SessionOwner

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "online4b_qualifier", ROOT / "scripts/qualify-online-recovery-v2.py")
qual = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qual)


class RecoveryV2EvidenceTests(unittest.TestCase):
    def test_persisted_journal_verifies_and_mutation_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            with SessionOwner(folder / "state") as owner:
                owner.journal.observe(owner.epoch, "off3b001",
                                      {"moves": "", "status": "started"})
                owner.journal.prepare(owner.epoch, "off3b001", (), "e2e4")
                record = owner.journal.snapshot()
            qual.verify_persisted_sqlite(folder, record)
            altered = copy.deepcopy(record)
            altered["intents"][0]["status"] = "CONFIRMED"
            with self.assertRaisesRegex(ValueError, "differs from runner"):
                qual.verify_persisted_sqlite(folder, altered)
            altered = copy.deepcopy(record)
            altered["owner_epoch"] = "fake"
            with self.assertRaisesRegex(ValueError, "owner differs"):
                qual.verify_persisted_sqlite(folder, altered)

    def test_absent_persisted_database_never_qualifies(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "absent"):
                qual.verify_persisted_sqlite(Path(d), {"owner_epoch": "fake"})

    def test_r13_uses_real_process_exit_not_an_assumed_success(self):
        valid = {"observation": "injected_bridge_kill", "bridge_exit": -9}
        self.assertEqual(qual.r13_witness([valid])["bridge_exit"], -9)
        for forged in (
            {"observation": "injected_bridge_kill", "bridge_exit": None},
            {"observation": "injected_bridge_kill", "bridge_exit": 0},
            {"observation": "not_killed", "bridge_exit": -9},
            {},
        ):
            with self.subTest(forged=forged), self.assertRaises(ValueError):
                qual.r13_witness([forged])

    def test_normal_completion_is_never_called_suspension(self):
        got = qual.classify_disposition(
            scenario_id="R00", game_complete=True, recovered=False,
            explicit_suspension=False, restart_attempted=False,
        )
        self.assertEqual(got, "NORMAL_COMPLETION")
        self.assertEqual(qual.classify_disposition(
            scenario_id="R00", game_complete=False, recovered=False,
            explicit_suspension=False, restart_attempted=False,
        ), "INCONCLUSIVE")
        self.assertEqual(qual.classify_disposition(
            scenario_id="R02", game_complete=True, recovered=False,
            explicit_suspension=False, restart_attempted=True,
        ), "INCONCLUSIVE")
        self.assertEqual(qual.classify_disposition(
            scenario_id="R10", game_complete=False, recovered=False,
            explicit_suspension=True, restart_attempted=False,
        ), "SAFE_SUSPENSION")
        self.assertEqual(qual.classify_disposition(
            scenario_id="R08", game_complete=False, recovered=False,
            explicit_suspension=False, restart_attempted=True,
        ), "INCONCLUSIVE")

    def test_valid_diagnostic_claims_do_not_authorize_online_release(self):
        value = qual.read(qual.POLICY)
        self.assertEqual(value["scope"], "offline-guarded-bridge-only")
        self.assertFalse(value["network_recovery_qualified"])
        self.assertFalse(value["public_bot_release"])
        self.assertFalse(value["deployment_host_qualified"])


if __name__ == "__main__":
    unittest.main()
