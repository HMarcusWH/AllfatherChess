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


    def test_late_exception_after_completed_game_is_never_suppressed(self):
        """Reverts to a false-green exit if the original finally-return returns."""
        moves=["f2f3","e7e5","g2g4","d8h4"]
        report={"game_id":"off3b001","color":"white","moves":moves,
                "accepted":[],"complete":True,
                "outcome":{"termination":"CHECKMATE"},
                "forbidden_attempts":[],"requests":[],
                "transport_trace":[],"fault":{"activations":[]}}
        class Done:
            def is_set(self):
                return True
        class Ready:
            def wait(self,*args):
                return True
        class Fault:
            def evidence(self):
                return {"activations":[]}
        class Sessions:
            def snapshot(self):
                return []
        class FinishedServer:
            url="http://127.0.0.1:21212/"
            finished=Done()
            event_stream_open=Ready()
            game_stream_open=Ready()
            fault=Fault()
            sessions=Sessions()
            def __init__(self,*args):
                pass
            def start(self):
                pass
            def issue_challenge(self):
                pass
            def close(self):
                pass
            def evidence(self):
                return report
        class Journal:
            def snapshot(self):
                return {"games":[{"game_id":"off3b001","terminal":1,
                                  "history_json":json.dumps(moves)}],
                        "intents":[],"cooldown":[],"events":[]}
        class Owner:
            epoch="a"*32
            journal=Journal()
            def __init__(self,*args):
                self.directory=Path(temp)/"state"
            def __enter__(self):
                return self
            def __exit__(self,*exc):
                return False
        class Proc:
            def poll(self):
                return None
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(smoke.os,"getpid",return_value=1), \
                 mock.patch.dict(smoke.os.environ,{"ONLINE_RELEASEx_ISOLATED":"1"}), \
                 mock.patch.object(smoke,"PERSIST",Path(temp)), \
                 mock.patch.object(smoke,"TOOLS",ROOT), \
                 mock.patch.object(smoke,"FakeLichess",FinishedServer), \
                 mock.patch.object(smoke,"SessionOwner",Owner), \
                 mock.patch.object(smoke.bridge_policy,"generate",return_value={}), \
                 mock.patch.object(smoke,"_start",return_value=(Proc(),None)), \
                 mock.patch.object(smoke,"shutdown_bridge"), \
                 mock.patch.object(smoke,"stop_all_isolated_processes",
                                   side_effect=AssertionError("late after completed game")):
                with self.assertRaisesRegex(AssertionError,"late after completed game"):
                    smoke.run(color="white",scenario_id="R00",run_id="late")
            result=json.loads((Path(temp)/"late/result.json").read_text())
            self.assertEqual(result["observations"][0]["observation"],
                             "complete_confirmed")
            self.assertFalse(result["ready"])
            self.assertTrue(any("AssertionError" in item
                                for item in result["runner_errors"]))



if __name__=="__main__":
    unittest.main()
