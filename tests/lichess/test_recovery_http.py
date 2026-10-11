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
        _, result = self.run_fault("R03", 1, expected_status=504)
        self.assertTrue(any(x["event"] == "response_write_completed" and x["status"] == 504
                            for x in result["transport_trace"]))

    def test_rate_limit_rejects_without_move(self):
        self.run_fault("R10", 0, expected_status=429)

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


if __name__ == "__main__":
    unittest.main()
