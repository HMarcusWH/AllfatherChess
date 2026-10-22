"""Release-1 is byte-frozen despite private canary experiment."""
import json
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
class NoReleasePromotion(unittest.TestCase):
    def test_offline_release_entrypoint_and_contract(self):
        text=(ROOT/"deploy/release/entrypoint.py").read_text()
        self.assertIn('choices=("verify", "offline-smoke")',text)
        self.assertIn('if os.environ.get("LICHESS_BOT_TOKEN")',text)
        contract=json.loads((ROOT/"deploy/release/runtime-contract.json").read_text())
        self.assertIs(contract["live_play"],False)
        self.assertEqual(contract["egress"],"none")
        self.assertEqual(contract["commands"],["verify","offline-smoke"])
    def test_all_promotions_still_forbidden(self):
        release=json.loads((ROOT/"qualification/release-qualification-v1.json").read_text())
        for key in ("live_transport_qualified","license_compliance_approved",
                    "artifact_distribution_authorized","public_bot_release",
                    "destination_host_qualified","release_promotion_authorized"):
            self.assertIs(release[key],False,key)
        license=json.loads((ROOT/"qualification/license-compliance-v1.json").read_text())
        self.assertIs(license["legal_review_completed"],False)

if __name__=="__main__":unittest.main()
