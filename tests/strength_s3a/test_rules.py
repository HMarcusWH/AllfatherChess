"""Independent pinned rules validation rejects incomplete/illegal root witnesses."""
from __future__ import annotations
from dataclasses import replace
import unittest
from controller.strength.contracts import StrengthContractError
from controller.strength.candidates import reconstruct_proposal
from tools.strength_lab.s3a_rules import legal_roots,verify_proposal
from tests.strength_s3a.test_candidates import fixture,CMD


class S3ARules(unittest.TestCase):
    def test_source_only_full_legal_root_replay(self):
        base=fixture()
        full=legal_roots(CMD)
        self.assertGreater(len(full),10)
        self.assertIn(base.incumbent.move,full)
        self.assertIn(base.move,full)
        proposal=replace(base,allowed_legal_roots=full)
        verdict=verify_proposal(proposal.as_dict())
        self.assertEqual(verdict["legal_root_count"],len(full))
        self.assertTrue(verdict["legal_history_verified"])
        for key in ("strength_qualified","resource_qualified","quality_qualified",
                    "work_dispatch_authorized","move_authorized"):
            self.assertIs(verdict[key],False)

    def test_fabricated_legal_root_subset_and_rogue_move_are_denied(self):
        partial=fixture()
        with self.assertRaises(StrengthContractError):
            verify_proposal(partial.as_dict())
        with self.assertRaises(StrengthContractError):
            legal_roots("position startpos moves e2e5")
        with self.assertRaises(StrengthContractError):
            legal_roots("position startpos moves e2e4 e7e5 e2e4")
        with self.assertRaises(StrengthContractError):
            legal_roots("position fen 8/8/8/8/8/8/8/8 w - - 0 1")

if __name__=="__main__":
    unittest.main()
