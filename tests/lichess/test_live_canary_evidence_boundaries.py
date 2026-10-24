"""Source-independent negative controls for PR-A's success-evidence gate.

These intentionally incomplete fixtures can never be accepted as a game.
They verify that cleanup/image/readonly-source identity defects are rejected
at the specific source-independent gate BEFORE game data can be consulted.
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location(
    "independent_canary_verifier",ROOT/"scripts/verify-online-live-canary.py")
VERIFIER=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)

class IndependentNegativeEvidence(unittest.TestCase):
    def fixture(self,root:Path):
        root.mkdir(parents=True,exist_ok=True)
        for name in ("gateway/server.json","gateway/gateway-state.json",
                     "bridge/report.json"):
            path=root/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text("{}")
        hashes={name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                for name in ("gateway/server.json","gateway/gateway-state.json",
                             "bridge/report.json")}
        value={
            "mode":"offline-fake-only","compose_cleanup_ok":True,
            "live_game_authorized":False,"public_bot_release":False,
            "color":"white","source_sha":"a"*40,
            "image_digest":"sha256:"+"b"*64,
            "evidence":hashes,
            "host_observation":{
                "scope":"synthetic-ci-fixture-only",
                "image_identity_verified":True,"source_mounts_read_only":True,
                "operator_envelope_met":True,
                "durable_volume_separate":True,
                "destination_host_qualified":False,
                "live_game_authorized":False,
                "capacity":{"operator_minimums_met":True,"capacity_complete":True},
                "network_tests":{key:True for key in (
                    "bridge_egress_test_pass","gateway_private_port_test_pass",
                    "separate_network_namespaces","bridge_only_internal",
                    "gateway_dual_homed","no_host_ports",
                    "gateway_only_secret_mount","bridge_external_tcp_denied")}}
        }
        (root/"integration.json").write_text(json.dumps(value))
        return value

    def test_no_success_if_cleanup_has_not_been_qualified(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            record=self.fixture(folder)
            record.pop("compose_cleanup_ok")
            (folder/"integration.json").write_text(json.dumps(record))
            with self.assertRaisesRegex(VERIFIER.EvidenceFailure,
                                       "fake run acquired real/public authority"):
                VERIFIER.verify_one(folder,"white","a"*40)

    def test_no_success_if_cleanup_failure_receipt_exists(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            self.fixture(folder)
            (folder/"cleanup-incomplete.json").write_text(
                '{"status":"NOT_COMPLETED"}')
            with self.assertRaisesRegex(VERIFIER.EvidenceFailure,
                                       "fake run acquired real/public authority"):
                VERIFIER.verify_one(folder,"white","a"*40)

    def test_actual_image_and_readonly_mount_are_mandatory(self):
        for target in ("image_identity_verified","source_mounts_read_only"):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as d:
                folder=Path(d)
                record=self.fixture(folder)
                record["host_observation"][target]=False
                (folder/"integration.json").write_text(json.dumps(record))
                with self.assertRaisesRegex(VERIFIER.EvidenceFailure,
                                            "host test result improperly promoted"):
                    VERIFIER.verify_one(folder,"white","a"*40)

    def test_source_gate_receipt_never_copies_exception_messages(self):
        secret="Bearer private-synthetic-test-token"
        def inject():
            # A malicious claim may include arbitrary path/token text.
            VERIFIER.require(False, secret)
        try:
            inject()
        except VERIFIER.EvidenceFailure as exc:
            diagnostic=VERIFIER._failure_diagnostic(exc)
        else:
            self.fail("fixture must be rejected")
        self.assertNotIn(secret,json.dumps(diagnostic))
        self.assertEqual(diagnostic["error_type"],"EvidenceFailure")
        self.assertEqual(diagnostic["source_gate"],
                         {"function":"UNCLASSIFIED","line":0})

    def test_verifier_source_line_is_bound_to_known_function(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)
            self.fixture(target)
            record=json.loads((target/"integration.json").read_text())
            record.pop("compose_cleanup_ok")
            (target/"integration.json").write_text(json.dumps(record))
            try:
                VERIFIER.verify_one(target,"white","a"*40)
            except VERIFIER.EvidenceFailure as exc:
                diagnostic=VERIFIER._failure_diagnostic(exc)
            else:
                self.fail("fixture must be rejected")
            self.assertEqual(diagnostic["error_type"],"EvidenceFailure")
            self.assertEqual(diagnostic["source_gate"]["function"],"verify_one")
            self.assertGreater(diagnostic["source_gate"]["line"],0)
            self.assertLessEqual(diagnostic["source_gate"]["line"],1000)
            self.assertNotIn(str(target),json.dumps(diagnostic))


if __name__=="__main__":
    unittest.main()
