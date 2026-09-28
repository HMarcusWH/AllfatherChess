#!/usr/bin/env python3
from __future__ import annotations
import copy,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from controller.engine_opt_profile import EngineOptProfileError,load_json,validate_reference,validate_hybrid
from tools.engine_opt.corpus import load_epd

class EngineOptContractTests(unittest.TestCase):
    def test_static_profiles(self):
        p=load_json(ROOT/"qualification/online-engine-opt-v2.json")
        s=load_json(ROOT/"qualification/engine-opt-v2-selection.json")
        r=load_json(ROOT/"config/allfather.online-engine-opt-v2.json")
        hp=load_json(ROOT/"qualification/online-hybrid-v2.json")
        h=load_json(ROOT/"config/allfather.online-hybrid-v2.validation.json")
        validate_reference(p,s,r)
        validate_hybrid(hp,r,h)

    def test_hybrid_rejects_reference_envelope_and_clock_drift(self):
        hp=load_json(ROOT/"qualification/online-hybrid-v2.json")
        r=load_json(ROOT/"config/allfather.online-engine-opt-v2.json")
        h=load_json(ROOT/"config/allfather.online-hybrid-v2.validation.json")
        for mutate in (
            lambda doc: doc["budget"].__setitem__("cpu_ms", doc["budget"]["cpu_ms"]+1),
            lambda doc: doc["online_time"].__setitem__("max_move_ms", doc["online_time"]["max_move_ms"]-1),
            lambda doc: doc["routing"]["stage_cpu_ms_estimate_by_owner"].__setitem__("lc0", 401),
            lambda doc: doc["hybrid_authority"].__setitem__("allow_skipped_extension_authority", True),
        ):
            candidate=copy.deepcopy(h)
            mutate(candidate)
            with self.assertRaises(EngineOptProfileError):
                validate_hybrid(hp,r,candidate)

    def test_selection_state_is_explicit(self):
        s=load_json(ROOT/"qualification/engine-opt-v2-selection.json")
        self.assertIn(s["status"],("provisional","selected"))

    def test_corpus_is_frozen_and_nonempty(self):
        rows=load_epd(ROOT/"tests/fixtures/engine_opt/positions.epd")
        self.assertGreaterEqual(len(rows),8)
        self.assertEqual(len({r.case_id for r in rows}),len(rows))

if __name__=="__main__":
    unittest.main()
