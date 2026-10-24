"""ONLINE-PLAY-1 live-path recovery fault gates exercising the existing journal.

No synthetic HTTP 200 can manufacture confirmation after an ambiguous POST.
The authoritative history, not the client retry policy, releases UNKNOWN_ACK.
"""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from deploy.lichess.move_journal import MoveJournal,JournalError,MoveUnresolved

GAME="off3b001"
EPOCH1="1"*32
EPOCH2="2"*32

class DurableOnlineCanaryRecovery(unittest.TestCase):
    def _ready(self,root):
        journal=MoveJournal(Path(root)/"intent.sqlite3")
        journal.initialize()
        journal.activate_owner(EPOCH1)
        journal.observe(EPOCH1,GAME,{"moves":"","status":"started"})
        journal.prepare(EPOCH1,GAME,(),"e2e4")
        journal.change_status(EPOCH1,GAME,"e2e4",before=(),
                              after="SUBMITTING",allowed=("PREPARED",))
        return journal
    def test_unknown_ack_never_replays_until_matching_server_history(self):
        with tempfile.TemporaryDirectory() as t:
            journal=self._ready(t)
            journal.change_status(EPOCH1,GAME,"e2e4",before=(),
                                  after="UNKNOWN_ACK",allowed=("SUBMITTING",))
            journal.activate_owner(EPOCH2)
            self.assertEqual(journal.observe(
                EPOCH2,GAME,{"moves":"","status":"started"}),"UNKNOWN_ACK")
            with self.assertRaises(MoveUnresolved):
                journal.assert_ready(EPOCH2,GAME,())
            with self.assertRaises(MoveUnresolved):
                journal.prepare(EPOCH2,GAME,(),"e2e4")
            self.assertEqual(journal.observe(
                EPOCH2,GAME,{"moves":"e2e4","status":"started"}),"SERVER_CONFIRMED")
            rows=journal.snapshot()["intents"]
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["status"],"CONFIRMED")
            # A second same-ply attempt remains forbidden after recovery.
            with self.assertRaises(MoveUnresolved):
                journal.prepare(EPOCH2,GAME,(),"e2e4")
    def test_old_owner_epoch_fenced_after_new_lease(self):
        with tempfile.TemporaryDirectory() as t:
            journal=self._ready(t)
            journal.activate_owner(EPOCH2)
            with self.assertRaises(JournalError):
                journal.change_status(EPOCH1,GAME,"e2e4",before=(),
                                      after="ACK_OBSERVED",allowed=("SUBMITTING",))
    def test_http429_blocks_account_even_after_owner_restart(self):
        with tempfile.TemporaryDirectory() as t:
            journal=self._ready(t)
            journal.establish_cooldown(EPOCH1,73,cause="synthetic-http429")
            journal.activate_owner(EPOCH2)
            self.assertGreater(journal.cooldown_left(EPOCH2),60)
            with self.assertRaises(MoveUnresolved):
                journal.assert_ready(EPOCH2,GAME,())
    def test_conflicting_authoritative_history_suspends(self):
        with tempfile.TemporaryDirectory() as t:
            journal=self._ready(t)
            journal.change_status(EPOCH1,GAME,"e2e4",before=(),
                                  after="UNKNOWN_ACK",allowed=("SUBMITTING",))
            journal.observe(EPOCH1,GAME,{"moves":"e2e4","status":"started"})
            with self.assertRaises(JournalError):
                journal.observe(EPOCH1,GAME,{"moves":"d2d4","status":"started"})
    def test_linked_or_missing_durable_journal_is_denied(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);target=root/"real.sqlite3"
            target.touch()
            link=root/"linked.sqlite3";link.symlink_to(target)
            with self.assertRaises(JournalError):
                MoveJournal(link)
            other=MoveJournal(root/"missing.sqlite3")
            with self.assertRaises(JournalError):
                other.snapshot()

if __name__=="__main__":
    unittest.main()
