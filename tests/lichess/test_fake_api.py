"""Fake HTTP API legality/endpoint negative controls, with genuine sockets."""
import json
import unittest
import urllib.error
import urllib.request
from tests.lichess.fake_lichess_api import FakeLichess, GAME_ID, TOKEN

class FakeAPITests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLichess("white")
        self.fake.start()
    def tearDown(self):
        self.fake.close()
    def call(self, verb, path):
        req = urllib.request.Request(
            self.fake.url.rstrip("/") + path, method=verb,
            data=b"" if verb == "POST" else None,
            headers={"Authorization": "Bearer " + TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as e:
            return e.code
    def test_illegal_before_start_or_wrong_move(self):
        self.assertEqual(self.call("POST", f"/api/bot/game/{GAME_ID}/move/e2e4"), 409)
        self.assertEqual(self.call("POST", f"/api/challenge/{GAME_ID}/accept"), 200)
        self.assertEqual(self.call("POST", f"/api/bot/game/{GAME_ID}/move/e2e5"), 400)
        self.assertEqual(self.fake.accepted, [])
    def test_post_body_and_session_reuse(self):
        import requests
        session = requests.Session()
        session.headers.update({"Authorization": "Bearer " + TOKEN})
        with session:
            r = session.post(self.fake.url + "api/token/test",
                             data=TOKEN, timeout=5)
            self.assertEqual(r.status_code, 200)
            self.assertIn(TOKEN, r.json())
            p = session.get(self.fake.url + "api/account", timeout=5)
            self.assertEqual(p.status_code, 200)
            self.assertEqual(p.json()["title"], "BOT")

    def test_duplicate_accept_rejected(self):
        path = f"/api/challenge/{GAME_ID}/accept"
        self.assertEqual(self.call("POST", path), 200)
        self.assertEqual(self.call("POST", path), 409)
    def test_prohibited_routes_are_attempted_not_accepted(self):
        for suffix in ("resign", "abort", "chat", "takeback/yes"):
            self.assertEqual(self.call("POST", f"/api/bot/game/{GAME_ID}/{suffix}"), 403)
        self.assertEqual(self.call("POST", "/api/bot/account/upgrade"), 403)
        self.assertEqual(len(self.fake.attempted_forbidden), 5)

if __name__ == "__main__":
    unittest.main()
