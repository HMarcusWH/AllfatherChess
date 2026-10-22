"""A published move is not a completed VERIFY evidence certificate."""
from __future__ import annotations

import unittest

from controller.counterfactual import terminal_evidence_from_manifest
from controller.decision import OWNER_ORDER


class PublicationDispositionQualification(unittest.TestCase):
    def test_published_incomplete_is_ineligible_even_with_three_completed_stages(self):
        base={
            "verification_id":"synthetic-verified-terminal-plane",
            "nomination":{"candidate_roots":["e2e4","d2d4","g1f3"]},
            "stages":[{"owner":owner,"family":owner,"bestmove":"e2e4",
                       "disposition":"completed"} for owner in OWNER_ORDER],
            "disposition":{"run":"completed"},
        }
        completed=terminal_evidence_from_manifest(base)
        self.assertTrue(completed.complete,"positive control must be complete")
        incomplete={**base,"disposition":{"run":"published_incomplete"}}
        terminal=terminal_evidence_from_manifest(incomplete)
        self.assertEqual(terminal.run_disposition,"published_incomplete")
        self.assertFalse(terminal.complete,
            "outward UCI publication cannot substitute for complete VERIFY evidence")
        for mode in ("cancelled","error","running"):
            with self.subTest(mode=mode):
                self.assertFalse(terminal_evidence_from_manifest(
                    {**base,"disposition":{"run":mode}}).complete)


if __name__=="__main__":
    unittest.main()
