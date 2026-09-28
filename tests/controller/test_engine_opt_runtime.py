#!/usr/bin/env python3
from __future__ import annotations
import json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from controller.runtime import BackendManager,RuntimeError as ControllerRuntimeError,load_runtime_config
from controller.routing import RoutingPolicy
from tests.controller.test_shadow_runtime import write_shadow_config

class PhaseRuntimeTests(unittest.TestCase):
    def _path(self,tmp:Path)->Path:
        path=write_shadow_config(tmp,verification=True)
        doc=json.loads(path.read_text())
        for spec in doc["instances"].values():
            if spec["role"]=="shadow":
                spec["options"]["MultiPV"]=1
                spec["phase_options"]={
                    "EXPLORE":{"MultiPV":1},
                    "VERIFY":{"MultiPV":3},
                    "STAGED_VERIFY":{"MultiPV":3},
                }
        doc["instances"]["lc0-shadow"]["warmup"]={
            "enabled":True,"nodes":2,"position":"startpos","reset_after":True
        }
        path.write_text(json.dumps(doc))
        return path

    def test_phase_options_and_warmup_parse_and_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=BackendManager.from_path(self._path(Path(tmp)))
            manager.start()
            try:
                self.assertTrue(manager.shadow_available("lc0-shadow"))
                self.assertEqual(manager.effective_options("lc0-shadow")["MultiPV"],1)
                self.assertEqual(manager.configure_shadow_phase("lc0-shadow","VERIFY")["MultiPV"],3)
                self.assertEqual(manager.effective_options("lc0-shadow")["MultiPV"],3)
                self.assertEqual(manager.configure_shadow_phase("lc0-shadow","EXPLORE")["MultiPV"],1)
            finally:
                manager.close()

    def test_unknown_phase_is_rejected_at_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=self._path(Path(tmp))
            doc=json.loads(path.read_text())
            doc["instances"]["lc0-shadow"]["phase_options"]["NOPE"]={"MultiPV":2}
            path.write_text(json.dumps(doc))
            with self.assertRaises(ControllerRuntimeError):
                load_runtime_config(path)

class RoutingEstimateTests(unittest.TestCase):
    def test_owner_specific_estimates_override_scalars(self):
        p=RoutingPolicy.from_config({
            "policy":"conservative_v1","min_observation_nodes":1,
            "checkpoint_interval_ms":10,"max_stages_per_owner":1,"extend_nodes":1,
            "stop_max_reversal_risk":0.05,"stop_min_support":1,
            "stop_min_stability_fraction":0.5,"stage_cpu_ms_estimate":500,
            "anchor_cpu_ms_estimate":0,"stage_gpu_ms_estimate":0,
            "verify_stage_cpu_ms_estimate":750,"verify_stage_gpu_ms_estimate":0,
            "stage_cpu_ms_estimate_by_owner":{"lc0":1600},
            "verify_stage_cpu_ms_estimate_by_owner":{"lc0":1700},
        })
        self.assertEqual(p.stage_cpu_estimate_for("stockfish"),500)
        self.assertEqual(p.stage_cpu_estimate_for("lc0"),1600)
        self.assertEqual(p.verify_cpu_estimate_for("lc0"),1700)

if __name__=="__main__":
    unittest.main()
