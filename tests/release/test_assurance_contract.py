"""Independent negative oracles for source closure and verdict implications."""
from __future__ import annotations
import copy
import importlib.util
import json
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
