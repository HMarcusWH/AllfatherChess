"""S3B source policy explicitly distinguishes real engines from certification."""
from __future__ import annotations
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from tools.strength_lab.s3b_runner import (
    preflight,_procfs,_checked_file, ROOT, POLICY)
from tools.strength_lab.s3b_verify import _typed_turn,_search
from controller.strength.contracts import StrengthContractError
from tests.strength_s3a.test_candidates import fixture

class DiagnosticBoundary(unittest.TestCase):
    def test_requires_explicit_offline_opt_in_before_any_engine_bytes(self):
        with patch.dict("os.environ",{"ALLFATHER_S3B_OFFLINE":"0"}):
            with self.assertRaises(StrengthContractError):
                preflight()

    def test_policy_never_promotes_real_experiment_to_resource_qualified(self):
        policy=json.loads(POLICY.read_text())
        self.assertEqual(policy["mode"],"observe_only")
        self.assertEqual(policy["status"],"REAL_ENGINE_DIAGNOSTIC_NOT_RESOURCE_QUALIFIED")
        self.assertTrue(all(x is False for x in policy["claims"].values()))
        self.assertEqual(policy["comparator"]["round_nodes"],[16384,65536])
        self.assertEqual(policy["proposer"]["reckless"]["nodes"],4096)
        self.assertEqual(policy["proposer"]["lc0"]["nodes"],128)
        self.assertEqual(policy["supported_native_go_commands"],["go wtime 300000 btime 300000 winc 0 binc 0"])

    def test_real_source_identity_roundtrip(self):
        a=fixture()
        self.assertEqual(_typed_turn(a.turn.as_dict()),a.turn)
        self.assertEqual(_search(a.anchor_search.as_dict()),a.anchor_search)
        with self.assertRaises(StrengthContractError):
            _typed_turn({**a.turn.as_dict(),"source_key":"forged"})

    def test_procfs_missing_pid_is_unknown_never_zero(self):
        self.assertIsNone(_procfs(None))
        self.assertIsNone(_procfs(-1))
        with self.assertRaises(StrengthContractError):
            _checked_file(Path(__file__).resolve().parents[2]/"LICENSES.md",
                          ROOT/"build/online-engine-opt-v2")

if __name__=="__main__":unittest.main()
