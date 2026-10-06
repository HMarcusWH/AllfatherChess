#!/usr/bin/env python3
"""Fail-closed tests for the legacy G3 resource reservation calibration."""
from __future__ import annotations
import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
SCRIPT = ROOT / "scripts/validate-online-hybrid-resource-calibration.py"
SPEC = importlib.util.spec_from_file_location("online_hybrid_resource_calibration", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)

CALIBRATION = ROOT / "qualification/online-hybrid-v1-resource-calibration.json"
POLICY = ROOT / "qualification/online-hybrid-authority.json"
CONFIG = ROOT / "config/allfather.online-hybrid.validation.json"

def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def dump(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")

class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.calibration = load(CALIBRATION)
        self.policy = load(POLICY)
        self.config = load(CONFIG)

    def _validate(self, *, calibration=None, policy=None, config=None):
        calibration = copy.deepcopy(self.calibration if calibration is None else calibration)
        policy = copy.deepcopy(self.policy if policy is None else policy)
        config = copy.deepcopy(self.config if config is None else config)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cp, pp, rp = root/"calibration.json", root/"policy.json", root/"runtime.json"
            dump(cp, calibration); dump(pp, policy); dump(rp, config)
            return mod.validate(cp, pp, rp)

    def test_frozen_calibration_qualifies(self):
        report = mod.validate(CALIBRATION, POLICY, CONFIG)
        self.assertTrue(report["qualified"])
        self.assertEqual(report["observed_maxima_ms"]["controller"], 217.362)
        self.assertEqual(report["observed_maxima_ms"]["explore"], 1760.0)
        self.assertEqual(report["observed_maxima_ms"]["verify"], 1290.0)
        self.assertEqual(report["explore_cpu_ms_by_owner"]["lc0"], 1800)
        self.assertEqual(report["verify_cpu_ms_by_owner"]["lc0"], 1400)

    def test_outer_envelope_cannot_be_increased(self):
        config = copy.deepcopy(self.config)
        config["budget"]["cpu_ms"] = 13000
        with self.assertRaises(mod.CalibrationError):
            self._validate(config=config)

    def test_controller_reserve_cannot_be_scaled_down(self):
        config = copy.deepcopy(self.config)
        config["budget"]["controller_overhead_reserve_ms"] = 200
        with self.assertRaises(mod.CalibrationError):
            self._validate(config=config)

    def test_lc0_explore_reservation_cannot_ignore_retained_maximum(self):
        calibration = copy.deepcopy(self.calibration)
        calibration["selection"]["explore_cpu_ms_by_owner"]["lc0"] = 1700
        with self.assertRaises(mod.CalibrationError):
            self._validate(calibration=calibration)

    def test_lc0_verify_reservation_requires_headroom_over_staged_maximum(self):
        calibration = copy.deepcopy(self.calibration)
        calibration["selection"]["verify_cpu_ms_by_owner"]["lc0"] = 1300
        with self.assertRaises(mod.CalibrationError):
            self._validate(calibration=calibration)

    def test_runtime_owner_map_drift_is_rejected(self):
        config = copy.deepcopy(self.config)
        config["routing"]["stage_cpu_ms_estimate_by_owner"]["lc0"] = 1799
        with self.assertRaises(mod.CalibrationError):
            self._validate(config=config)

    def test_policy_owner_map_drift_is_rejected(self):
        policy = copy.deepcopy(self.policy)
        policy["verification"]["reservation_cpu_ms_by_owner"]["lc0"] = 1399
        with self.assertRaises(mod.CalibrationError):
            self._validate(policy=policy)

    def test_artifact_digest_must_be_sha256(self):
        calibration = copy.deepcopy(self.calibration)
        calibration["retained_local1_sources"][0]["artifact_sha256"] = "bad"
        with self.assertRaises(mod.CalibrationError):
            self._validate(calibration=calibration)

    def test_claim_boundary_overreach_is_rejected(self):
        calibration = copy.deepcopy(self.calibration)
        calibration["claim_boundary"]["deployment"] = True
        with self.assertRaises(mod.CalibrationError):
            self._validate(calibration=calibration)

if __name__ == "__main__":
    unittest.main()
