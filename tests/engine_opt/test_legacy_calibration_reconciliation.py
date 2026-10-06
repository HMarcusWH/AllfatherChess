#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.engine_opt import legacy_calibration as calibration


class CalibrationReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.calibration = calibration.load(calibration.CALIBRATION)
        self.observations = calibration.load(calibration.OBSERVATIONS)

    def test_reconciled_evidence_reproduces_selected_reservations(self):
        report = calibration.validate()
        self.assertTrue(report["qualified"])
        self.assertEqual(
            report["observed_maxima_ms"],
            {
                "controller": 217.362,
                "explore": 1760.0,
                "verify": 1290.0,
            },
        )
        self.assertEqual(report["explore_cpu_ms_by_owner"]["lc0"], 1800)
        self.assertEqual(report["verify_cpu_ms_by_owner"]["lc0"], 1400)

    def test_first_explore_count_is_corrected_not_imputed(self):
        first = self.observations["sources"][0]
        self.assertEqual(
            first["series"]["lc0_explore_cpu_ms"]["summary"]["samples"],
            689,
        )
        self.assertEqual(len(first["exclusions"]), 1)
        self.assertFalse(first["campaign_passed"])
        self.assertFalse(self.observations["sources"][1]["campaign_passed"])

    def _validate_calibration(self, document):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calibration.json"
            path.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return calibration.validate(
                path,
                calibration.POLICY,
                calibration.CONFIG,
                calibration.OBSERVATIONS,
            )

    def test_well_formed_wrong_artifact_or_source_identity_is_rejected(self):
        for key, value in (
            ("artifact_sha256", "0" * 64),
            ("source_commit", "0" * 40),
        ):
            document = copy.deepcopy(self.calibration)
            document["retained_local1_sources"][0][key] = value
            with self.subTest(key=key), self.assertRaises(
                calibration.CalibrationError
            ):
                self._validate_calibration(document)

    def test_sample_count_and_summary_tampering_are_rejected(self):
        for key, value in (
            ("samples", 692),
            ("max", 1799.0),
            ("median", 1010.0),
        ):
            document = copy.deepcopy(self.calibration)
            document["retained_local1_sources"][0]["observed"][
                "lc0_explore_cpu_ms"
            ][key] = value
            with self.subTest(key=key), self.assertRaises(
                calibration.CalibrationError
            ):
                self._validate_calibration(document)

    def test_observation_file_cannot_be_rebound_after_tampering(self):
        observations = copy.deepcopy(self.observations)
        observations["sources"][0]["series"][
            "lc0_explore_cpu_ms"
        ]["summary"]["max"] = 1799.0
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            op = root / "observations.json"
            op.write_text(
                json.dumps(observations, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            document = copy.deepcopy(self.calibration)
            document["reconciliation"]["sha256"] = calibration.sha(op)
            cp = root / "calibration.json"
            cp.write_text(
                json.dumps(document, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(calibration.CalibrationError):
                calibration.validate(
                    cp,
                    calibration.POLICY,
                    calibration.CONFIG,
                    op,
                )

    def test_measurement_scope_and_failed_campaign_boundary_are_frozen(self):
        observations = copy.deepcopy(self.observations)
        observations["extraction"]["controller"] = "physical process CPU"
        with tempfile.TemporaryDirectory() as tmp:
            op = Path(tmp) / "observations.json"
            op.write_text(json.dumps(observations), encoding="utf-8")
            with self.assertRaises(calibration.CalibrationError):
                calibration.validate(
                    calibration.CALIBRATION,
                    calibration.POLICY,
                    calibration.CONFIG,
                    op,
                )

        observations = copy.deepcopy(self.observations)
        observations["sources"][0]["campaign_passed"] = True
        with tempfile.TemporaryDirectory() as tmp:
            op = Path(tmp) / "observations.json"
            op.write_text(json.dumps(observations), encoding="utf-8")
            with self.assertRaises(calibration.CalibrationError):
                calibration.validate(
                    calibration.CALIBRATION,
                    calibration.POLICY,
                    calibration.CONFIG,
                    op,
                )

    def test_fake_archive_fails_before_producer_metadata_is_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fake.zip"
            with zipfile.ZipFile(path, "w") as handle:
                handle.writestr("manifest.json", '{"passed":true}')
            with self.assertRaises(calibration.CalibrationError):
                calibration.extract_source(
                    path,
                    self.calibration["retained_local1_sources"][0],
                )

    def test_percentiles_use_declared_interpolation(self):
        self.assertEqual(
            calibration.summarize([1, 2, 3, 4])["p95"],
            3.85,
        )
        for values in ([True], [float("nan")], [-1], []):
            with self.subTest(values=values), self.assertRaises(
                calibration.CalibrationError
            ):
                calibration.summarize(values)


if __name__ == "__main__":
    unittest.main()
