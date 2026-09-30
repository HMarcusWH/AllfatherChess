#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from tools.resource_lab.process_cpu import (
    ProcessCpuClock,
    ProcessCpuClockError,
    ProcessCpuClockEvidence,
)


class ProcessCpuClockTests(unittest.TestCase):
    def test_default_linux_clock_reads_a_live_child_process(self):
        child=subprocess.Popen([
            sys.executable,
            "-c",
            "import time; end=time.monotonic()+2; x=0\n"
            "while time.monotonic()<end: x+=1",
        ])
        try:
            clock=ProcessCpuClock(child.pid,max_resolution_ns=1_000_000)
            before=clock.sample_ns()
            time.sleep(0.05)
            after=clock.sample_ns()
            self.assertGreater(after,before)
            self.assertLessEqual(clock.resolution_ns,1_000_000)
        finally:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)

    def test_high_resolution_clock_records_exact_delta(self):
        samples=iter([1_000_000,1_350_000])
        clock=ProcessCpuClock(
            42,
            max_resolution_ns=1_000_000,
            clock_id_factory=lambda pid: 99,
            clock_gettime_ns=lambda clock_id: next(samples),
            clock_getres=lambda clock_id: 1e-9,
        )
        before=clock.sample_ns()
        after=clock.sample_ns()
        evidence=clock.evidence(before,after)
        self.assertEqual(evidence.delta_ns,350_000)
        self.assertEqual(evidence.resolution_ns,1)

    def test_excessive_resolution_fails_closed(self):
        with self.assertRaises(ProcessCpuClockError):
            ProcessCpuClock(
                42,
                max_resolution_ns=1_000_000,
                clock_id_factory=lambda pid: 99,
                clock_gettime_ns=lambda clock_id: 1,
                clock_getres=lambda clock_id: 0.01,
            )

    def test_regressing_clock_evidence_is_rejected(self):
        with self.assertRaises(ProcessCpuClockError):
            ProcessCpuClockEvidence(
                method="posix-process-cpu-clock-v1",
                pid=1,
                resolution_ns=1,
                before_ns=100,
                after_ns=99,
            )

    def test_wrong_method_is_rejected(self):
        with self.assertRaises(ProcessCpuClockError):
            ProcessCpuClockEvidence(
                method="procfs-jiffies",
                pid=1,
                resolution_ns=1,
                before_ns=1,
                after_ns=2,
            )


if __name__=="__main__":
    unittest.main()
