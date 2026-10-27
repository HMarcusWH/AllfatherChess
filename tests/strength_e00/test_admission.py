"""E00 admission never launders a smoke result into scientific qualification."""
from __future__ import annotations
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from tools.strength_lab.e00_admission import review_candidate, POLICY
from tools.strength_lab.e00_verify import E00Failure

class E00AdmissionNegatives(unittest.TestCase):
    def test_contract_keeps_review_and_public_authority_false(self):
        data=json.loads(POLICY.read_text(encoding="utf-8"))
        self.assertEqual(data["verdict"],"REVIEW_REQUIRED")
        self.assertEqual(data["full_history_count"],512)
        self.assertTrue(data["requires_strict_cgroup"])
        self.assertTrue(data["human_review_required"])
        self.assertFalse(data["scientific_qualified"])
        self.assertFalse(data["strength_qualified"])
        self.assertFalse(data["release_authorized"])

    def test_no_review_candidate_from_incomplete_smoke(self):
        from tools.strength_lab.e00_runner import ROOT_OUT
        ROOT_OUT.mkdir(parents=True,exist_ok=True)
        import tempfile
        with tempfile.TemporaryDirectory(dir=ROOT_OUT) as temp:
            folder=Path(temp)
            with patch("tools.strength_lab.e00_admission.verify_campaign",return_value={
                "phase":"smoke","status":"PASS","actual_history_count":8,
                "fixed_node_bestmove_agreement":8,
                "flight_reconstructed_count":8,"resource_qualified":False,
                "e00_scientifically_qualified":False,"strength_qualified":False
            }):
                with self.assertRaisesRegex(E00Failure,"only full 512"):
                    review_candidate(folder)
            self.assertFalse((folder/"e00-review-candidate.json").exists())

    def test_nonpassing_full_run_denied(self):
        from tools.strength_lab.e00_runner import ROOT_OUT
        ROOT_OUT.mkdir(parents=True,exist_ok=True)
        import tempfile
        with tempfile.TemporaryDirectory(dir=ROOT_OUT) as temp:
            folder=Path(temp)
            with patch("tools.strength_lab.e00_admission.verify_campaign",return_value={
                "phase":"full","status":"INCONCLUSIVE","actual_history_count":512,
                "fixed_node_bestmove_agreement":512,
                "flight_reconstructed_count":512,"resource_qualified":False,
                "e00_scientifically_qualified":False,"strength_qualified":False
            }):
                with self.assertRaises(E00Failure):
                    review_candidate(folder)

if __name__=="__main__":
    unittest.main()
