"""Hermetic RELEASE-1 aggregate validation: positive and adversarial regressions."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "release1_aggregate", ROOT / "scripts/qualify-release-aggregate.py"
)
aggregate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aggregate)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")


class ReleaseAggregateContracts(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.stage = self.root / "stage"
        self.oci = self.root / "oci.json"
        self.host = self.root / "host.json"
        self.smoke_dir = self.root / "smoke"
        self.os_inventory = self.root / "os.txt"
        self.wheel_inventory = self.root / "wheels.json"
        self.source = self.git_source = "a" * 40
        self.tree = self.git_tree = "b" * 40
        for name in (
            "qualification/release-image-v1.json",
            "qualification/release-qualification-v1.json",
            "qualification/license-compliance-v1.json",
        ):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / name).read_bytes())
        write_json(self.stage / "manifest.json", {
            "source_commit": self.source,
            "source_tree": self.tree,
            "release_image_policy_sha256": aggregate.digest(
                self.root / "qualification/release-image-v1.json"
            ),
        })
        write_json(self.oci, {
            "integrity_verified": True,
            "public_bot_release": False,
            "publisher_signature_verified": False,
            "oci_image_manifest_digest": "sha256:" + "c" * 64,
            "oci_config_digest": "sha256:" + "d" * 64,
            "oci_archive_sha256": "e" * 64,
        })
        write_json(self.host, {
            "test_host_observed": True,
            "capacity_met": False,
            "destination_host_qualified": False,
        })
        write_json(self.stage / "wheels/wheel-attestation.json", {
            "wheels": [{"name": "synthetic-only.whl", "sha256": "f" * 64}],
        })
        (self.stage / "wheels/runtime-wheels.lock").write_text(
            "synthetic requirements lock\n", encoding="utf-8"
        )
        self.os_inventory.write_text("synthetic packages\n", encoding="utf-8")
        write_json(self.wheel_inventory, {"synthetic": True})
        for key in ("r00-white", "r00-black", "r02-white", "r10-white"):
            write_json(self.smoke_dir / key / "result.json", {
                "offline": True, "ready": True, "runner_errors": [],
                "public_bot_release": False,
                "game": {"complete": key != "r10-white", "accepted": ["e2e4"]},
                "observations": [{"phase": "synthetic"}],
            })

    def git(self, command, **kwargs):
        self.assertEqual(command[:4], ["git", "-C", str(self.root), "rev-parse"])
        self.assertIs(kwargs.get("text"), True)
        if command[-1] == "HEAD":
            return self.git_source + "\n"
        if command[-1] == "HEAD^{tree}":
            return self.git_tree + "\n"
        raise AssertionError("unexpected git command")

    def qualify(self, **overrides):
        arguments = dict(stage=self.stage, oci=self.oci, host=self.host,
                         smoke_dir=self.smoke_dir, os_inventory=self.os_inventory,
                         wheel_inventory=self.wheel_inventory, source_sha=self.source,
                         root=self.root)
        arguments.update(overrides)
        with mock.patch.object(aggregate.subprocess, "check_output", side_effect=self.git):
            return aggregate.qualify(**arguments)

    def test_sha1_git_vs_sha256_artifacts(self):
        self.assertIsNotNone(aggregate.GIT_SHA1_HEX.fullmatch(self.source))
        for bad in ("a" * 39, "a" * 41, "a" * 64, "G" * 40, ""):
            with self.subTest(revision=bad):
                self.assertIsNone(aggregate.GIT_SHA1_HEX.fullmatch(bad))
        self.assertTrue(aggregate.is_sha256_hex("f" * 64))
        self.assertTrue(aggregate.is_sha256_hex("sha256:" + "f" * 64, prefixed=True))
        for bad, prefixed in (
            ("a" * 40, False), ("a" * 63, False), ("G" * 64, False),
            ("sha256:" + "a" * 40, True), ("a" * 64, True),
            ("sha1:" + "a" * 64, True), (None, False),
        ):
            with self.subTest(digest=bad, prefixed=prefixed):
                self.assertFalse(aggregate.is_sha256_hex(bad, prefixed=prefixed))

    def test_valid_synthetic_aggregate_cannot_promote_public_release(self):
        report = self.qualify()
        self.assertEqual(report["source_commit"], self.source)
        self.assertTrue(report["image_integrity_qualified"])
        self.assertEqual(set(report["smoke"]),
                         {"r00-white", "r00-black", "r02-white", "r10-white"})
        self.assertFalse(report["offline_candidate_qualified"])
        self.assertFalse(report["frozen_dependency_lock_qualified"])
        self.assertEqual(report["dependency_lock_checks"],
                         {"os_lock": False, "wheel_lock": False})
        self.assertTrue(report["release_blocked"])
        for key in ("license_approved", "destination_host_qualified",
                    "network_recovery_qualified", "artifact_distribution_authorized",
                    "public_bot_release", "release_promotion_authorized"):
            with self.subTest(key=key):
                self.assertIs(report[key], False)
        self.assertIn("os_lock", report["blockers"])
        self.assertIn("wheel_lock", report["blockers"])

    def test_rejects_invalid_git_length_even_if_expected_matches(self):
        for bad in ("a" * 39, "a" * 41, "a" * 64, "G" * 40):
            with self.subTest(revision=bad):
                self.git_source = bad
                with self.assertRaisesRegex(ValueError, "incorrect release source revision"):
                    self.qualify(source_sha=bad)

    def test_rejects_mismatched_git_source(self):
        with self.assertRaisesRegex(ValueError, "incorrect release source revision"):
            self.qualify(source_sha="c" * 40)

    def test_rejects_manifest_source_tree_and_policy_drift(self):
        path = self.stage / "manifest.json"
        original = json.loads(path.read_text())
        for field, invalid, error in (
            ("source_commit", "c" * 40, "candidate source/tree mismatch"),
            ("source_tree", "c" * 40, "candidate source/tree mismatch"),
            ("release_image_policy_sha256", "f" * 64, "candidate policy mismatch"),
        ):
            with self.subTest(field=field):
                bad = dict(original, **{field: invalid})
                write_json(path, bad)
                with self.assertRaisesRegex(ValueError, error):
                    self.qualify()

    def test_rejects_malformed_sha256_oci_digests(self):
        original = json.loads(self.oci.read_text())
        for field, invalid in (
            ("oci_image_manifest_digest", "sha256:" + "a" * 40),
            ("oci_config_digest", "sha256:" + "a" * 63),
            ("oci_archive_sha256", "a" * 40),
        ):
            with self.subTest(field=field):
                write_json(self.oci, dict(original, **{field: invalid}))
                with self.assertRaisesRegex(ValueError, "invalid OCI"):
                    self.qualify()

    def test_rejects_missing_release_smoke(self):
        (self.smoke_dir / "r02-white/result.json").unlink()
        with self.assertRaisesRegex(ValueError, "smoke scenario missing"):
            self.qualify()

    def test_rejects_host_and_licensing_authority_escalation(self):
        write_json(self.host, {
            "test_host_observed": True, "capacity_met": False,
            "destination_host_qualified": True
        })
        with self.assertRaisesRegex(ValueError, "host admission attempted"):
            self.qualify()
        write_json(self.host, {
            "test_host_observed": True, "capacity_met": False,
            "destination_host_qualified": False
        })
        path = self.root / "qualification/license-compliance-v1.json"
        license_doc = json.loads(path.read_text())
        license_doc["artifact_distribution_authorized"] = True
        write_json(path, license_doc)
        with self.assertRaisesRegex(ValueError, "licensing gate unexpectedly opened"):
            self.qualify()


if __name__ == "__main__":
    unittest.main()
