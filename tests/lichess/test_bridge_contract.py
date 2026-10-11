"""Hermetic ONLINE-3B provenance and policy contracts."""
from __future__ import annotations
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from deploy.lichess import bridge_policy
ROOT = Path(__file__).resolve().parents[2]


class BridgeContractTests(unittest.TestCase):
    def test_source_lock_is_exact_and_nonlive(self):
        lock = json.loads((ROOT / "deploy/lichess/bridge.lock.json").read_text())
        self.assertEqual(lock["scope"], "ONLINE-3B-OFFLINE-ONLY")
        self.assertEqual(len(lock["commit"]), 40)
        self.assertFalse(lock["live_api_permitted"])
        self.assertTrue(all(len(sha) == 40 for sha in lock["tracked_blobs"].values()))

    def test_policy_never_preawards_qualification(self):
        policy = json.loads((ROOT / "qualification/online-bridge-v1.json").read_text())
        self.assertEqual(policy["scope"], "offline-simulation-only")
        self.assertFalse(policy["public_bot_release"])
        self.assertNotIn("offline_bridge_qualified", policy)

    def test_local_endpoint_and_dangerous_drift(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "deploy/bin").mkdir(parents=True)
            (root / "deploy/bin/allfather-online").write_text("fixture")
            cfg = {
                "url": "http://127.0.0.1:8765/", "token": bridge_policy.TOKEN,
                "engine": {
                    "dir": str((root / "deploy/bin").resolve()), "name": "allfather-online",
                    "working_dir": str(root.resolve()), "protocol": "uci",
                    "ponder": False, "uci_options": {}, "engine_options": {},
                    "polyglot": {"enabled": False},
                    "online_moves": {n: {"enabled": False} for n in
                        ("chessdb_book", "lichess_cloud_analysis", "lichess_opening_explorer", "online_egtb")},
                    "lichess_bot_tbs": {n: {"enabled": False} for n in ("syzygy", "gaviota")},
                    "draw_or_resign": {"resign_enabled": False, "offer_draw_enabled": False},
                },
                "challenge": {
                    "concurrency": 1, "accept_bot": True, "only_bot": True,
                    "min_base": 600, "max_base": 600, "min_increment": 5, "max_increment": 5,
                    "variants": ["standard"], "time_controls": ["rapid"],
                    "modes": ["casual"], "allow_list": ["approved_test_bot"],
                },
                "matchmaking": {"allow_matchmaking": False},
                "greeting": {}, "max_takebacks_accepted": 0,
                "fake_think_time": False, "rate_limiting_delay": 0
            }
            bridge_policy.assert_safety(cfg, release=root, url=cfg["url"])
            for path, value in (
                (("url",), "https://lichess.org/"),
                (("engine", "ponder"), True),
                (("challenge", "concurrency"), 2),
                (("challenge", "min_base"), 300),
                (("matchmaking", "allow_matchmaking"), True),
                (("engine", "uci_options"), {"Hash": 512}),
                (("engine", "online_moves", "lichess_cloud_analysis", "enabled"), True),
            ):
                bad = copy.deepcopy(cfg)
                part = bad
                for key in path[:-1]:
                    part = part[key]
                part[path[-1]] = value
                with self.subTest(path=path), self.assertRaises(ValueError):
                    bridge_policy.assert_safety(bad, release=root, url=cfg["url"])

    def test_credential_scrubbing(self):
        import os
        original = os.environ.get("LICHESS_BOT_TOKEN")
        try:
            os.environ["LICHESS_BOT_TOKEN"] = "DO_NOT_LEAK"
            clean = bridge_policy.sanitized_env()
            self.assertNotIn("LICHESS_BOT_TOKEN", clean)
        finally:
            if original is None:
                os.environ.pop("LICHESS_BOT_TOKEN", None)
            else:
                os.environ["LICHESS_BOT_TOKEN"] = original


if __name__ == "__main__":
    unittest.main()
