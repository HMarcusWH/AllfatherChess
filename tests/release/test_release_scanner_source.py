"""Bounded registry candidate identity and source-pin negative controls."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[2]


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


source=module("release1_scanner_source","scripts/prepare-release-scanner.py")
ci=module("release1_scanner_ci","scripts/ci-release-scanner.py")


class ReleaseScannerSourceContractTests(unittest.TestCase):
    def test_frozen_patch_matches_reviewed_digest(self):
        policy=json.loads((ROOT/"qualification/release-scanner-v1.json").read_text())
        source.check_lock(policy)
        self.assertEqual(
            source.sha256(ROOT/policy["patch_path"]),
            "87fbe492c4b504374b41a6303ebc75833559bacb83addf9e5520b9768cc37d09")
        self.assertEqual(policy["upstream_commit"],
            "d88056b4e5b61d0ca037340df91be47d343b4386")
        self.assertEqual(policy["toolchain_version"],"go1.25.8")
        self.assertEqual(policy["vendored_syft_version"],"v1.42.3")

    def test_mutated_patch_cannot_keep_approved_digest(self):
        original=source.ROOT
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/"deploy/release/scanner"
            target.mkdir(parents=True)
            (target/"target.go.patch").write_text("PRIVATE_CANARY\n")
            policy=json.loads(source.LOCK.read_text())
            with mock.patch.object(source,"ROOT",Path(td)):
                with self.assertRaisesRegex(ValueError,"patch bytes"):
                    source.check_lock(policy)

    def test_unreviewed_source_and_authority_changes_fail(self):
        source_policy=json.loads(source.LOCK.read_text())
        for field,value in (
            ("upstream_commit","a"*40),
            ("toolchain_version","go1.26.3"),
            ("vendored_syft_version","v1.51.0"),
            ("public_bot_release",True),
            ("required_file_hash_algorithms",["SHA256"]),
        ):
            with self.subTest(field=field):
                changed=dict(source_policy,**{field:value})
                with self.assertRaisesRegex(ValueError,"source/build policy"):
                    source.check_lock(changed)

    def test_candidate_approval_requires_frozen_independent_digests(self):
        with tempfile.TemporaryDirectory() as td:
            folder=Path(td)
            candidate={
                "schema_version":1,
                "upstream_commit":"d88056b4e5b61d0ca037340df91be47d343b4386",
                "binary_sha256":"b"*64,
                "manifest_digest":"sha256:"+"a"*64,
                "image_reproducible":True,
                "binary_reproducible":True}
            (folder/"scanner-candidate.json").write_text(json.dumps(candidate))
            with mock.patch.object(ci,"OUT",folder):
                with self.assertRaisesRegex(ValueError,"not yet frozen"):
                    ci.verify_approval()
                # These approvals remain separate from build-generated claims.
                frozen=dict(json.loads(ci.LOCK.read_text()),
                            approved_binary_sha256="b"*64,
                            approved_manifest_digest="sha256:"+"a"*64)
                with mock.patch.object(ci,"LOCK",folder/"reviewed.json"):
                    (folder/"reviewed.json").write_text(json.dumps(frozen))
                    ci.verify_approval()
                    candidate["manifest_digest"]="sha256:"+"c"*64
                    (folder/"scanner-candidate.json").write_text(json.dumps(candidate))
                    with self.assertRaisesRegex(ValueError,"not yet frozen"):
                        ci.verify_approval()

    def test_registry_local_digest_is_full_sha256_manifest_identity(self):
        manifest=json.dumps({"schemaVersion":2,"mediaType":
          "application/vnd.docker.distribution.manifest.v2+json",
          "config":{},"layers":[]}).encode()
        expected="sha256:"+hashlib.sha256(manifest).hexdigest()
        class Response:
            status=200
            headers={"Content-Type":
                     "application/vnd.docker.distribution.manifest.v2+json",
                     "Docker-Content-Digest":expected}
            def read(self,amount):
                return manifest
            def __enter__(self): return self
            def __exit__(self,*args): pass
        with mock.patch.object(ci,"urlopen",return_value=Response()):
            self.assertEqual(ci.response_digest("candidate-1"),expected)
        with mock.patch.object(ci,"urlopen",return_value=Response()):
            Response.headers=dict(Response.headers,
                **{"Docker-Content-Digest":"sha256:"+"0"*64})
            with self.assertRaisesRegex(ValueError,"hash"):
                ci.response_digest("candidate-2")


if __name__=="__main__":
    unittest.main()
