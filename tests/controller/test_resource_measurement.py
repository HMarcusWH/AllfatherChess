from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from adapters.resource import ProcessSnapshot
from controller.resource_measurement import (
    ResourceMeasurementRun,
    ResourceMeasurementSettings,
)


class _Provider:
    provider_id = "linux-procfs-v1"

    def __init__(self):
        self.count = {}

    def snapshot(self, pid):
        n = self.count.get(pid, 0)
        self.count[pid] = n + 1
        return ProcessSnapshot(
            pid=pid,
            start_time_ticks=1000 + pid,
            monotonic_ns=1_000_000_000 + n * 50_000_000,
            user_cpu_ticks=10 + n * 3,
            system_cpu_ticks=5 + n,
            rss_bytes=10_000 + n * 100,
            vm_hwm_bytes=20_000 + n * 100,
        )

    def delta(self, start, end):
        from adapters.resource import LinuxProcProvider
        return LinuxProcProvider(clock_ticks=100).delta(start, end)


class ResourceMeasurementSettingsTests(unittest.TestCase):
    def test_required_cpu_cannot_be_hidden_behind_disabled_measurement(self):
        with self.assertRaises(Exception):
            ResourceMeasurementSettings.from_config(
                {
                    "enabled": False,
                    "provider": "linux-procfs-v1",
                    "require_cpu_for_claim": True,
                },
                mode="active",
            )

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(Exception):
            ResourceMeasurementSettings.from_config(
                {"enabled": True, "provider": "magic"},
                mode="active",
            )


class ResourceMeasurementRunTests(unittest.TestCase):
    def settings(self):
        return ResourceMeasurementSettings(
            enabled=True,
            provider="linux-procfs-v1",
            require_cpu_for_claim=True,
            require_gpu_for_claim=False,
            record_memory=True,
        )

    def test_stage_and_run_process_cpu_are_separate(self):
        provider = _Provider()
        run = ResourceMeasurementRun(
            run_id="r1",
            settings=self.settings(),
            provider=provider,
            controller_cpu_started_ns=0,
        )
        run.register_process(instance="stockfish-anchor", pid=11)
        run.begin_stage(key="a", instance="stockfish-anchor", phase="ANCHOR", pid=11)
        stage = run.finish_stage("a")
        self.assertIsNotNone(stage)
        self.assertTrue(stage.complete)
        self.assertGreater(stage.cpu_ms, 0)
        with tempfile.TemporaryDirectory() as tmp:
            summary = run.seal(Path(tmp) / "resource.json")
            doc = json.loads((Path(tmp) / "resource.json").read_text())
        self.assertTrue(summary["qualified"])
        self.assertTrue(doc["coverage"]["cpu"]["complete"])
        self.assertIn("stockfish-anchor", doc["processes"])
        self.assertGreater(doc["engine_cpu_ms"], 0)
        self.assertGreaterEqual(doc["physical_cpu_ms"], doc["engine_cpu_ms"])

    def test_one_instance_cannot_have_overlapping_stage_measurements(self):
        run = ResourceMeasurementRun(
            run_id="r2",
            settings=self.settings(),
            provider=_Provider(),
        )
        run.begin_stage(key="one", instance="lc0-shadow", phase="EXPLORE", pid=22)
        with self.assertRaises(Exception):
            run.begin_stage(key="two", instance="lc0-shadow", phase="VERIFY", pid=22)

    def test_unfinished_stage_fails_cpu_coverage(self):
        run = ResourceMeasurementRun(
            run_id="r3",
            settings=self.settings(),
            provider=_Provider(),
        )
        run.register_process(instance="reckless-shadow", pid=33)
        run.begin_stage(key="x", instance="reckless-shadow", phase="EXPLORE", pid=33)
        with tempfile.TemporaryDirectory() as tmp:
            summary = run.seal(Path(tmp) / "resource.json")
            doc = json.loads((Path(tmp) / "resource.json").read_text())
        self.assertFalse(summary["qualified"])
        self.assertFalse(doc["coverage"]["cpu"]["complete"])
        self.assertFalse(doc["stages"][0]["complete"])


if __name__ == "__main__":
    unittest.main()
