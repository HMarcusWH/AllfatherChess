"""Crash-durable move journal and canonical server-history negative tests."""
from __future__ import annotations
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from deploy.lichess.reconcile import (
    ReconciliationError, canonical_history, classify_snapshot, position_id,
)
from deploy.lichess.move_journal import MoveJournal, JournalError, MoveUnresolved, SessionFenced
from deploy.lichess.session_owner import SessionOwner, OwnerError


class DurableRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.owner = SessionOwner(self.root / "state")
        self.owner.__enter__()
        self.addCleanup(self.owner.__exit__, None, None, None)
        self.journal = self.owner.journal
        self.epoch = self.owner.epoch
        self.journal.observe(self.epoch, "game001", {"moves": "", "status": "started"})

    def test_move_prepared_sent_and_confirmed_by_authoritative_history(self):
        self.assertEqual(self.journal.prepare(self.epoch, "game001", (), "e2e4"),
                         position_id(()))
        self.journal.change_status(self.epoch, "game001", "e2e4",
                                   before=(), after="SUBMITTING", allowed=("PREPARED",))
        self.journal.change_status(self.epoch, "game001", "e2e4",
                                   before=(), after="ACK_OBSERVED", allowed=("SUBMITTING",))
        self.assertEqual(self.journal.observe(self.epoch, "game001",
                                              {"moves": "", "status": "started"}), "UNKNOWN_ACK")
        with self.assertRaises(MoveUnresolved):
            self.journal.assert_ready(self.epoch, "game001", ())
        self.assertEqual(self.journal.observe(self.epoch, "game001",
                                              {"moves": "e2e4", "status": "started"}),
                         "SERVER_CONFIRMED")
        self.assertEqual(self.journal.intent(self.epoch, "game001")["status"], "CONFIRMED")
        self.journal.assert_ready(self.epoch, "game001", ("e2e4",))
        self.assertEqual(self.journal.prepare(self.epoch, "game001", ("e2e4",), "e7e5"),
                         position_id(("e2e4",)))

    def test_crash_after_submission_persists_unresolved_intent(self):
        self.journal.prepare(self.epoch, "game001", (), "e2e4")
        self.journal.change_status(self.epoch, "game001", "e2e4",
                                   before=(), after="SUBMITTING", allowed=("PREPARED",))
        fresh = MoveJournal(self.journal.path)
        self.assertEqual(fresh.intent(self.epoch, "game001")["status"], "SUBMITTING")
        with self.assertRaises(MoveUnresolved):
            fresh.change_status(self.epoch, "game001", "e2e4",
                                before=(), after="SUBMITTING", allowed=("PREPARED",))
        with self.assertRaises(MoveUnresolved):
            fresh.prepare(self.epoch, "game001", (), "e2e4")
        self.assertEqual(fresh.observe(self.epoch, "game001",
                                       {"moves": "e2e4 f7f6", "status": "started"}),
                         "SERVER_CONFIRMED")

    def test_ambiguous_snapshot_never_proves_safe_retry(self):
        self.journal.prepare(self.epoch, "game001", (), "e2e4")
        self.journal.change_status(self.epoch, "game001", "e2e4",
                                   before=(), after="UNKNOWN_ACK", allowed=("PREPARED",))
        self.assertEqual(self.journal.observe(self.epoch, "game001",
                                              {"moves": "", "status": "started"}), "UNKNOWN_ACK")
        with self.assertRaises(MoveUnresolved):
            self.journal.prepare(self.epoch, "game001", (), "d2d4")
        with self.assertRaises(JournalError):
            self.journal.observe(self.epoch, "game001",
                                 {"moves": "d2d4", "status": "started"})

    def test_stale_or_illegal_state_is_rejected(self):
        self.journal.observe(self.epoch, "game001",
                             {"moves": "e2e4 e7e5", "status": "started"})
        for moves in ("e2e4", "d2d4", "e2e5", "0000", "e2e4 e7e5 e7e6"):
            with self.subTest(moves=moves), self.assertRaises(JournalError):
                self.journal.observe(self.epoch, "game001",
                                     {"moves": moves, "status": "started"})

    def test_cooldown_is_account_wide_and_survives_new_owner_epoch(self):
        self.journal.establish_cooldown(self.epoch, 61, cause="429", now=1000)
        self.assertEqual(self.journal.cooldown_left(self.epoch, now=1020), 41)
        second = "e" * 32
        self.journal.activate_owner(second)
        with self.assertRaises(SessionFenced):
            self.journal.cooldown_left(self.epoch, now=1020)
        self.assertEqual(self.journal.cooldown_left(second, now=1020), 41)

    def test_conflicting_session_cannot_hold_owner_lock(self):
        with self.assertRaises(OwnerError):
            with SessionOwner(self.root / "state"):
                pass

    def test_source_position_identity_matches_full_history(self):
        self.assertEqual(position_id(["e2e4", "e7e5"]),
                         position_id("e2e4 e7e5"))
        self.assertNotEqual(position_id("e2e4"), position_id(""))
        with self.assertRaises(ReconciliationError):
            canonical_history("e2e5")

    def test_rejected_second_intent_and_terminal_reopen(self):
        self.journal.prepare(self.epoch, "game001", (), "e2e4")
        with self.assertRaises(MoveUnresolved):
            self.journal.prepare(self.epoch, "game001", (), "d2d4")
        self.journal.change_status(self.epoch, "game001", "e2e4",
                                   before=(), after="SUBMITTING", allowed=("PREPARED",))
        self.journal.observe(self.epoch, "game001",
                             {"moves": "e2e4", "status": "mate"})
        with self.assertRaises(JournalError):
            self.journal.observe(self.epoch, "game001",
                                 {"moves": "e2e4", "status": "started"})


if __name__ == "__main__":
    unittest.main()
