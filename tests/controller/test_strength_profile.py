#!/usr/bin/env python3
"""LC0 strength-facing profile and provenance tests."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.runtime import BackendManager, RuntimeError, load_runtime_config
from controller.strength_profile import (
    StrengthProfileError,
    load_json,
    validate_lock,
    validate_profile,
    validate_runtime_config,
    validate_vendor_binding,
    verify_network_file,
)
from tests.controller.test_shadow_runtime import write_shadow_config


LOCK_PATH = ROOT / "qualification" / "lc0-strength.lock.json"
PROFILE_PATH = ROOT / "qualification" / "lc0-strength-profile.json"
CONFIG_PATH = ROOT / "config" / "allfather.strength.validation.json"


class StrengthProfileStaticTests(unittest.TestCase):
    def setUp(self):
        self.lock = load_json(LOCK_PATH)
        self.profile = load_json(PROFILE_PATH)
        self.config = load_json(CONFIG_PATH)
        self.vendor = load_json(ROOT / "vendor.lock.json")

    def test_shipped_lock_is_frozen(self):
        validate_lock(self.lock)
        validate_lock(self.lock, require_frozen=True)
        self.assertEqual(self.lock["network"]["expected_size_bytes"], 18648209)

    def test_strength_lock_matches_vendor_lc0_source(self):
        validate_vendor_binding(self.lock, self.vendor)
        bad = copy.deepcopy(self.lock)
        bad["engine"]["vendor_commit"] = "0" * 40
        with self.assertRaises(StrengthProfileError):
            validate_vendor_binding(bad, self.vendor)

    def test_profile_is_explicit_and_real_backend(self):
        validate_profile(self.profile)
        self.assertNotEqual(self.profile["runtime"]["Backend"], "random")
        self.assertGreater(self.profile["runtime"]["MinibatchSize"], 0)

    def test_strength_runtime_matches_lock_and_profile(self):
        validate_runtime_config(self.config, self.lock, self.profile)
        lc0 = self.config["instances"]["lc0-shadow"]
        self.assertEqual(
            lc0["options"]["ScoreType"],
            self.config["shadow"]["lc0_score_type"],
        )
        self.assertEqual(lc0["options"]["Backend"], "blas")

    def test_random_backend_is_rejected(self):
        profile = copy.deepcopy(self.profile)
        profile["build"]["backend"] = "random"
        profile["runtime"]["Backend"] = "random"
        with self.assertRaises(StrengthProfileError):
            validate_profile(profile)

    def test_automatic_minibatch_is_rejected(self):
        profile = copy.deepcopy(self.profile)
        profile["runtime"]["MinibatchSize"] = 0
        with self.assertRaises(StrengthProfileError):
            validate_profile(profile)

    def test_empty_or_autodiscovered_weights_are_rejected_by_runtime_binding(self):
        for value in ("", "<autodiscover>"):
            with self.subTest(value=value):
                config = copy.deepcopy(self.config)
                config["instances"]["lc0-shadow"]["options"]["WeightsFile"] = value
                with self.assertRaises(StrengthProfileError):
                    validate_runtime_config(config, self.lock, self.profile)

    def test_score_type_mismatch_is_rejected_by_static_qualification(self):
        config = copy.deepcopy(self.config)
        config["shadow"]["lc0_score_type"] = "centipawn"
        with self.assertRaises(StrengthProfileError):
            validate_runtime_config(config, self.lock, self.profile)

    def test_frozen_network_file_binds_size_and_sha(self):
        payload = b"not-a-real-network-but-byte-identity-is-testable"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.pb.gz"
            path.write_bytes(payload)
            lock = copy.deepcopy(self.lock)
            lock["qualification_status"] = "frozen"
            lock["network"]["filename"] = path.name
            lock["network"]["expected_size_bytes"] = len(payload)
            lock["network"]["sha256"] = hashlib.sha256(payload).hexdigest()
            identity = verify_network_file(path, lock, require_frozen=True)
            self.assertEqual(identity["size"], len(payload))
            self.assertEqual(identity["sha256"], lock["network"]["sha256"])

            path.write_bytes(payload + b"x")
            with self.assertRaises(StrengthProfileError):
                verify_network_file(path, lock, require_frozen=True)


class ReplayNetworkIdentityTests(unittest.TestCase):
    def test_backend_manager_binds_explicit_lc0_weight_bytes_before_start(self):
        payload = b"fixture-lc0-network-bytes"
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            weights = directory / "weights.pb.gz"
            weights.write_bytes(payload)
            path = write_shadow_config(directory)
            document = json.loads(path.read_text(encoding="utf-8"))
            document["instances"]["lc0-shadow"]["options"]["WeightsFile"] = weights.name
            path.write_text(json.dumps(document), encoding="utf-8")

            manager = BackendManager.from_path(path)
            identity = manager.engine_identity["lc0-shadow"]
            recorded = identity["artifacts"]["weights"]
            self.assertEqual(recorded["path"], str(weights.resolve()))
            self.assertEqual(recorded["size"], len(payload))
            self.assertEqual(
                recorded["sha256"],
                hashlib.sha256(payload).hexdigest(),
            )


class RuntimeScoreTypeFirewallTests(unittest.TestCase):
    def test_shadow_runtime_rejects_lc0_adapter_option_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp))
            document = json.loads(path.read_text(encoding="utf-8"))
            document["instances"]["lc0-shadow"]["options"]["ScoreType"] = "WDL_mu"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_runtime_config(path)

    def test_shadow_runtime_requires_explicit_lc0_score_type_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_shadow_config(Path(tmp))
            document = json.loads(path.read_text(encoding="utf-8"))
            document["instances"]["lc0-shadow"]["options"].pop("ScoreType")
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_runtime_config(path)


if __name__ == "__main__":
    unittest.main()
