#!/usr/bin/env python3
from __future__ import annotations
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import importlib.util

SPEC=importlib.util.spec_from_file_location(
    "engine_opt_ab",ROOT/"scripts/engine-opt-ab.py"
)
assert SPEC and SPEC.loader
MOD=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

class EngineOptAbTests(unittest.TestCase):
    def test_constituent_option_contracts(self):
        sf,args,env=MOD.options_for("stockfish",None)
        self.assertEqual(sf["Hash"],16)
        self.assertEqual(args,[])
        self.assertEqual(env,{})
        rr,args,env=MOD.options_for("reckless",None)
        self.assertFalse(rr["Minimal"])
        self.assertEqual(args,[])
        lc,args,env=MOD.options_for("lc0",Path("/tmp/net.pb.gz"))
        self.assertEqual(lc["MinibatchSize"],0)
        self.assertEqual(lc["MaxPrefetch"],0)
        self.assertNotIn("AdaptivePrefetch",lc)
        self.assertNotIn("DefectTelemetry",lc)
        self.assertEqual(args,["--show-hidden"])
        self.assertEqual(env["OPENBLAS_NUM_THREADS"],"1")

if __name__=="__main__":
    unittest.main()
