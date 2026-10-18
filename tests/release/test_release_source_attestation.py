"""Real Git-byte and staged-manifest authorization tests for RELEASE-1."""
from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location(
    "release1_attested_aggregate",ROOT/"scripts/qualify-release-aggregate.py")
aggregate=importlib.util.module_from_spec(spec)
spec.loader.exec_module(aggregate)


class ReleaseVerifierSourceAttestationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix="release-source-contract-")
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.git("init","-q")
        self.git("config","user.email","local-only@example.test")
        self.git("config","user.name","Offline Source Test")
        self.source_paths=aggregate.VERDICT_SOURCE_PATHS
        for relative in self.source_paths:
            target=self.root/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes((ROOT/relative).read_bytes())
        self.git("add",".")
        self.git("commit","-qm","bind exact-source")
        self.source=self.git("rev-parse","HEAD",capture=True)
        self.attestation={"source_attestation":{
            relative:hashlib.sha256((self.root/relative).read_bytes()).hexdigest()
            for relative in self.source_paths}}

    def git(self,*args,capture=False):
        command=["git","-C",str(self.root),*args]
        result=subprocess.run(command,check=True,capture_output=True,
                              text=True,timeout=20)
        return result.stdout.strip() if capture else None

    def test_clean_source_loads_from_sealed_snapshot(self):
        with aggregate.verified_release_snapshot(
                self.root,self.source,self.attestation) as sealed:
            http=aggregate.load_verified_module(
                sealed,"scripts/release_http_contract.py","sealed_http_positive")
            row={"method":"GET","path":"/api/account","status":200,
                 "request_target":"/api/account","target_canonical":True}
            self.assertTrue(http.allowed_release_http(row,"r00-white"))
            smoke=aggregate.load_verified_module(
                sealed,"scripts/qualify-release-smoke.py","sealed_smoke_positive")
            self.assertTrue(callable(smoke.verify_file))
            self.assertNotEqual(sealed.resolve(),self.root.resolve())

    def test_isolated_git_worktree_preserves_original_committed_verifier_bytes(self):
        # A build step can mutate the producer checkout while preserving HEAD.
        # A new detached worktree must execute Git's original bytes instead.
        with tempfile.TemporaryDirectory(prefix="release1-isolated-") as td:
            verified=Path(td)/"verified"
            self.git("worktree","add","--detach",str(verified),self.source)
            victim="scripts/qualify-release-smoke.py"
            original=(verified/victim).read_bytes()
            (self.root/victim).write_bytes(b"raise SystemExit('FORGED')\n")
            self.assertEqual((verified/victim).read_bytes(),original)
            with self.assertRaisesRegex(ValueError,"staged attestation"):
                with aggregate.verified_release_snapshot(
                        self.root,self.source,self.attestation):
                    self.fail("mutable producer source was accepted")
            with aggregate.verified_release_snapshot(
                    verified,self.source,self.attestation) as sealed:
                self.assertEqual((sealed/victim).read_bytes(),original)

    def test_worktree_mutation_cannot_reuse_old_manifest_and_head(self):
        path=self.root/"scripts/qualify-release-smoke.py"
        path.write_bytes(path.read_bytes()+b"\n# PRIVATE_CANARY\n")
        with self.assertRaisesRegex(ValueError,"staged attestation"):
            with aggregate.verified_release_snapshot(
                    self.root,self.source,self.attestation):
                self.fail("mutated source accepted")

    def test_rehashed_mutation_cannot_forge_git_source_identity(self):
        relative="scripts/qualify-release-smoke.py"
        path=self.root/relative
        path.write_bytes(path.read_bytes()+b"\n# altered verifier\n")
        self.attestation["source_attestation"][relative]=hashlib.sha256(
            path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,"Git committed bytes"):
            with aggregate.verified_release_snapshot(
                    self.root,self.source,self.attestation):
                self.fail("rehashed forged source accepted")

    def test_transitive_http_policy_is_also_exactly_attested(self):
        relative="tests/lichess/http_target_contract.py"
        path=self.root/relative
        path.write_bytes(path.read_bytes()+b"\n# altered HTTP policy\n")
        self.attestation["source_attestation"][relative]=hashlib.sha256(
            path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,"Git committed bytes"):
            with aggregate.verified_release_snapshot(
                    self.root,self.source,self.attestation):
                self.fail("changed indirect helper accepted")

    def test_missing_and_malformed_manifest_attestation_fails(self):
        self.attestation["source_attestation"].pop(
            "scripts/qualify-release-aggregate.py")
        with self.assertRaisesRegex(ValueError,"staged attestation"):
            with aggregate.verified_release_snapshot(
                    self.root,self.source,self.attestation):
                self.fail("missing self attestation accepted")

    def test_symlink_replacement_fails_closed(self):
        target=self.root/"scripts/qualify-release-smoke.py"
        backup=self.root/"untrusted-source.py"
        backup.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(backup)
        with self.assertRaisesRegex(ValueError,"linked or outside"):
            with aggregate.verified_release_snapshot(
                    self.root,self.source,self.attestation):
                self.fail("linked source accepted")

    def test_original_mutation_after_snapshot_does_not_change_executed_bytes(self):
        relative="scripts/qualify-release-smoke.py"
        with aggregate.verified_release_snapshot(
                self.root,self.source,self.attestation) as sealed:
            original=(sealed/relative).read_bytes()
            (self.root/relative).write_bytes(b"raise SystemExit('CANARY')\n")
            self.assertEqual((sealed/relative).read_bytes(),original)
            smoke=aggregate.load_verified_module(
                sealed,relative,"sealed_smoke_after_mutation")
            self.assertTrue(callable(smoke.verify_file))

    def test_aggregate_verifier_itself_must_match_git_commit(self):
        relative="scripts/qualify-release-aggregate.py"
        path=self.root/relative
        path.write_bytes(path.read_bytes()+b"\n# self mutation\n")
        self.attestation["source_attestation"][relative]=hashlib.sha256(
            path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,"Git committed bytes"):
            with aggregate.verified_release_snapshot(
                    self.root,self.source,self.attestation):
                self.fail("changed aggregate entrypoint accepted")


if __name__=="__main__":
    unittest.main()
