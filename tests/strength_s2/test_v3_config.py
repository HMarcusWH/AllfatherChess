"""S2 schema-v3 opt-in must not weaken any old v1/v2 parser."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from controller.runtime import load_runtime_config, RuntimeError
from controller.strength.native_clock import NativeClockSettings

ROOT=Path(__file__).resolve().parents[2]
VALID=json.loads((ROOT/"config/allfather.strength-s2-observe.experimental.json").read_text())


class V3ConfigTests(unittest.TestCase):
    def load(self,config):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/"profile.json"
            data=deepcopy(config)
            data["root"]=str(ROOT)
            data["instances"]["stockfish-anchor"]["binary"]=sys.executable
            p.write_text(json.dumps(data),encoding="utf-8")
            return load_runtime_config(p)

    def test_explicit_native_observe_only_has_one_stockfish(self):
        runtime=self.load(VALID)
        self.assertEqual(runtime.mode,"anchor")
        self.assertEqual(set(runtime.backends),{"stockfish-anchor"})
        self.assertIsInstance(runtime.online_time,NativeClockSettings)
        self.assertEqual(runtime.strength.mode,"observe_only")
        self.assertEqual(runtime.strength.source_commit.__len__(),40)
        self.assertIsNone(runtime.shadow)
        self.assertIsNone(runtime.hybrid_authority)

    def test_legacy_and_v3_schema_isolation(self):
        cases=[
            lambda d:d.update(schema_version=2),
            lambda d:d.update(schema_version=4),
            lambda d:d["strength"].update(mode="apply"),
            lambda d:d["strength"].update(mode="control_anchor"),
            lambda d:d["strength"].update(extraneous=True),
            lambda d:d["instances"]["stockfish-anchor"]["options"].update(MultiPV=2),
            lambda d:d["instances"]["stockfish-anchor"]["options"].update(Ponder=True),
            lambda d:d["instances"]["stockfish-anchor"]["options"].update(Threads=0),
            lambda d:d["instances"]["stockfish-anchor"].update(role="shadow"),
            lambda d:d["instances"].update(other=deepcopy(d["instances"]["stockfish-anchor"])),
            lambda d:d.update(shadow={"owners":[]}),
            lambda d:d["strength"].update(evidence_dir="../escape"),
        ]
        for change in cases:
            with self.subTest(change=change):
                a=deepcopy(VALID)
                change(a)
                with self.assertRaises((RuntimeError,ValueError)):
                    self.load(a)

    def test_strength_key_rejected_by_v2_even_when_disabled(self):
        a=deepcopy(VALID)
        a["schema_version"]=2
        a["strength"]={"enabled":False}
        with self.assertRaises(RuntimeError):
            self.load(a)


if __name__=="__main__":
    unittest.main()
