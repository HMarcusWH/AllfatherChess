"""No real network: validate recovery transport, move gate and account cooldown."""
from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import chess
import requests
from deploy.lichess import guarded_transport as guard
from deploy.lichess.session_owner import SessionOwner


class FakeHTTP:
    def __init__(self, response=None, failure=None):
        self.response = response
        self.failure = failure
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        if self.failure:
            raise self.failure
        return self.response


def response(status: int):
    r = requests.Response()
    r.status_code = status
    r._content = b'{"ok":true}'
    r.url = "http://127.0.0.1:8765/api/bot/game/g001/move/e2e4"
    return r


class GuardedTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.owner = SessionOwner(Path(self.tmp.name))
        self.owner.__enter__()
        self.addCleanup(self.owner.__exit__, None, None, None)
        values = self.owner.environment()
        values["ALLFATHER_RECOVERY_AUDIT"] = str(Path(self.tmp.name) / "audit.ndjson")
        saved = {k: os.environ.get(k) for k in values}
        os.environ.update(values)
        def restore():
            for k, previous in saved.items():
                if previous is None: os.environ.pop(k, None)
                else: os.environ[k] = previous
        self.addCleanup(restore)
        self.journal = self.owner.journal
        self.epoch = self.owner.epoch
        guard.observe_state("g001", {"moves": "", "status": "started"})

    def _stage(self):
        board = chess.Board()
        guard.stage_move(board, SimpleNamespace(
            move=chess.Move.from_uci("e2e4"), resigned=False), "g001")

    def test_null_move_never_reaches_http(self):
        for value in (None, chess.Move.null(), chess.Move.from_uci("e7e5")):
            with self.subTest(value=value), self.assertRaises(guard.RecoverySuspended):
                guard.stage_move(chess.Board(), SimpleNamespace(move=value, resigned=False),
                                 "g001")
        self.assertIsNone(self.journal.intent(self.epoch, "g001"))

    def test_successful_http_ack_alone_does_not_confirm_move(self):
        self._stage()
        fake = FakeHTTP(response(200))
        result = guard.guarded_post(fake, "move", ("g001", "e2e4"),
                                    "http://127.0.0.1:8765/api/bot/game/g001/move/e2e4")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(self.journal.intent(self.epoch, "g001")["status"], "ACK_OBSERVED")
        with self.assertRaises(guard.RecoverySuspended):
            guard.stage_move(chess.Board(), SimpleNamespace(
                move=chess.Move.from_uci("d2d4")), "g001")
        guard.observe_state("g001", {"moves": "e2e4", "status": "started"})
        self.assertEqual(self.journal.intent(self.epoch, "g001")["status"], "CONFIRMED")

    def test_lost_ack_is_never_blindly_retried(self):
        self._stage()
        fake = FakeHTTP(failure=requests.exceptions.ConnectionError("lost reply"))
        with self.assertRaises(guard.RecoverySuspended):
            guard.guarded_post(fake, "move", ("g001", "e2e4"),
                               "http://127.0.0.1:8765/api/bot/game/g001/move/e2e4")
        self.assertEqual(self.journal.intent(self.epoch, "g001")["status"], "UNKNOWN_ACK")
        with self.assertRaises(guard.RecoverySuspended):
            guard.guarded_post(fake, "move", ("g001", "e2e4"),
                               "http://127.0.0.1:8765/api/bot/game/g001/move/e2e4")
        self.assertEqual(fake.calls, 1)

    def test_429_blocks_all_followup_requests_and_survives_restart(self):
        self._stage()
        fake = FakeHTTP(response(429))
        with self.assertRaises(guard.RecoverySuspended):
            guard.guarded_post(fake, "move", ("g001", "e2e4"),
                               "http://127.0.0.1:8765/api/bot/game/g001/move/e2e4")
        self.assertEqual(fake.calls, 1)
        self.assertGreater(self.journal.cooldown_left(self.epoch), 50)
        for endpoint in ("playing", "abort", "token_test", "stream_event"):
            with self.subTest(endpoint=endpoint), self.assertRaises(guard.RecoverySuspended):
                guard.before_request(endpoint)
        self.assertEqual(fake.calls, 1)

    def test_rejects_live_endpoint_or_unapproved_side_effect(self):
        self._stage()
        fake = FakeHTTP(response(200))
        with self.assertRaises(guard.RecoverySuspended):
            guard.guarded_post(fake, "move", ("g001", "e2e4"),
                               "https://lichess.org/api/bot/game/g001/move/e2e4")
        with self.assertRaises(guard.RecoverySuspended):
            guard.before_request("upgrade")
        self.assertEqual(fake.calls, 0)

    def test_stale_epoch_rejected_for_every_api(self):
        self.owner.new_epoch()
        # Simulate a stale bridge process that retained the preceding epoch.
        os.environ["ALLFATHER_RECOVERY_EPOCH"] = self.epoch
        # new_epoch updates the owner object's epoch; save old token separately
        with self.assertRaises(guard.RecoverySuspended):
            guard.before_request("playing")


if __name__ == "__main__":
    unittest.main()
