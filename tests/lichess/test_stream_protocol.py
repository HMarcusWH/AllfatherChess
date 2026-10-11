"""Real HTTP NDJSON stream and independent legal-history fixtures."""
import json
import threading
import unittest
from pathlib import Path
import chess
import requests
from tests.lichess.fake_lichess_api import FakeLichess, GAME_ID, TOKEN

ROOT = Path(__file__).resolve().parents[2]

class StreamContracts(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLichess("white")
        self.fake.start()
        self.headers = {"Authorization": "Bearer " + TOKEN}
    def tearDown(self):
        self.fake.close()
    def test_chunked_streams_and_gamefull_initial(self):
        with requests.get(self.fake.url + "api/stream/event",
                          headers=self.headers, stream=True, timeout=8) as response:
            self.assertEqual(response.status_code, 200)
            self.fake.issue_challenge()
            chunks = (line for line in response.iter_lines(chunk_size=1) if line)
            event = json.loads(next(chunks))
            self.assertEqual(event["type"], "challenge")
            self.assertEqual(event["challenge"]["id"], GAME_ID)
        self.assertEqual(
            requests.post(self.fake.url + f"api/challenge/{GAME_ID}/accept",
                          headers=self.headers, timeout=5).status_code, 200)
        with requests.get(self.fake.url + f"api/bot/game/stream/{GAME_ID}",
                          headers=self.headers, stream=True, timeout=8) as response:
            initial = json.loads(next(line for line in response.iter_lines(chunk_size=1) if line))
            self.assertEqual(initial["type"], "gameFull")
            self.assertEqual(initial["initialFen"], "startpos")
            self.assertEqual(initial["state"]["moves"], "")

    def test_all_special_history_fixtures_are_legal_from_startpos(self):
        examples = json.loads((ROOT / "tests/fixtures/lichess/legal_histories.json").read_text())
        self.assertEqual(set(examples),
                         {"startpos_castle", "startpos_en_passant", "startpos_double_promotion"})
        for name, history in examples.items():
            with self.subTest(name=name):
                board = chess.Board()
                for uci in history:
                    move = chess.Move.from_uci(uci)
                    self.assertIn(move, board.legal_moves)
                    board.push(move)
                self.assertEqual(len(board.move_stack), len(history))

if __name__ == "__main__":
    unittest.main()
