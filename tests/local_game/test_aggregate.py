"""Aggregate soak shards may promote claims only after complete-set verification."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.local_game.aggregate_soak import aggregate
from tools.local_game.common import ROOT, load, policy, save, source_identity
from tools.local_game.runner import schedule


class SoakAggregateTests(unittest.TestCase):
    def _write_shards(self, root: Path, *, omit: int | None = None) -> None:
        jobs=schedule(policy(ROOT),"soak")
        source=source_identity()
        for shard in range(10):
            if shard==omit:
                continue
            assigned=[job for index,job in enumerate(jobs) if index%10==shard]
            campaign=root/f"artifact-{shard}"/"test-results"/"local-full-game"/f"campaign-{shard}"
            campaign.mkdir(parents=True)
            save(campaign/"manifest.json",{
                "schema_version":1,
                "campaign_id":f"campaign-{shard}",
                "mode":"soak",
                "shard":{"index":shard,"count":10},
                "source":source,
                "status":"completed",
                "failures":[],
                "planned_jobs":assigned,
            })
            save(campaign/"report.json",{
                "schema_version":1,
                "campaign_id":f"campaign-{shard}",
                "execution_scope":"partial_soak_shard",
                "passed":True,
                "shard_passed":True,
                "baseline_is_complete":False,
                "aggregate_soak_complete":False,
                "observed_games":2*len(assigned),
                "validated_games":2*len(assigned),
                "claim_boundary":{"full_game_lifecycle":False},
            })

    def test_fabricated_manifest_report_pairs_cannot_promote_aggregate_claim(self):
        (ROOT/"build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/"build") as tmp:
            root=Path(tmp)
            self._write_shards(root)
            report=aggregate(root)
            self.assertFalse(report["passed"])
            self.assertFalse(report["aggregate_soak_complete"])
            self.assertTrue(any("requalification" in error or "QualificationError" in error
                                for error in report["errors"]))

    def test_complete_requalified_ten_shard_union_can_promote_aggregate_claim(self):
        (ROOT/"build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/"build") as tmp:
            root=Path(tmp)
            self._write_shards(root)

            def trusted_recompute(campaign):
                return load(Path(campaign) / "report.json")

            with patch("tools.local_game.aggregate_soak.qualify",
                       side_effect=trusted_recompute):
                report=aggregate(root)
            self.assertTrue(report["passed"])
            self.assertTrue(report["aggregate_soak_complete"])
            self.assertTrue(report["baseline_is_complete"])
            self.assertTrue(report["claim_boundary"]["full_game_lifecycle"])
            self.assertEqual(report["validated_games"],208)

    def test_missing_shard_fails_aggregate(self):
        (ROOT/"build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/"build") as tmp:
            root=Path(tmp)
            self._write_shards(root,omit=7)
            report=aggregate(root)
            self.assertFalse(report["passed"])
            self.assertFalse(report["aggregate_soak_complete"])
            self.assertIn("missing soak shards: [7]",report["errors"])


if __name__=="__main__":
    unittest.main()
