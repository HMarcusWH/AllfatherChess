"""E00 source-integrity and measurement regressions: destructive controls."""
from __future__ import annotations
import copy
import hashlib
import unittest
from tools.strength_lab.e00_evidence import reconcile_flight,reconstruct_stdout_times
from tools.strength_lab.e00_metrics import paired_metrics
from tools.strength_lab.e00_resources import cpulist,verify_profile,verify_cpu_counters,cgroup_cpu_stat
from tools.strength_lab.e00_exec import enforce_search_cpu
from tools.strength_lab.participant_scope import ResourceScopeError
from unittest.mock import patch
from tools.strength_lab.e00_clock import (
    last_engine_work,frontend_publication_delay_ms,summarize_clock)

SRC="1"*40
SHA="a"*64
PROFILE="b"*64
OPTS="c"*64
CMD="position startpos"
BEST="bestmove e2e4"

def fixture():
    history=hashlib.sha256(CMD.encode()).hexdigest()
    row={"position_command":CMD,"history_sha256":history}
    turn={"generation":1,"history_sha256":history,
          "session_id":"1"*32,"search_nonce":"2"*32}
    selection={"anchor_line":BEST,"chosen_line":BEST,
               "incumbent_epoch":1,"would_authorize":False,
               "replaces_anchor":False}
    events=[
       {"event":"begin","data":{"turn":turn,"search":{
           "turn":turn,"source_commit":SRC,"binary_sha256":SHA,
           "profile_sha256":PROFILE,"options_sha256":OPTS},
           "position_command":CMD,"native_time":{
               "external_go_command":"go nodes 50000",
               "anchor_go_command":"go nodes 50000",
               "received_monotonic":1.0,"generation":1}}},
       {"event":"terminal","data":{"checkpoint":{"move":"e2e4",
           "observed_ns":1_500_000_000},"selection":selection}},
       {"event":"published","data":{"chosen_line":BEST,
           "at_ns":1_501_000_000,"epoch":1}}
    ]
    return row,{"events":events}

class EvidenceTests(unittest.TestCase):
    def check(self,flight,row):
        return reconcile_flight(flight,row=row,occurrence=0,published_stdout=BEST,
                                expected_go="go nodes 50000",source_commit=SRC,
                                binary_sha256=SHA,profile_sha256=PROFILE,
                                options_sha256=OPTS,prior_session="1"*32)

    def test_canonical_flight_matches_external_terminal(self):
        row,flight=fixture()
        self.assertEqual(self.check(flight,row),("1"*32,"2"*32))
        self.assertEqual(frontend_publication_delay_ms(flight),1.)

    def test_resealed_but_falsified_source_flight_is_rejected(self):
        row,base=fixture()
        patches=[
            (0,"search",{"source_commit":"2"*40}),
            (0,"turn",{"generation":2}),
            (0,"native_time",{"anchor_go_command":"go nodes 1"}),
            (1,"selection",{"chosen_line":"bestmove d2d4"}),
            (1,"checkpoint",{"move":"d2d4"}),
            (2,None,{"chosen_line":"bestmove d2d4"}),
            (2,None,{"at_ns":1_000_000_000}),
        ]
        for ix,section,patch in patches:
            bad=copy.deepcopy(base)
            target=bad["events"][ix]["data"]
            if section is not None:
                target=target[section]
            target.update(patch)
            with self.subTest(ix=ix,patch=patch):
                with self.assertRaises(ValueError):self.check(bad,row)
        with self.assertRaises(ValueError):
            self.check(base,{"position_command":"position startpos moves e2e4",
                             "history_sha256":row["history_sha256"]})

    def test_real_uci_timestamp_pairs_and_duplicates(self):
        rows=[{"direction":"stdin","line":"go nodes 100","ns":100},
              {"direction":"stdout","line":BEST,"ns":500}]
        self.assertEqual(reconstruct_stdout_times(rows,"go nodes 100",1)[0]["wall_ms"],.0004)
        with self.assertRaises(ValueError):
            reconstruct_stdout_times(rows+[rows[1]],"go nodes 100",1)
        with self.assertRaises(ValueError):
            reconstruct_stdout_times(rows,"go nodes 200",1)
        with self.assertRaises(ValueError):
            reconstruct_stdout_times(rows[:-1],"go nodes 100",1)

    def test_metrics_do_not_turn_missing_cpu_into_zero(self):
        self.assertIsNone(paired_metrics([5.,6.],[4.,5.])["wrapper_minus_direct_cpu_ms"])
        with self.assertRaises(ValueError):
            paired_metrics([4.,5.],[4.],None,None)
        with self.assertRaises(ValueError):
            paired_metrics([4.],[4.],[2.],None)

    def test_resource_counter_and_host_identity(self):
        self.assertTrue(verify_cpu_counters(100,2100,2.))
        self.assertFalse(verify_cpu_counters(None,2100,0.))
        self.assertFalse(verify_cpu_counters(None,None,0.))
        self.assertEqual(cpulist("0-2,6"),{0,1,2,6})
        with self.assertRaises(ValueError):cpulist("0-10000")
        profile={"schema_version":1,"kind":"e00_host_profile_v1",
                 "strength_qualified":False,"equal_compute":False,
                 "allowed_cpus":[2,3],"strict_physical_isolation":True,
                 "system":"Linux","architecture":"x86_64",
                 "search_cpu":2,"harness_cpu":3,
                 "sibling_sets":{"2":[2],"3":[3]}}
        verify_profile(profile,require_strict=True)
        bad=copy.deepcopy(profile)
        bad["sibling_sets"]["2"]=[2,3]
        with self.assertRaises(ValueError):verify_profile(bad,require_strict=True)

    def test_cgroup_throttling_is_observed_not_invented(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            folder=__import__("pathlib").Path(td)
            self.assertIsNone(cgroup_cpu_stat(folder))
            (folder/"cpu.stat").write_text("usage_usec 1200\nnr_periods 5\nnr_throttled 1\nthrottled_usec 40\n")
            row=cgroup_cpu_stat(folder)
            self.assertEqual(row["nr_throttled"],1)
            self.assertEqual(row["usage_usec"],1200)
            (folder/"cpu.stat").write_text("usage_usec 1\nusage_usec 2\n")
            with self.assertRaises(ValueError):cgroup_cpu_stat(folder)

    def test_worker_switches_from_harness_affinity_to_delegated_search_cpu(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            scope=__import__("pathlib").Path(td)
            (scope/"cpuset.cpus.effective").write_text("2-3\n")
            with patch("tools.strength_lab.e00_exec.os.sched_setaffinity") as setter, \
                 patch("tools.strength_lab.e00_exec.os.sched_getaffinity",
                       return_value={2}) as getter:
                enforce_search_cpu(2,scope)
                setter.assert_called_once_with(0,{2})
                getter.assert_called_once()
            with patch("tools.strength_lab.e00_exec.os.sched_setaffinity") as setter:
                with self.assertRaises(ResourceScopeError):
                    enforce_search_cpu(4,scope)
                setter.assert_not_called()
            (scope/"cpuset.cpus.effective").unlink()
            with self.assertRaises(ResourceScopeError):
                enforce_search_cpu(2,scope)

    def test_frozen_throttle_budget_and_claim_denials(self):
        from pathlib import Path
        import json
        root=Path(__file__).resolve().parents[2]
        doc=json.loads((root/"qualification/strength/e00-native-equivalence-v1.json").read_text())
        self.assertEqual(doc["max_participant_cpu_throttled_usec"],0)
        self.assertTrue(doc["require_cgroup_for_resource_qualification"])
        for flag in ("strength_qualified","equal_compute",
                     "live_game_authorized","release_authorized"):
            self.assertIs(doc["claims"][flag],False)

    def test_native_clock_diagnostics_are_not_equal_work_claims(self):
        row,flight=fixture()
        work=last_engine_work(["info depth 2 time 15 nodes 100 pv e2e4",
                               "info depth 3 time 20 nodes 200 pv d2d4",BEST])
        self.assertEqual(work,{"nodes":200,"time":20,"depth":3})
        metrics=summarize_clock([frontend_publication_delay_ms(flight)],
                                {"direct":[work],"wrapped":[work]})
        self.assertIs(metrics["timed_search_equivalence_qualified"],False)
        self.assertIs(metrics["clock_overhead_qualified"],False)

if __name__=="__main__":
    unittest.main()
