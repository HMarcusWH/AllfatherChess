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

    def test_complete_canonical_target_and_query_values_are_observed(self):
        self.assertEqual(self.call("GET",
                         "/api/users/status?ids=allfatherfixture"), 200)
        good = self.fake.requests[-1]
        self.assertEqual(good["request_target"],
                         "/api/users/status?ids=allfatherfixture")
        self.assertTrue(good["target_canonical"])
        self.assertEqual(good["path"], "/api/users/status")
        # A rejected query is never stored verbatim in downloadable evidence.
        for target in ("/api/account?access_token=private-test-secret",
                       "/api/users/status?ids=unapproved",
                       "/api/users/status?ids=allfatherfixture&other=yes",
                       "/api/account?x=1"):
            self.assertEqual(self.call("GET", target), 403)
            record = self.fake.requests[-1]
            self.assertFalse(record["target_canonical"])
            self.assertIsNone(record["request_target"])
            self.assertIn("request_target_sha256", record)
            self.assertNotIn("private-test-secret", str(record))
        self.assertEqual(self.call(
            "POST", "/api/challenge/"+GAME_ID+"/accept"), 200)
        for target in ("/api/bot/game/"+GAME_ID+"/move/e2e4?offeringDraw=false",
                       "/api/bot/game/"+GAME_ID+"/move/e2e4?offeringDraw=true"):
            self.assertIn(self.call("POST", target), (200,400,409))
            record=self.fake.requests[-1]
            self.assertTrue(record["target_canonical"])
            self.assertEqual(record["request_target"], target)

    def test_rejected_move_suffixes_not_published(self):
        prefix=f"/api/bot/game/{GAME_ID}/move/"
        for target in (prefix+"garbage", prefix+"e2e4PRIVATE_CANARY",
                       prefix+"e2e4/PRIVATE_CANARY",
                       prefix+"e2e4?secret=PRIVATE_CANARY"):
            with self.subTest(target=target):
                self.assertEqual(self.call("POST",target),403)
                record=self.fake.requests[-1]
                self.assertEqual(record["path"],
                                 prefix+"e2e4" if "?" in target
                                 else "__invalid_path__")
                self.assertFalse(record["target_canonical"])
                self.assertIsNone(record["request_target"])
                self.assertNotIn("PRIVATE_CANARY",str(record))
                self.assertNotIn("PRIVATE_CANARY",
                                 str(self.fake.attempted_forbidden))

    def test_raw_trace_artifact_does_not_retain_malformed_path_canaries(self):
        from pathlib import Path
        from tests.lichess.fault_plan import load_scenarios
        self.fake.close()
        scenarios=Path(__file__).resolve().parents[2] / (
            "tests/fixtures/lichess/recovery_scenarios.json")
        self.fake=FakeLichess("white",load_scenarios(scenarios)["R00"])
        self.fake.start()
        for target in ("/api/account/PRIVATE_CANARY",
                       f"/api/bot/game/{GAME_ID}/move/PRIVATE_CANARY",
                       "//api/account?secret=PRIVATE_CANARY"):
            self.assertEqual(self.call("GET",target),403)
        report=json.dumps(self.fake.evidence(),sort_keys=True)
        self.assertNotIn("PRIVATE_CANARY",report)
        self.assertIn("__invalid_path__",report)

    def test_network_path_bypasses_cpython_normalization_only_if_not_observed(self):
        import socket
        for target in ("//api/account", "///api/account",
                       "//api/users/status?ids=allfatherfixture"):
            with self.subTest(target=target):
                with socket.create_connection(self.fake.http.server_address,
                                              timeout=4) as connection:
                    connection.settimeout(4)
                    query=(f"GET {target} HTTP/1.1\r\n"
                           "Host: 127.0.0.1\r\n"
                           f"Authorization: Bearer {TOKEN}\r\n"
                           "Connection: close\r\n\r\n").encode()
                    connection.sendall(query)
                    answer=connection.recv(1024)
                self.assertIn(b"403",answer)
                observed=self.fake.requests[-1]
                self.assertEqual(observed["method"],"GET")
                self.assertEqual(observed["status"],403)
                self.assertFalse(observed["target_canonical"])
                self.assertIsNone(observed["request_target"])
                self.assertEqual(len(observed["request_target_sha256"]),64)
                self.assertIn("GET __invalid_path__",
                              self.fake.attempted_forbidden)
                self.assertNotIn(target, str(self.fake.requests))
                self.assertNotIn(target, str(self.fake.attempted_forbidden))

    def test_raw_double_slash_post_never_becomes_valid_token_endpoint(self):
        import socket
        with socket.create_connection(self.fake.http.server_address,
                                      timeout=4) as connection:
            connection.settimeout(4)
            connection.sendall((
                "POST //api/token/test HTTP/1.1\r\n"
                "Host: 127.0.0.1\r\n"
                f"Authorization: Bearer {TOKEN}\r\n"
                "Content-Length: 0\r\n"
                "Connection: close\r\n\r\n"
            ).encode())
            answer=connection.recv(1024)
        self.assertIn(b"403",answer)
        record=self.fake.requests[-1]
        self.assertEqual(record["status"],403)
        self.assertFalse(record["target_canonical"])
        self.assertIsNone(record["request_target"])

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
