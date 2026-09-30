#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from adapters.resource.linux_affinity import LinuxAffinityError
from tools.resource_lab.observe import (
    ResourceLabObservationError,
    observe_affinity,
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


if __name__=="__main__":
    unittest.main()
