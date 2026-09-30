#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from adapters.resource.linux_affinity import LinuxAffinityError
from tools.resource_lab.observe import (
    AffinityObservation,
    ResourceLabObservationError,
    observe_affinity,
    process_cpu_scope,
)


class FakeTree:
    def __init__(self,pid,start):
        self.root_start_time_ticks=start
        self.pid=pid
    def as_dict(self):
        return {
            "root_pid":self.pid,
            "root_start_time_ticks":self.root_start_time_ticks,
            "passes":1,
            "enforced":False,
            "tasks":[],
        }


class FakeProvider:
    provider_id="fake"
    def __init__(self,failures=0,root_alive=True):
        self.failures=failures
        self.root_alive=root_alive
        self.calls=0
    def process_start_time(self,pid):
        if not self.root_alive:
            raise LinuxAffinityError("root gone")
        return 10
    def inspect_tree_affinity(self,pid):
        self.calls+=1
        if self.calls<=self.failures:
            raise LinuxAffinityError("transient TID disappeared")
        return FakeTree(pid,10)


class ObserveTests(unittest.TestCase):
    def test_transient_tid_race_is_retried_and_retained(self):
        result=observe_affinity(FakeProvider(failures=1),123,max_attempts=3)
        self.assertEqual(result.status,"completed")
        self.assertEqual(result.attempts,2)
        self.assertEqual(len(result.faults),1)
        self.assertFalse(result.observation["enforced"])

    def test_exhausted_tid_races_are_incomplete_not_fatal(self):
        result=observe_affinity(FakeProvider(failures=99),123,max_attempts=3)
        self.assertEqual(result.status,"incomplete")
        self.assertIsNone(result.observation)
        self.assertEqual(len(result.faults),3)

    def test_root_disappearance_is_fatal(self):
        with self.assertRaises(ResourceLabObservationError):
            observe_affinity(FakeProvider(root_alive=False),123,max_attempts=3)


    def test_process_cpu_scope_rejects_separate_child_process(self):
        before=AffinityObservation(
            policy="bounded-observed-affinity-v1",
            status="completed",
            root_pid=100,
            root_start_time_ticks=10,
            attempts=1,
            observation={
                "root_pid":100,
                "root_start_time_ticks":10,
                "passes":1,
                "enforced":False,
                "tasks":[
                    {"pid":100,"process_start_time_ticks":10,"tid":100,"task_start_time_ticks":10,"cpus":[0]},
                    {"pid":200,"process_start_time_ticks":20,"tid":200,"task_start_time_ticks":20,"cpus":[1]},
                ],
            },
            faults=(),
        )
        after=AffinityObservation(
            policy="bounded-observed-affinity-v1",
            status="completed",
            root_pid=100,
            root_start_time_ticks=10,
            attempts=1,
            observation={
                "root_pid":100,
                "root_start_time_ticks":10,
                "passes":1,
                "enforced":False,
                "tasks":[
                    {"pid":100,"process_start_time_ticks":10,"tid":100,"task_start_time_ticks":10,"cpus":[0]},
                ],
            },
            faults=(),
        )
        scope=process_cpu_scope(before,after)
        self.assertFalse(scope["complete"])
        self.assertEqual(scope["child_process_ids"],[200])
        self.assertIn("separate-child-process-observed",scope["reasons"])

    def test_process_cpu_scope_requires_complete_boundary_observations(self):
        completed=AffinityObservation(
            policy="bounded-observed-affinity-v1",
            status="completed",
            root_pid=100,
            root_start_time_ticks=10,
            attempts=1,
            observation={
                "root_pid":100,
                "root_start_time_ticks":10,
                "passes":1,
                "enforced":False,
                "tasks":[],
            },
            faults=(),
        )
        incomplete=AffinityObservation(
            policy="bounded-observed-affinity-v1",
            status="incomplete",
            root_pid=100,
            root_start_time_ticks=10,
            attempts=3,
            observation=None,
            faults=("transient task race",),
        )
        scope=process_cpu_scope(incomplete,completed)
        self.assertFalse(scope["complete"])
        self.assertIn("before-affinity-observation-incomplete",scope["reasons"])

if __name__=="__main__":
    unittest.main()
