#!/usr/bin/env python3
"""Contract tests for immutable runtime-substrate identity."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.resource_profiles import OrchestrationContractError
from controller.runtime_substrate import RuntimeSubstrate


def substrate(**overrides) -> RuntimeSubstrate:
    values = {
        "os_id": "ubuntu",
        "os_version_id": "24.04",
        "kernel_release": "6.17.0-1022-azure",
        "architecture": "x86_64",
        "libc_name": "glibc",
        "libc_version": "2.39",
        "python_version": "3.12.3",
        "runner_image_os": "ubuntu24",
        "runner_image_version": "20260920.314.1",
        "openblas_package": "libopenblas-dev=0.3.26+ds-1",
        "clock_ticks_per_second": 100,
    }
    values.update(overrides)
    return RuntimeSubstrate.from_observation(**values)


class RuntimeSubstrateTests(unittest.TestCase):
    def test_round_trip_and_digest(self):
        item = substrate()
        restored = RuntimeSubstrate.from_dict(item.as_dict())
        self.assertEqual(restored, item)
        self.assertEqual(restored.digest, item.digest)
        self.assertTrue(item.substrate_id.startswith("runtime-substrate/"))

    def test_openblas_or_kernel_change_changes_identity(self):
        base = substrate()
        self.assertNotEqual(
            base.digest,
            substrate(openblas_package="libopenblas-dev=0.3.27").digest,
        )
        self.assertNotEqual(
            base.digest,
            substrate(kernel_release="6.17.0-1023-azure").digest,
        )

    def test_missing_runner_image_or_openblas_is_explicitly_incomplete(self):
        item = substrate(openblas_package=None)
        self.assertFalse(item.complete)
        item = substrate(runner_image_version=None)
        self.assertFalse(item.complete)

    def test_bool_clock_tick_and_authority_escalation_are_rejected(self):
        with self.assertRaises(OrchestrationContractError):
            substrate(clock_ticks_per_second=True)
        raw = substrate().as_dict()
        tampered = copy.deepcopy(raw)
        tampered["authority"]["outward_move"] = True
        with self.assertRaises(OrchestrationContractError):
            RuntimeSubstrate.from_dict(tampered)

    def test_complete_flag_cannot_be_forged(self):
        raw = substrate().as_dict()
        raw["openblas_package"] = None
        raw["complete"] = True
        with self.assertRaises(OrchestrationContractError):
            RuntimeSubstrate.from_dict(raw)


if __name__ == "__main__":
    unittest.main()
