#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from adapters.resource.linux_proc import ProcessSnapshot
from tools.resource_lab.candidate_matrix import Candidate
from tools.resource_lab.measure import (
    parse_search_observation,
    physical_primitives,
    reconstruct_physical_measurement,
)
from tools.resource_lab.process_cpu import ProcessCpuClockEvidence
from tools.resource_lab.qualify import (
    ResourceLabQualificationError,
    _reconstruct_measurement,
    validate_attempt_order,
)


class QualifyAttemptTests(unittest.TestCase):
    def test_exact_blocked_attempt_order_passes(self):
        expected=(
            {
                "candidate_id":"a","repeat_index":0,"case_id":"c0",
                "block_index":0,"order_index":0,"attempt_ordinal":0,
            },
            {
                "candidate_id":"b","repeat_index":0,"case_id":"c0",
                "block_index":0,"order_index":1,"attempt_ordinal":1,
            },
        )
        rows=[
            {**expected[0],"attempt_index":0},
            {**expected[1],"attempt_index":0},
        ]
        validate_attempt_order(
            rows,expected,identity_field="candidate_id",label="Stage-A"
        )

    def test_reordered_attempt_fails(self):
        expected=(
            {
                "candidate_id":"a","repeat_index":0,"case_id":"c0",
                "block_index":0,"order_index":0,"attempt_ordinal":0,
            },
            {
                "candidate_id":"b","repeat_index":0,"case_id":"c0",
                "block_index":0,"order_index":1,"attempt_ordinal":1,
            },
        )
        rows=[
            {**expected[1],"attempt_index":0},
            {**expected[0],"attempt_index":0},
        ]
        with self.assertRaises(ResourceLabQualificationError):
            validate_attempt_order(
                rows,expected,identity_field="candidate_id",label="Stage-A"
            )

    def test_missing_attempt_fails(self):
        expected=(
            {
                "candidate_id":"a","repeat_index":0,"case_id":"c0",
                "block_index":0,"order_index":0,"attempt_ordinal":0,
            },
            {
                "candidate_id":"b","repeat_index":0,"case_id":"c0",
                "block_index":0,"order_index":1,"attempt_ordinal":1,
            },
        )
        with self.assertRaises(ResourceLabQualificationError):
            validate_attempt_order(
                [{**expected[0],"attempt_index":0}],
                expected,
                identity_field="candidate_id",
                label="Stage-A",
            )

    def test_retry_index_fails_even_without_duplicate_row(self):
        expected=(
            {
                "candidate_id":"a","repeat_index":0,"case_id":"c0",
                "block_index":0,"order_index":0,"attempt_ordinal":0,
            },
        )
        with self.assertRaises(ResourceLabQualificationError):
            validate_attempt_order(
                [{**expected[0],"attempt_index":1}],
                expected,
                identity_field="candidate_id",
                label="Stage-A",
            )

    def test_tampered_order_metadata_fails(self):
        expected=(
            {
                "composition_id":"c0","repeat_index":0,"case_id":"x",
                "block_index":0,"order_index":0,"attempt_ordinal":0,
            },
        )
        row={**expected[0],"attempt_index":0,"block_index":7}
        with self.assertRaises(ResourceLabQualificationError):
            validate_attempt_order(
                [row],expected,identity_field="composition_id",label="Stage-B"
            )



    def test_producer_physical_metric_tampering_is_rejected(self):
        candidate=Candidate(
            family="stockfish",
            variant="v",
            nodes=16,
            binary_relpath="bin/x",
            network_relpath=None,
            binary_sha256="1"*64,
            network_sha256=None,
            args=(),
            environment=(),
            options=(),
            warmup_nodes=None,
            reference=True,
        )
        before=ProcessSnapshot(
            pid=42,start_time_ticks=9,monotonic_ns=1_000_000_000,
            user_cpu_ticks=10,system_cpu_ticks=2,rss_bytes=100,vm_hwm_bytes=110,
        )
        after=ProcessSnapshot(
            pid=42,start_time_ticks=9,monotonic_ns=1_002_000_000,
            user_cpu_ticks=10,system_cpu_ticks=2,rss_bytes=120,vm_hwm_bytes=130,
        )
        clock=ProcessCpuClockEvidence(
            method="posix-process-cpu-clock-v1",pid=42,resolution_ns=1,
            before_ns=10_000,after_ns=510_000,
        )
        primitives=physical_primitives(before,before,after,clock)
        physical=reconstruct_physical_measurement(
            primitives,
            clock_ticks_per_second=100,
            required_cpu_method="posix-process-cpu-clock-v1",
            max_cpu_resolution_ns=1_000_000,
        )
        transcript=["<< info nodes 16 score cp 1 pv e2e4","<< bestmove e2e4"]
        measurement=parse_search_observation(
            transcript,family="stockfish",physical=physical
        ).as_dict()
        affinity={
            "policy":"bounded-observed-affinity-v1",
            "status":"incomplete",
            "root_pid":42,
            "root_start_time_ticks":9,
            "attempts":3,
            "observation":None,
            "faults":["transient task race"],
            "authority":{
                "resource_context":True,
                "resource_authorization":False,
                "outward_move":False,
            },
        }
        row={
            "measurement":dict(measurement),
            "physical_primitives":primitives,
            "process_cpu_scope":{
                "complete":False,
                "root_pid":42,
                "root_start_time_ticks":9,
                "observed_process_ids":[42],
                "child_process_ids":[],
                "reasons":[
                    "before-affinity-observation-incomplete",
                    "after-affinity-observation-incomplete",
                ],
            },
            "transcript":transcript,
            "affinity_observation_before":affinity,
            "affinity_observation_after":affinity,
        }
        rebuilt,faults=_reconstruct_measurement(
            row,
            candidate=candidate,
            clock_ticks_per_second=100,
            cpu_policy={
                "required_method":"posix-process-cpu-clock-v1",
                "max_resolution_ns":1_000_000,
            },
            affinity_policy={
                "policy":"bounded-observed-affinity-v1",
                "max_attempts":3,
            },
            label="synthetic",
        )
        self.assertEqual(rebuilt,measurement)
        self.assertEqual(faults,2)
        row["measurement"]["cpu_ms"]=999
        with self.assertRaises(ResourceLabQualificationError):
            _reconstruct_measurement(
                row,
                candidate=candidate,
                clock_ticks_per_second=100,
                cpu_policy={
                    "required_method":"posix-process-cpu-clock-v1",
                    "max_resolution_ns":1_000_000,
                },
                affinity_policy={
                    "policy":"bounded-observed-affinity-v1",
                    "max_attempts":3,
                },
                label="synthetic",
            )

if __name__=="__main__":
    unittest.main()
