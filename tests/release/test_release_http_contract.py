"""Shared fail-closed HTTP admission across smoke and aggregate."""
from __future__ import annotations
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

def load(path, ident):
    spec=importlib.util.spec_from_file_location(ident,ROOT/path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

smoke=load("scripts/qualify-release-smoke.py","release1_smoke_http_test")
aggregate=load("scripts/qualify-release-aggregate.py","release1_aggregate_http_test")


class SharedHTTPAdmissionTests(unittest.TestCase):
    def test_safe_release_report_module_loads_without_tests_package(self):
        import importlib.util as ilu
        path=ROOT/"scripts/sanitize_qualification_evidence.py"
        spec=ilu.spec_from_file_location("safe_report_test",path)
        instance=ilu.module_from_spec(spec)
        spec.loader.exec_module(instance)
        self.assertEqual(instance.sanitize_report({
            "path":"/api/account?secret=CANARY"})["path"],"/api/account")

    def test_standalone_release_verifiers_import_policy_without_pythonpath(self):
        # Identical invocation mode to the real OCI release workflow.
        with tempfile.TemporaryDirectory() as tmp:
            environment = {k:v for k,v in os.environ.items() if k != "PYTHONPATH"}
            for entry in ("scripts/qualify-release-smoke.py",
                          "scripts/qualify-release-aggregate.py"):
                with self.subTest(cli=entry):
                    outcome = subprocess.run(
                        [sys.executable, str(ROOT / entry), "--help"],
                        cwd=tmp, env=environment, capture_output=True,
                        text=True, timeout=20)
                    self.assertEqual(outcome.returncode, 0, outcome.stderr)
                    self.assertIn("usage:", outcome.stdout.lower())

    def test_each_allowed_get_and_case_agrees_across_both_verifiers(self):
        for case in smoke.CASES:
            for path in smoke.http_contract.ALLOWED_GETS:
                row={"method":"GET","path":path,"status":200,
                     "request_target":path,"target_canonical":True}
                with self.subTest(case=case,path=path):
                    self.assertTrue(smoke.http_contract.allowed_release_http(
                        row,case))
                    self.assertEqual(aggregate.allowed_release_http(row,case),
                                     smoke.http_contract.allowed_release_http(
                                         row,case))

    def test_no_unauthorized_get_or_failed_status_is_admissible(self):
        for row in (
            {"method":"GET","path":"/api/account/email","status":200},
            {"method":"GET","path":"/api/account","status":401},
            {"method":"GET","path":"/api/account","status":429},
            {"method":"GET","path":"/api/account","status":200,
             "request_id":"move-123"},
            {"method":"GET","path":"/api/account?x=1","status":200},
            {"method":"GET","path":"/api/account","status":True},
            {"method":"POST","path":"/api/token/test","status":500},
            {"method":"POST",
             "path":"/api/bot/game/off3b001/move/%65%32e4","status":200,
             "request_id":"request-1"},
            {"method":"GET","path":"/api/account","status":200,
             "request_target":"/api/account?access_token=leak",
             "target_canonical":True},
            {"method":"GET","path":"/api/users/status","status":200,
             "request_target":"/api/users/status?ids=unauthorized",
             "target_canonical":True},
        ):
            with self.subTest(row=row):
                self.assertFalse(smoke.http_contract.allowed_release_http(
                    row,"r00-white"))
                self.assertFalse(aggregate.allowed_release_http(row,"r00-white"))

    def test_rejected_raw_query_is_hashed_before_audit(self):
        from tests.lichess.http_target_contract import safe_audit_fields
        original="/api/account?access_token=PRIVATE-CANARY"
        safe=safe_audit_fields("GET",original)
        self.assertFalse(safe["target_canonical"])
        self.assertIsNone(safe["request_target"])
        self.assertEqual(safe["path"],"/api/account")
        self.assertNotIn("PRIVATE-CANARY",repr(safe))
        self.assertEqual(len(safe["request_target_sha256"]),64)

    def test_legitimate_upstream_query_forms_are_admitted(self):
        for method, raw, rid in (
            ("GET", "/api/users/status?ids=allfatherfixture", None),
            ("POST", "/api/bot/game/off3b001/move/e2e4?offeringDraw=false",
             "request-1"),
            ("POST", "/api/bot/game/off3b001/move/e2e4?offeringDraw=true",
             "request-1"),
        ):
            row = {"method":method, "path":raw.split("?")[0],
                   "request_target":raw, "target_canonical":True,
                   "status":200,"request_id":rid}
            self.assertTrue(aggregate.allowed_release_http(row,"r00-white"))

if __name__=="__main__":
    unittest.main()
