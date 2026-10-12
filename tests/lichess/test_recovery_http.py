"""Real HTTP requests through the actual simulator fault hooks, without engines."""
import json
import unittest
import requests

from tests.lichess.fake_lichess_api import FakeLichess, GAME_ID, TOKEN
from tests.lichess.fault_plan import load_scenarios
from pathlib import Path

SCENARIOS = Path(__file__).resolve().parents[2] / "tests/fixtures/lichess/recovery_scenarios.json"
HEADERS = {"Authorization": "Bearer " + TOKEN}


class RecoveryHTTPTests(unittest.TestCase):
    def run_fault(self, case, expected_commit, expected_status=None):
        fake = FakeLichess("white", load_scenarios(SCENARIOS)[case])
        fake.start()
        try:
            accept = requests.post(fake.url + f"api/challenge/{GAME_ID}/accept",
                                   headers=HEADERS, timeout=4)
            self.assertEqual(accept.status_code, 200)
            url = fake.url + f"api/bot/game/{GAME_ID}/move/e2e4"
            if expected_status is None:
                with self.assertRaises(requests.exceptions.RequestException):
                    requests.post(url, headers=HEADERS, timeout=4)
            else:
                result = requests.post(url, headers=HEADERS, timeout=4)
                self.assertEqual(result.status_code, expected_status)
            actual = fake.evidence()
            self.assertEqual(len(actual["fault"]["activations"]), 1)
            commits = [x for x in actual["transport_trace"] if x["event"] == "board_commit"
                       and x["origin"] == "allfather"]
            self.assertEqual(len(commits), expected_commit)
            if expected_commit:
                self.assertEqual(commits[0]["uci"], "e2e4")
            return fake, actual
        finally:
            fake.close()

    def test_request_reaches_handler_but_drops_before_commit(self):
        self.run_fault("R01", 0)

    def test_authority_commits_but_http_ack_is_lost(self):
        self.run_fault("R02", 1)

    def test_504_is_not_evidence_that_move_was_uncommitted(self):
        import time
        fake, result = self.run_fault("R03", 1, expected_status=504)
        # requests may receive all response bytes before the handler finishes
        # its post-flush observational trace. Wait for the observation rather
        # than racing the server thread or inventing a client acknowledgment.
        until = time.monotonic() + 2
        while not any(x["event"] == "response_write_completed" and x["status"] == 504
                      for x in result["transport_trace"]) and time.monotonic() < until:
            time.sleep(0.01)
            result = fake.evidence()
        self.assertTrue(any(x["event"] == "response_write_completed" and x["status"] == 504
                            for x in result["transport_trace"]))

    def test_rate_limit_rejects_without_move(self):
        self.run_fault("R10", 0, expected_status=429)

    def test_synthetic_low_clock_is_reflected_in_server_state(self):
        _, result = self.run_fault("R12", 1)
        self.assertEqual(result["clock"]["white"], 1500)
        self.assertTrue(any(row["event"] == "synthetic_clock_override"
                            for row in result["transport_trace"]))


    def test_repeat_move_request_cannot_commit_same_ply_twice(self):
        fake = FakeLichess("white", load_scenarios(SCENARIOS)["R04"])
        fake.start()
        try:
            requests.post(fake.url + f"api/challenge/{GAME_ID}/accept",
                          headers=HEADERS, timeout=4).raise_for_status()
            url = fake.url + f"api/bot/game/{GAME_ID}/move/e2e4"
            self.assertEqual(requests.post(url, headers=HEADERS, timeout=4).status_code, 200)
            self.assertIn(requests.post(url, headers=HEADERS, timeout=4).status_code, (400, 409))
            commits = [x for x in fake.evidence()["transport_trace"]
                       if x["event"] == "board_commit" and x["origin"] == "allfather"]
            self.assertEqual(len(commits), 1)
        finally:
            fake.close()


    def test_replacing_global_stream_fences_old_subscriber(self):
        fake = FakeLichess("white", load_scenarios(SCENARIOS)["R09"])
        fake.start()
        try:
            url = fake.url + "api/stream/event"
            with requests.get(url, headers=HEADERS, stream=True, timeout=4) as first:
                self.assertEqual(first.status_code, 200)
                with requests.get(url, headers=HEADERS, stream=True, timeout=4) as second:
                    self.assertEqual(second.status_code, 200)
                    fake.issue_challenge()
                    row = json.loads(next(x for x in second.iter_lines(chunk_size=1) if x))
                    self.assertEqual(row["type"], "challenge")
                    self.assertEqual(len(fake.evidence()["fault"]["activations"]), 1)
                    ev = [x for x in fake.evidence()["stream_sessions"] if x["event"] == "subscribe"]
                    self.assertEqual(len(ev), 2)
                    self.assertTrue(fake.sessions.next_sid >= 2)
        finally:
            fake.close()

    def test_wrong_game_payload_is_labeled_fault(self):
        import chess
        fake = FakeLichess("white", load_scenarios(SCENARIOS)["R11"])
        fake.start()
        try:
            with requests.get(fake.url + f"api/bot/game/stream/{GAME_ID}",
                              headers=HEADERS, stream=True, timeout=4) as response:
                lines = (json.loads(line) for line in response.iter_lines(chunk_size=1) if line)
                self.assertEqual(next(lines)["type"], "gameFull")
                with fake.lock:
                    fake.apply(chess.Move.from_uci("e2e4"), "allfather")
                state = next(lines)
                self.assertEqual(state["gameId"], "untrusted-other-game")
                self.assertEqual(fake.evidence()["fault"]["activations"][0]["action"],
                                 "wrong_game")
        finally:
            fake.close()


    def test_terminal_event_repetition_is_observable_not_a_second_commit(self):
        import chess
        fake = FakeLichess("white", load_scenarios(SCENARIOS)["R14"])
        fake.start()
        try:
            with requests.get(fake.url + f"api/bot/game/stream/{GAME_ID}",
                              headers=HEADERS, stream=True, timeout=4) as response:
                lines = (json.loads(row) for row in response.iter_lines(chunk_size=1) if row)
                self.assertEqual(next(lines)["type"], "gameFull")
                with fake.lock:
                    for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):
                        fake.apply(chess.Move.from_uci(uci), "scripted_opponent")
                states = [next(lines) for _ in range(5)]
                self.assertEqual([x["status"] for x in states][-2:], ["mate", "mate"])
                audit = fake.evidence()
                commits = [r for r in audit["transport_trace"] if r["event"] == "board_commit"]
                self.assertEqual(len(commits), 4)
                self.assertEqual(audit["fault"]["activations"][0]["action"], "duplicate_terminal")
        finally:
            fake.close()


    def test_fenced_http_stream_reaches_eof(self):
        fake=FakeLichess("white",load_scenarios(SCENARIOS)["R09"])
        fake.start()
        try:
            url=fake.url+"api/stream/event"
            with requests.get(url,headers=HEADERS,stream=True,timeout=4) as first:
                with requests.get(url,headers=HEADERS,stream=True,timeout=4) as second:
                    self.assertEqual(first.status_code,200)
                    self.assertEqual(second.status_code,200)
                    # Must finish rather than hang on an unterminated HTTP/1.1 chunk.
                    self.assertFalse(any(first.iter_lines(chunk_size=1)))
                    fake.issue_challenge()
                    event=json.loads(next(x for x in second.iter_lines(chunk_size=1) if x))
                    self.assertEqual(event["type"],"challenge")
        finally:
            fake.close()


if __name__ == "__main__":
    unittest.main()
