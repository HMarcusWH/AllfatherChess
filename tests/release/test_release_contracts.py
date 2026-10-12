"""Hermetic RELEASE-1 fail-closed policy and staging controls."""
from __future__ import annotations
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def module(path,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

builder=module("scripts/build-release-candidate.py","release1_builder")
verifier=module("scripts/verify-release-candidate.py","release1_verifier")
oci=module("scripts/verify-release-oci.py","release1_oci")
policy=module("scripts/release-promotion-policy.py","release1_promotion")


class OfflineReleaseContracts(unittest.TestCase):
    def test_all_live_release_permissions_are_frozen_false(self):
        result=policy.audit()
        self.assertFalse(result["public_release_authorized"])
        self.assertFalse(result["artifact_distribution_authorized"])
        checks=[
            ("qualification/release-image-v1.json",["public_bot_release",
                 "allow_real_credentials","deployment_host_qualified"]),
            ("qualification/license-compliance-v1.json",["legal_review_completed",
                 "artifact_distribution_authorized","controller_distribution_license_approved"]),
            ("qualification/release-qualification-v1.json",
                 ["release_promotion_authorized","artifact_distribution_authorized"]),
        ]
        for path, names in checks:
            document=json.loads((ROOT/path).read_text())
            for name in names:
                self.assertIs(document[name],False)

    def test_cannot_whitelist_unauthorized_distribution_through_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            scratch=Path(temp)
            for p in ("release-image-v1.json","license-compliance-v1.json",
                      "release-qualification-v1.json"):
                target=scratch/"qualification"/p
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes((ROOT/"qualification"/p).read_bytes())
            doc=scratch/"qualification/license-compliance-v1.json"
            bad=json.loads(doc.read_text())
            bad["artifact_distribution_authorized"]=True
            doc.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError,"artifact_distribution_authorized"):
                policy.audit(scratch)

    def test_manifest_rejects_path_traversal_and_bad_digest(self):
        for name in ("allfather/../../root/.ssh/id_rsa",
                     "/opt/bridge/lib/lichess.py", "tools/../bridge/lib/config.py",
                     "bridge\\\\lib\\\\lichess.py","unlisted/file"):
            with self.subTest(path=name),self.assertRaises(ValueError):
                verifier._safe_name(name)
        with self.assertRaises(ValueError):
            oci.require(False,"missing OCI digest")

    def test_manifest_requires_packaged_policy_before_accepting_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ("allfather","bridge","tools","wheels"):
                (root/name).mkdir()
            file=root/"allfather/probe"
            file.write_text("trusted")
            result={"schema_version":1,"release_kind":"RELEASE-1-OFFLINE-CANDIDATE",
                    "public_bot_release":False,"artifact_distribution_authorized":False,
                    "network_recovery_qualified":False,"deployment_host_qualified":False,
                    "source_commit":"a"*40,"source_tree":"b"*40,"bridge_commit":"c"*40,
                    "files":[{"path":"allfather/probe","sha256":builder.digest(file),
                              "bytes":file.stat().st_size}]}
            with self.assertRaises((ValueError, OSError)):
                verifier.verify(result,verifier.stage_roots(root),installed=False,policy={})

    def test_pinned_base_and_apt_snapshot_are_not_placeholders(self):
        doc=json.loads((ROOT/"qualification/release-image-v1.json").read_text())
        self.assertRegex(doc["base_image"],r"^docker\.io/library/ubuntu@sha256:[0-9a-f]{64}$")
        self.assertRegex(doc["apt_snapshot"],r"^[0-9]{8}T[0-9]{6}Z$")
        src=(ROOT/"deploy/release/ubuntu.sources").read_text()
        self.assertEqual(src.count("Snapshot: "+doc["apt_snapshot"]),2)
        self.assertIn("Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg",src)
        self.assertFalse(doc["os_package_lock_complete"])
        self.assertFalse(doc["wheel_lock_complete"])

    def test_source_tree_staging_requires_clean_exact_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                builder.assemble(Path(temp),Path(temp)/"out",Path(temp),
                                 Path(temp),Path(temp))


if __name__=="__main__":
    unittest.main()
