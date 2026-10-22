"""Live fork only derives from pinned offline defaults; never engine selection."""
import json
import os
import re
import unittest
from pathlib import Path
from deploy.lichess.live_gateway_policy import (
    GATEWAY_URL,LICHESS_ORIGIN,SYNTHETIC_TOKEN)

ROOT=Path(__file__).resolve().parents[2]

class LiveBridgeContract(unittest.TestCase):
    def test_endpoint_origin_and_bridge_nonsecret(self):
        self.assertEqual(LICHESS_ORIGIN,"https://lichess.org")
        self.assertEqual(GATEWAY_URL,"http://live-gateway:8765/")
        self.assertEqual(SYNTHETIC_TOKEN,"offline-fixture-token")
    def test_controller_and_network_unchanged(self):
        policy=json.loads((ROOT/"qualification/online-live-canary-v1.json").read_text())
        for key in ("public_bot_release","live_game_authorized",
                    "operator_armed","outbound_challenges_allowed",
                    "automatic_matchmaking_allowed",
                    "external_move_sources_allowed","move_retries_allowed"):
            self.assertIs(policy[key],False,key)
        self.assertIsNone(policy["bot_account_username"])
        self.assertIsNone(policy["opponent_username"])
        self.assertEqual((policy["clock_seconds"],policy["increment_seconds"]),(600,5))
    def test_recovery_guard_offline_default_still_present(self):
        guard=(ROOT/"deploy/lichess/guarded_transport.py").read_text()
        self.assertIn('os.getenv("ALLFATHER_TRANSPORT_PROFILE", "offline")',guard)
        self.assertIn('parsed.hostname != "127.0.0.1"',guard)
        self.assertIn('journal.change_status',guard)
        self.assertIn('"UNKNOWN_ACK"',guard)
        self.assertIn('journal.establish_cooldown',guard)
    def test_pinned_exact_source_not_mutated(self):
        lock=json.loads((ROOT/"deploy/lichess/bridge.lock.json").read_text())
        self.assertEqual(lock["commit"],"df7e730de58cc3ef2f1415a0dc2eeda842d39167")
        self.assertIs(lock["live_api_permitted"],False)
    def test_no_production_test_origin_override(self):
        source=(ROOT/"deploy/live_canary/entrypoint.py").read_text()
        self.assertNotIn("for_fake_test_only",source)
        self.assertNotIn("fake_lichess_api",source)
        self.assertIn('choices=("verify-only","gateway","bridge")',source)

if __name__=="__main__":unittest.main()
