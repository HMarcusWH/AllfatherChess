"""Sensitive HTTP material is never permitted in PR-A artifacts/logging."""
import unittest
from pathlib import Path
from deploy.lichess.live_gateway import LiveGateway,GatewayFailure,read_gateway_token
from deploy.lichess.live_gateway_policy import CanarySession,RouteDenied,admit_route
class RedactionTests(unittest.TestCase):
    def test_gateway_direct_real_secret_denied(self):
        s=CanarySession("allfatherfixture","approved_test_bot",token_verified=True)
        with self.assertRaises(RouteDenied):
            admit_route("GET",b"/api/account",
                        {"Host":"live-gateway:8765","Authorization":"Bearer SIMULATED-REAL-TOKEN"},
                        b"",s)
    def test_reject_linked_or_wrong_mode_secret(self):
        import os,tempfile
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"token";p.write_text("test-token\n");os.chmod(p,0o644)
            with self.assertRaises(GatewayFailure):read_gateway_token(p)
            os.chmod(p,0o600)
            self.assertEqual(read_gateway_token(p),"test-token")
            link=Path(d)/"link";link.symlink_to(p)
            with self.assertRaises(GatewayFailure):read_gateway_token(link)
    def test_gateway_code_disables_proxies_and_redirects(self):
        root=Path(__file__).resolve().parents[2]
        src=(root/"deploy/lichess/live_gateway.py").read_text()
        self.assertIn("session.trust_env=False",src)
        self.assertIn('"allow_redirects":False',src)
        self.assertIn('"verify":True',src)
        self.assertIn('LICHESS_ORIGIN+path',src)
        self.assertNotIn('verify=False',src)

if __name__=="__main__":unittest.main()
