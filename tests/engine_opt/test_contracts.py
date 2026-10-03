#!/usr/bin/env python3
from __future__ import annotations
import copy,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from controller.engine_opt_profile import EngineOptProfileError,load_json,validate_reference,validate_hybrid
from tools.engine_opt.corpus import load_epd
from tools.engine_opt.selection import validate_selected_lc0_rows

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
        self.assertEqual(s["selected"]["lc0"]["matrix_profile"],"b4-p0-c256k-cold")
        self.assertIsNone(s["selected"]["lc0"]["warmup_nodes"])
        self.assertEqual(s["selected"]["lc0"]["minibatch_size"],4)
        self.assertEqual(s["selected"]["lc0"]["max_prefetch"],0)
        self.assertGreaterEqual(s["qualification"]["confirmation_repeats"],2)
        self.assertEqual(s["qualification"]["baseline_profile"],"v1-current-cold")
        self.assertEqual(s["qualification"]["corpus_cases"],8)

    def test_selected_lc0_row_contract_handles_cold_profiles(self):
        selected=load_json(ROOT/"qualification/engine-opt-v2-selection.json")["selected"]["lc0"]
        row={
            "options":{
                "NNCacheSize":selected["nn_cache_size"],
                "MinibatchSize":selected["minibatch_size"],
                "MaxPrefetch":selected["max_prefetch"],
                "AdaptivePrefetch":selected["adaptive_prefetch"],
            },
            "warmup":None,
        }
        validate_selected_lc0_rows([row],selected,lambda ok,msg:self.assertTrue(ok,msg))
        bad=copy.deepcopy(row)
        bad["warmup"]={"nodes":64}
        with self.assertRaises(AssertionError):
            validate_selected_lc0_rows([bad],selected,lambda ok,msg:self.assertTrue(ok,msg))

    def test_historical_host_binding_is_explicitly_legacy_unbound(self):
        binding=load_json(ROOT/"qualification/engine-opt-v2-host-binding.json")
        self.assertEqual(binding["profile_id"],"engine-opt-v2")
        self.assertEqual(binding["status"],"profile_change_pending_requalification")
        self.assertEqual(
            binding["historical_qualification"]["qualified_head"],
            "085420843b95f3f2dd206fc1c66bf642cbd49b6d",
        )
        self.assertEqual(binding["host_binding"]["qualification_domain"],"legacy_unbound")
        self.assertEqual(binding["host_binding"]["runtime_substrate"],"legacy_unbound")
        self.assertFalse(binding["host_binding"]["generic_host_portability_established"])
        self.assertFalse(binding["host_binding"]["selection_eligible_on_unmatched_host"])
        self.assertEqual(binding["post_j2_diagnostic"]["pull_request"],48)
        self.assertEqual(binding["post_j2_diagnostic"]["diagnosis"],"WITHIN_BINARY_INSTABILITY")
        self.assertFalse(binding["claim_boundary"]["generic_host_portability_established"])
        self.assertEqual(
            binding["current_domain_bound_qualification"]["status"],
            "historical_superseded_by_profile_change",
        )
        self.assertEqual(
            binding["pending_domain_bound_qualification"]["selected_profile"],
            "b4-p0-c256k-cold",
        )

    def test_corpus_is_frozen_and_nonempty(self):
        rows=load_epd(ROOT/"tests/fixtures/engine_opt/positions.epd")
        self.assertGreaterEqual(len(rows),8)
        self.assertEqual(len({r.case_id for r in rows}),len(rows))

if __name__=="__main__":
    unittest.main()
