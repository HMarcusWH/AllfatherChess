"""Shared fail-closed HTTP admission across smoke and aggregate."""
from __future__ import annotations
import importlib.util
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
    def test_each_allowed_get_and_case_agrees_across_both_verifiers(self):
        for case in smoke.CASES:
            for path in smoke.http_contract.ALLOWED_GETS:
                row={"method":"GET","path":path,"status":200}
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
        ):
            with self.subTest(row=row):
                self.assertFalse(smoke.http_contract.allowed_release_http(
                    row,"r00-white"))
                self.assertFalse(aggregate.allowed_release_http(row,"r00-white"))

if __name__=="__main__":
    unittest.main()
