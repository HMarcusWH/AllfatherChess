"""Ephemeral signatures, durable one-game lease, adversarial runtime admission."""
import base64
import dataclasses
import datetime as dt
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from deploy.live_canary.supervisor import persist_stop_marker,SupervisorRefused
from deploy.live_canary.admission import (
    SourceBinding,LeaseLedger,AdmissionDenied,verify_admission,policy_is_denied)
from deploy.live_canary.supervisor import SupervisorRefused,LiveSupervisor,verify_host_receipt
from deploy.lichess.live_session import LiveStageError

BINDING=SourceBinding(source_commit="a"*40,source_tree="b"*40,
    image_digest="sha256:"+"c"*64,policy_sha256="d"*64,
    host_id="ci-test-host",host_digest="e"*64,legal_review_digest="f"*64,
    owner_bot="allfatherfixture",opponent_bot="approved_test_bot")

class StopMarkerTests(unittest.TestCase):
    def test_durable_stop_idempotent_and_link_refused(self):
        import tempfile,stat
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            marker=root/"stop.flag"
            persist_stop_marker(marker)
            self.assertEqual(marker.read_bytes(),b"STOP\n")
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode),0o600)
            persist_stop_marker(marker)
            self.assertEqual(marker.read_bytes(),b"STOP\n")
            link=root/"evil.flag"
            link.symlink_to(marker)
            with self.assertRaises(SupervisorRefused):
                persist_stop_marker(link)

class PrBTrustBoundaryTests(unittest.TestCase):
    def test_pr_a_has_no_source_operator_trust_or_live_authority(self):
        from deploy.live_canary.entrypoint import require_separately_approved_operator_trust
        root=Path(__file__).resolve().parents[2]
        self.assertFalse((root/"qualification/online-canary-operator-trust-v1.json").exists())
        with tempfile.TemporaryDirectory() as folder:
            key=Path(folder)/"operator.pub.pem";key.write_bytes(b"test-key-not-authority")
            with self.assertRaises(ValueError):
                require_separately_approved_operator_trust(root,BINDING,key)

    def test_wrong_key_or_legal_review_cannot_be_approved_by_runtime_lease(self):
        from deploy.live_canary.entrypoint import require_separately_approved_operator_trust
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);p=root/"qualification";p.mkdir()
            key=root/"operator.pub.pem";key.write_bytes(b"untrusted-test-key")
            values={
                "schema_version":1,"scope":"pr-b-one-game-operator-trust",
                "approved":True,"source_commit":BINDING.source_commit,
                "source_tree":BINDING.source_tree,"image_digest":BINDING.image_digest,
                "host_id":BINDING.host_id,"host_digest":BINDING.host_digest,
                "legal_review_digest":"0"*64,
                "operator_public_key_sha256":"f"*64,
            }
            (p/"online-canary-operator-trust-v1.json").write_text(json.dumps(values))
            with self.assertRaises(ValueError):
                require_separately_approved_operator_trust(root,BINDING,key)

class SignedLeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.priv=root/"private.pem";self.pub=root/"public.pem"
        subprocess.run(["openssl","genpkey","-algorithm","Ed25519","-out",str(self.priv)],
                       check=True,capture_output=True)
        subprocess.run(["openssl","pkey","-in",str(self.priv),"-pubout","-out",str(self.pub)],
                       check=True,capture_output=True)
    def lease(self,override=None):
        now=dt.datetime.now(dt.timezone.utc)
        value={**dataclasses.asdict(BINDING),"schema_version":1,
               "session_nonce":"1"*48,"operator_approval_id":"ci-synthetic-approved",
               "not_before_utc":(now-dt.timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "expires_at_utc":(now+dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "max_games":1}
        value.update(override or {})
        data=(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True)+"\n").encode()
        path=Path(self.tmp.name)/"payload";sig=Path(self.tmp.name)/"sig"
        path.write_bytes(data)
        subprocess.run(["openssl","pkeyutl","-sign","-inkey",str(self.priv),
                        "-rawin","-in",str(path),"-out",str(sig)],
                       check=True,capture_output=True)
        value["signature_b64"]=base64.b64encode(sig.read_bytes()).decode()
        return value
    def test_real_valid_ed25519_signature(self):
        lease=self.lease()
        self.assertEqual(verify_admission(lease,BINDING,self.pub),lease)
    def test_approval_mutation_fails_signature(self):
        lease=self.lease();lease["opponent_bot"]="otherbot"
        with self.assertRaises(AdmissionDenied):verify_admission(lease,BINDING,self.pub)
    def test_wrong_host_policy_image_head_tree_legal_denied(self):
        for field,value in [("host_digest","0"*64),("policy_sha256","0"*64),
                            ("image_digest","sha256:"+"0"*64),
                            ("source_commit","0"*40),("source_tree","0"*40),
                            ("legal_review_digest","0"*64)]:
            with self.subTest(field=field):
                lease=self.lease({field:value})
                with self.assertRaises(AdmissionDenied):
                    verify_admission(lease,BINDING,self.pub)
    def test_expiry_bad_start_and_untrusted_key(self):
        lease=self.lease()
        with self.assertRaises(AdmissionDenied):
            verify_admission(lease,BINDING,self.pub,now=dt.datetime(2070,1,1,tzinfo=dt.timezone.utc))
        self.pub.write_bytes(b"not-an-ed25519-key")
        with self.assertRaises(AdmissionDenied):verify_admission(lease,BINDING,self.pub)
    def test_no_unsigned_or_multiple_game_lease(self):
        for change in [{"signature_b64":"bad"},{"max_games":2},
                       {"session_nonce":"short"}]:
            lease=self.lease()
            lease.update(change)
            with self.assertRaises(AdmissionDenied):verify_admission(lease,BINDING,self.pub)
    def test_durable_one_game_lease_survives_restart(self):
        db=Path(self.tmp.name)/"session.db"
        s=LeaseLedger(db);s.initialize()
        s.claim("a"*32);s.bind_challenge("a"*32,"off3b001")
        s.bind_game("a"*32,"off3b001");s.finish("a"*32)
        again=LeaseLedger(db);again.initialize()
        self.assertEqual(again.snapshot()["terminal"],1)
        with self.assertRaises(AdmissionDenied):
            again.claim("b"*32)
        with self.assertRaises(AdmissionDenied):
            again.bind_game("a"*32,"newgame1")
    def test_source_policy_has_no_approvals(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[2]
        result=policy_is_denied(root/"qualification/online-live-canary-v1.json")
        self.assertEqual(len(result),64)

class RepairRegressions(unittest.TestCase):
    def test_host_report_tampering_preserving_digest_is_denied(self):
        import hashlib
        report={"schema_version":1,"scope":"synthetic-ci-fixture-only",
            "host_id":"ci-test-host",
            "capacity":{"operator_minimums_met":True,"capacity_complete":True},
            "operator_envelope_met":True,"durable_volume_separate":True,
            "bridge_egress_test_pass":True,"gateway_private_port_test_pass":True,
            "destination_host_qualified":False,"live_game_authorized":False,
            "public_bot_release":False,
            "storage_tests":{"persistent_separate":True,"scratch_separate":True},
            "image_identity_verified":True,"source_mounts_read_only":True,
            "network_tests":{k:True for k in (
                "bridge_egress_test_pass","gateway_private_port_test_pass",
                "separate_network_namespaces","bridge_only_internal",
                "gateway_dual_homed","no_host_ports","gateway_only_secret_mount",
                "bridge_external_tcp_denied")}}
        digest=hashlib.sha256(json.dumps(report,sort_keys=True,
                                        separators=(",",":")).encode()).hexdigest()
        binding=dataclasses.replace(BINDING,host_digest=digest)
        report["observation_digest"]=digest
        verify_host_receipt(report,binding)
        report["capacity"]["capacity_complete"]=False
        with self.assertRaises(SupervisorRefused):
            verify_host_receipt(report,binding)

    def test_staging_failure_drains_once_preserves_primary_and_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            sup=object.__new__(LiveSupervisor)
            sup.state=Path(d)/"state"
            sup.work=Path(d)/"scratch"
            sup.release=Path(d)/"release"
            sup.bridge=Path(d)/"bridge"
            sup.binding=BINDING
            sup.phase="preflight"
            sup.process=None
            sup.cleanup_error_type=None
            sup.stopping=False
            err=LiveStageError("BRIDGE_CONFIG_VALIDATE")
            with mock.patch.object(sup,"preflight"), mock.patch(
                "deploy.live_canary.supervisor.stage_live",side_effect=err
            ), mock.patch.object(sup,"stop_and_reap",
                side_effect=SupervisorRefused("forced cleanup error")) as cleanup:
                with self.assertRaises(LiveStageError) as got:
                    sup.run(lease_file=Path(d)/"lease",
                            binding_file=Path(d)/"binding")
                self.assertIs(got.exception,err)
                self.assertEqual(sup.phase,"workspace_stage")
                self.assertEqual(sup.cleanup_error_type,"SupervisorRefused")
                cleanup.assert_called_once()


if __name__=="__main__":unittest.main()
