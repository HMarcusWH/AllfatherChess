"""S3-A opt-in preparation is executable but has NO auxiliary move authority."""
from __future__ import annotations
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from controller.runtime import RuntimeError, load_runtime_config
from controller.strength.integrity import strict_load, verify_flight
from tests.harness.uci_session import UciSession

ROOT=Path(__file__).resolve().parents[2]
PROFILE=ROOT/"config/allfather.strength-s3a-observe.experimental.json"
POLICY=ROOT/"qualification/strength/s3a-contracts-v1.json"
FAKE=ROOT/"tests/strength_s2/fake_native_uci.py"
EVIDENCE=ROOT/"build/strength-s3a-evidence"


class S3APrepProfile(unittest.TestCase):
    def load(self,document,*,opt_in=True):
        with tempfile.TemporaryDirectory() as td:
            content=deepcopy(document)
            content["root"]=str(ROOT)
            content["instances"]["stockfish-anchor"]["binary"]=sys.executable
            target=Path(td)/"s3a.json"
            target.write_text(json.dumps(content))
            with patch.dict(os.environ,{"ALLFATHER_S3A_OFFLINE":"1" if opt_in else "0"}):
                return load_runtime_config(target)

    def test_strict_v3_requires_explicit_opt_in_and_one_anchor(self):
        doc=json.loads(PROFILE.read_text())
        with self.assertRaises(RuntimeError):
            self.load(doc,opt_in=False)
        cfg=self.load(doc)
        self.assertEqual(cfg.mode,"anchor")
        self.assertEqual(set(cfg.backends),{"stockfish-anchor"})
        self.assertEqual(cfg.strength.policy,"strength_s3a_contracts_observe_v1")
        self.assertEqual(cfg.strength.mode,"observe_only")
        self.assertIsNone(cfg.shadow)
        self.assertIsNone(cfg.hybrid_authority)
        self.assertEqual(cfg.backends["stockfish-anchor"].options["MultiPV"],1)

    def test_old_configs_and_bad_policy_cannot_opt_in_by_accident(self):
        doc=json.loads(PROFILE.read_text())
        mutations=[
            lambda d:d.update(schema_version=2),
            lambda d:d.update(schema_version=4),
            lambda d:d["strength"].update(mode="apply"),
            lambda d:d["strength"].update(mode="control_anchor"),
            lambda d:d["strength"].update(runtime_helpers=True),
            lambda d:d["strength"].update(policy="strength_s4_apply_v1"),
            lambda d:d["instances"]["stockfish-anchor"]["options"].update(MultiPV=2),
            lambda d:d["instances"].update(specialist=deepcopy(d["instances"]["stockfish-anchor"])),
            lambda d:d.update(mode="active"),
        ]
        for mutation in mutations:
            d=deepcopy(doc);mutation(d)
            with self.subTest(d=d),self.assertRaises((RuntimeError,ValueError)):
                self.load(d)

    def test_source_claims_remain_denied(self):
        doc=json.loads(POLICY.read_text())
        self.assertEqual(doc["status"],"IMPLEMENTED_SOURCE_ONLY_NOT_ENGINE_DISPATCH_QUALIFIED")
        self.assertEqual(doc["action_default"],"NONE")
        self.assertIsNone(doc["measurement_profile"])
        self.assertFalse(doc["cost_catalog_reviewed"])
        self.assertFalse(doc["runtime_proposer_or_comparator_processes"])
        self.assertTrue(all(v is False for v in doc["claim_boundary"].values()))

    def test_one_real_fake_child_go_is_anchor_only_and_flight_is_not_qualified(self):
        before={p.name for p in EVIDENCE.glob("*.json")} if EVIDENCE.exists() else set()
        with tempfile.TemporaryDirectory() as td:
            config=json.loads(PROFILE.read_text())
            config["root"]=str(ROOT)
            config["instances"]["stockfish-anchor"]["binary"]=str(FAKE)
            target=Path(td)/"s3a.json"
            target.write_text(json.dumps(config))
            with UciSession(Path(sys.executable),cwd=ROOT,timeout=12,
                            args=["-m","controller","--config",str(target)],
                            environment={"ALLFATHER_S3A_OFFLINE":"1"}) as u:
                u.set_position({"startpos_moves":[]})
                command="go wtime 30000 btime 30000 winc 1000 binc 1000"
                u.send(command)
                output=u.read_until(lambda line:line.startswith("bestmove "),
                                    label="S3-A anchor-only",timeout=8)
                self.assertIn("info string native-go-received "+command,output)
                self.assertEqual([line for line in output if line.startswith("bestmove ")],
                                 ["bestmove e2e4"])
                u.ready()
        after={p.name for p in EVIDENCE.glob("*.json")} if EVIDENCE.exists() else set()
        made=[EVIDENCE/name for name in after-before]
        try:
            self.assertEqual(len(made),1)
            data=strict_load(made[0])
            decision=verify_flight(data)
            self.assertEqual(decision["status"],"PUBLISHED_ANCHOR")
            self.assertEqual(decision["replacement_count"],0)
            self.assertIs(decision["qualified"],False)
        finally:
            for path in made:
                path.unlink()

if __name__=="__main__":
    unittest.main()
