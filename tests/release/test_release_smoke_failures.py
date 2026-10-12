"""The actual image supervisor cannot turn exceptions into green evidence."""
from __future__ import annotations
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location(
    "offline_smoke_failure_tests",ROOT/"deploy/release/offline_smoke.py")
smoke=importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class SmokeFailureTests(unittest.TestCase):
    def test_unexpected_exception_cannot_be_swallowed_by_finally(self):
        class BrokenServer:
            def __init__(self,*args):
                self.fault=type("F",(),{"evidence":lambda self:{"activations":[]}})()
            def start(self):
                raise AssertionError("synthetic supervisor exception")
            def evidence(self):
                return {"complete":False,"moves":[],"accepted":[],
                        "forbidden_attempts":[],"requests":[],
                        "fault":{"activations":[]},"transport_trace":[]}

        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(smoke.os,"getpid",return_value=1),\
                 mock.patch.dict(smoke.os.environ,{"ONLINE_RELEASEx_ISOLATED":"1"}),\
                 mock.patch.object(smoke,"PERSIST",Path(d)),\
                 mock.patch.object(smoke,"TOOLS",ROOT),\
                 mock.patch.object(smoke,"FakeLichess",BrokenServer):
                with self.assertRaisesRegex(AssertionError,"synthetic"):
                    smoke.run(color="white",scenario_id="R00",run_id="broken")
                result=json.loads((Path(d)/"broken/result.json").read_text())
                self.assertFalse(result["ready"])
                self.assertTrue(any("AssertionError" in x
                                    for x in result["runner_errors"]))


if __name__=="__main__":
    unittest.main()
