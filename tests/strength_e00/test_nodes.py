"""E00 destructive contracts: these are not real Stockfish qualification."""
from __future__ import annotations
import json,os
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from common.search_request import parse_position_command
from controller.strength.contracts import StrengthContractError,digest
from controller.strength.native_clock import NativeClockSettings,make_native_plan
from controller.strength.e00_nodes import E00NodeSettings,make_e00_plan,verify_e00_plan
from tools.strength_lab.e00_verify import E00Failure,p95,_bestmove,_board

ROOT=Path(__file__).resolve().parents[2]
SETTINGS=E00NodeSettings()
POSITION=parse_position_command("position startpos")

class NodeContractTests(unittest.TestCase):
    def make(self,go="go nodes 50000"):
        return make_e00_plan(command=go,position=POSITION,generation=3,
                             settings=SETTINGS,received_monotonic=123.25,
                             controller_cpu_started_ns=44)

    def test_exact_passthrough_and_reconstruction(self):
        plan=self.make()
        self.assertEqual(plan.anchor_go_command,"go nodes 50000")
        self.assertEqual(plan.native_nodes,50000)
        self.assertEqual(plan.helper_advice_ms,0)
        self.assertFalse(plan.as_dict()["authority"]["outward_move"])
        self.assertEqual(verify_e00_plan(plan.as_dict(),position=POSITION),[])

    def test_root_restrictions(self):
        plan=self.make("go nodes 64 searchmoves e2e4 d2d4")
        self.assertEqual(plan.restriction,("e2e4","d2d4"))
        self.assertEqual(verify_e00_plan(plan.as_dict(),position=POSITION),[])

    def test_only_nodes_and_bounded_requests(self):
        for go in ("go nodes 0","go nodes 200001","go nodes -1",
                   "go nodes 10 nodes 20","go nodes 50000 wtime 30000 btime 30000",
                   "go movetime 5000","go wtime 30000 btime 30000",
                   "go nodes 2000 searchmoves","go nodes 2000 searchmoves E2E4",
                   "go ponder nodes 5000","go nodes 50000 infinite",
                   "go nodes 50000 banana","go nodes 1000 searchmoves e2e4 e2e4"):
            with self.subTest(go=go):
                with self.assertRaises(StrengthContractError):self.make(go)

    def test_resealed_mutations_rejected(self):
        original=self.make().as_dict()
        for patch_value in ({"native_nodes":1},{"hard_budget_ms":100},
              {"anchor_go_command":"go nodes 1"},
              {"authority":{"timing":True,"outward_move":True}},
              {"experimental_only":False},{"restriction":["e2e4"]},
              {"schema_version":2}):
            with self.subTest(patch=patch_value):
                value={**original,**patch_value}
                raw=dict(value);raw.pop("plan_id")
                value["plan_id"]="e00-"+digest(raw)
                self.assertTrue(verify_e00_plan(value,position=POSITION))

    def test_ordinary_native_s2_still_denies_nodes(self):
        with self.assertRaises(StrengthContractError):
            make_native_plan(command="go nodes 50000",position=POSITION,
                generation=3,settings=NativeClockSettings(),received_monotonic=1.0,
                controller_cpu_started_ns=1)

    def test_offline_activation_and_schema_isolation(self):
        from controller.strength.config import from_v3
        conf=json.loads((ROOT/"config/allfather.strength-e00-nodes.experimental.json").read_text())
        name=conf["anchor"]
        opts=conf["instances"][name]["options"]
        specs={name:SimpleNamespace(family="stockfish",role="anchor",phase_options={},
                    warmup=None,options=opts,environment={})}
        source=ROOT/"config/allfather.strength-e00-nodes.experimental.json"
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(StrengthContractError):
                from_v3(raw=conf,path=source,root=ROOT,anchor=name,specs=specs)
        with patch.dict(os.environ,{"ALLFATHER_E00_OFFLINE":"1"}):
            settings,timing=from_v3(raw=conf,path=source,root=ROOT,anchor=name,specs=specs)
            self.assertEqual(settings.policy,"strength_e00_incumbent_observe_v1")
            self.assertIsInstance(timing,E00NodeSettings)
            poisoned=json.loads(json.dumps(conf))
            poisoned["strength"]["mode"]="apply"
            with self.assertRaises(StrengthContractError):
                from_v3(raw=poisoned,path=source,root=ROOT,anchor=name,specs=specs)
            poisoned=json.loads(json.dumps(conf))
            poisoned["strength"]["timing"]["max_nodes"]=50000000
            with self.assertRaises(StrengthContractError):
                from_v3(raw=poisoned,path=source,root=ROOT,anchor=name,specs=specs)

    def test_verifier_metrics_and_chess_move(self):
        self.assertEqual(p95([10.,30.,5.,20.]),30.)
        board=_board("position startpos moves e2e4 e7e5")
        self.assertEqual(_bestmove("bestmove g1f3",board),"g1f3")
        with self.assertRaises(E00Failure):_bestmove("bestmove a1a8",board)
        with self.assertRaises(E00Failure):p95([float("nan")])

if __name__=="__main__":
    unittest.main()
