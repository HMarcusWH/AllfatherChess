#!/usr/bin/env python3
from __future__ import annotations

import unittest

from tools.local_game.common import QualificationError
from tools.meta1.common import RUN_DISPOSITION, campaign_disposition


class Meta1GateTests(unittest.TestCase):
    def test_real_j12_authority_allows_campaign(self):
        self.assertEqual(
            campaign_disposition({
                "mechanism_valid": True,
                "authority_qualified": True,
                "qualification_disposition": "QUALIFIED_ORCHESTRATED_AUTHORITY",
                "synthetic_capacity_observation": False,
            }),
            RUN_DISPOSITION,
        )

    def test_synthetic_host_is_recorded_not_played(self):
        self.assertEqual(
            campaign_disposition({
                "mechanism_valid": True,
                "authority_qualified": False,
                "qualification_disposition": "NOT_QUALIFIED_HOST_CAPACITY",
                "synthetic_capacity_observation": True,
            }),
            "NOT_QUALIFIED_HOST_CAPACITY",
        )

    def test_invalid_j12_evidence_fails_closed(self):
        with self.assertRaises(QualificationError):
            campaign_disposition({
                "mechanism_valid": False,
                "authority_qualified": False,
                "qualification_disposition": "INVALID_EVIDENCE",
            })


if __name__ == "__main__":
    unittest.main()
