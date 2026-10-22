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
from deploy.live_canary.supervisor import persist_stop_marker,SupervisorRefused
from deploy.live_canary.admission import (
    SourceBinding,LeaseLedger,AdmissionDenied,verify_admission,policy_is_denied)
from deploy.live_canary.supervisor import SupervisorRefused

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

if __name__=="__main__":unittest.main()
