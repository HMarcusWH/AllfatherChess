"""Independent negative oracles for source closure and verdict implications."""
from __future__ import annotations
import copy
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def load(path,ident):
    spec=importlib.util.spec_from_file_location(ident,ROOT/path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

closure=load("scripts/verify_source_closure.py","assurance_closure")
claims=load("scripts/verify_assurance_contract.py","assurance_claims")
sanitizer=load("scripts/sanitize_qualification_evidence.py","assurance_safe_output")


class AssuranceContractTests(unittest.TestCase):
    def test_all_positive_claims_have_explicit_proof_requirements(self):
        p=claims.validate_contract()
        self.assertFalse(p["public_bot_release"])
        self.assertFalse(p["authority"]["public_distribution"])
        for key,entry in p["claims"].items():
            self.assertTrue(entry["requires"],key)
            self.assertTrue(entry["does_not_imply"],key)

    def test_exact_recovery_source_closure_is_transitive(self):
        for profile in ("online-recovery-v1","online-recovery-v2"):
            with self.subTest(profile=profile):
                self.assertTrue(closure.verify_profile(profile)[
                    "source_closure_verified"])
        policy=json.loads((ROOT/"qualification/online-recovery-v2.json").read_text())
        names=set(policy["attested_paths"])
        for name in ("tests/lichess/fault_plan.py",
                     "deploy/lichess/run_recovery.py",
                     "deploy/lichess/log_capture.py",
                     "deploy/lichess/Dockerfile.offline",
                     "scripts/verify_source_closure.py",
                     "qualification/assurance-contract-v1.json"):
            self.assertIn(name,names)

    def test_git_committed_bytes_catch_mutated_executable_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            subprocess.run(["git","-C",str(root),"init","-q"],
                           check=True,capture_output=True)
            subprocess.run(["git","-C",str(root),"config",
                            "user.email","offline@example.test"],check=True)
            subprocess.run(["git","-C",str(root),"config",
                            "user.name","Offline Test"],check=True)
            source=root/"deploy/lichess/fault_plan.py"
            source.parent.mkdir(parents=True)
            source.write_text("STATE='attested'\n")
            subprocess.run(["git","-C",str(root),"add","."],check=True)
            subprocess.run(["git","-C",str(root),"commit","-qm","freeze"],
                           check=True)
            sha=subprocess.check_output(
                ["git","-C",str(root),"rev-parse","HEAD"],text=True).strip()
            path="deploy/lichess/fault_plan.py"
            closure.verify_committed_bytes(sha,[path],root)
            source.write_text("STATE='tampered'\n")
            with self.assertRaisesRegex(ValueError,
                                        "executed source differs from Git"):
                closure.verify_committed_bytes(sha,[path],root)

    def test_dynamic_executable_inputs_cannot_be_dropped_from_attestation(self):
        dynamic={
            "qualification/http-evidence-contract-v1.json",
            "tests/fixtures/lichess/recovery_scenarios.json",
            "deploy/lichess/bridge.lock.json",
        }
        for profile,policy in (
            ("online-recovery-v1","qualification/online-recovery-v1.json"),
            ("online-recovery-v2","qualification/online-recovery-v2.json"),
        ):
            required=closure.expected_sources(profile)
            self.assertTrue(dynamic.issubset(required))
            self.assertIn(policy,required)
            original=closure.PROFILES[profile]["inputs"]
            try:
                # A producer-controlled profile cannot shrink independent
                # expected imports/dynamic contract inputs.
                config=closure.PROFILES[profile]
                self.assertTrue(set(original).issubset(required))
                self.assertTrue(closure.verify_profile(profile)[
                    "source_closure_verified"])
            finally:
                closure.PROFILES[profile]["inputs"]=original

    def test_dependency_parser_catches_unattested_nested_import(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            target=root/"deploy/lichess"
            target.mkdir(parents=True)
            a=target/"root.py"
            b=target/"nested.py"
            a.write_text("from deploy.lichess import nested\n")
            b.write_text("VALUE=1\n")
            deps=closure.imported_sources(a,root)
            self.assertIn("deploy/lichess/nested.py",deps)
            b.unlink()
            self.assertNotIn("deploy/lichess/nested.py",
                             closure.imported_sources(a,root))

    def test_contradictory_online4b_claims_are_rejected(self):
        good={
            "public_bot_release":False, "source_commit":"a"*40,
            "network_recovery_qualified":False,
            "harness_qualified":True,"required_run_count":1,
            "validated_run_count":1,"qualification_failures":[],
            "scenario_results":[{"harness_valid":True,"scenario":"R00",
                                 "status":"NORMAL_COMPLETION"}],
            "containment_qualified_offline":True,
            "recovery_qualified_offline":False,
        }
        claims.verify_report(good,"online4b")
        forged={**good,"recovery_qualified_offline":True}
        with self.assertRaisesRegex(ValueError,"missing fresh recovery witness"):
            claims.verify_report(forged,"online4b")
        corrupt={**good,"scenario_results":[{"harness_valid":True,
                                              "status":"UNSAFE_WITNESS"}]}
        with self.assertRaisesRegex(ValueError,"unsafe or unverified"):
            claims.verify_report(corrupt,"online4b")

    def test_release_promotion_cannot_be_inferred_from_dependency_locks(self):
        report={
            "public_bot_release":False,
            "network_recovery_qualified":False,
            "artifact_distribution_authorized":False,
            "release_blocked":True,"release_promotion_authorized":False,
            "license_approved":False,"destination_host_qualified":False,
            "image_integrity_qualified":False,
            "frozen_dependency_lock_qualified":True,
            "offline_candidate_qualified":False,
            "blockers":["controller_license_review"],
        }
        claims.verify_report(report,"release_aggregate")
        forged={**report,"offline_candidate_qualified":True}
        with self.assertRaisesRegex(ValueError,"authorization prerequisites"):
            claims.verify_report(forged,"release_aggregate")

    def test_four_games_without_oci_provenance_do_not_grant_image_integrity(self):
        complete={key:{"passed":True,
                       "independent_evidence":{"independent_qualified":True}}
                  for key in ("r00-white","r00-black","r02-white","r10-white")}
        report={
            "public_bot_release":False,
            "network_recovery_qualified":False,
            "artifact_distribution_authorized":False,
            "release_blocked":True,
            "release_promotion_authorized":False,
            "license_approved":False,
            "destination_host_qualified":False,
            "image_integrity_qualified":True,
            "offline_candidate_qualified":False,
            "smoke":complete,
            "oci_archive_sha256":"a"*64,
            "image_manifest_digest":"sha256:"+"b"*64,
            "oci_evidence":{},
        }
        with self.assertRaisesRegex(ValueError,"OCI archive, SBOM"):
            claims.verify_report(report,"release_aggregate")
        report["oci_evidence"]={key:True for key in (
            "integrity_verified","oci_layers_verified",
            "oci_diff_ids_verified","oci_application_identity_verified",
            "oci_attestation_references_verified",
            "oci_attestation_subjects_verified",
            "oci_sbom_verified","oci_provenance_verified")}
        claims.verify_report(report,"release_aggregate")

    def test_forged_image_integrity_needs_four_independent_games(self):
        report={
            "public_bot_release":False, "network_recovery_qualified":False,
            "artifact_distribution_authorized":False,
            "release_blocked":True,"release_promotion_authorized":False,
            "license_approved":False,"destination_host_qualified":False,
            "image_integrity_qualified":True,"smoke":{},
            "offline_candidate_qualified":False,
        }
        with self.assertRaisesRegex(ValueError,"verified packaged games"):
            claims.verify_report(report,"release_aggregate")

    def test_safe_output_never_exports_nested_secret_canaries(self):
        doc={
            "public_bot_release":False,
            "unsafe_witnesses":[{"behavior":"UNSAFE_WITNESS",
                "unusable_move_submissions":[
                    {"path":"/api/account?access_token=PRIVATE-CANARY",
                     "uci":"PRIVATE-CANARY","status":401}],
                "early_rate_limit_requests":[
                    {"path":"/api/account?access_token=PRIVATE-CANARY",
                     "request_target":"//PRIVATE-CANARY",
                     "error":"PRIVATE-CANARY"}],
                "runner_errors":["PRIVATE-CANARY"]}],
            "failure":{"message":"PRIVATE-CANARY"},
            "harness_failures":[{"error":"PRIVATE-CANARY"}],
        }
        out=sanitizer.serialize_report(doc)
        self.assertNotIn("PRIVATE-CANARY",out)
        self.assertIn("/api/account",out)
        self.assertIn("UNSAFE_WITNESS",out)

if __name__=="__main__":
    unittest.main()
