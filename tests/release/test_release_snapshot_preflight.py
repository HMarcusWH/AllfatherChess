"""Early signed snapshot diagnostic; no APT, network or engine builds."""
from __future__ import annotations
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("release1_snapshot_check",
                                            ROOT/"scripts/check-release-snapshot.py")
checker=importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class DummyResponse:
    def __enter__(self):
        return self
    def __exit__(self,*args):
        pass
    def read(self,n):
        return b"signed index is independently verified by Docker APT"[:n]


class SnapshotAvailabilityTests(unittest.TestCase):
    def test_transient_503_retries_only_same_frozen_suite(self):
        attempts={}
        def opener(url,timeout):
            attempts[url]=attempts.get(url,0)+1
            if attempts[url]==1:
                raise HTTPError(url,503,"temporary upstream outage",{},None)
            return DummyResponse()
        result=checker.probe("20261001T000000Z",opener=opener,
                             sleeper=lambda seconds:None)
        self.assertEqual(set(result["suites"]),set(checker.SUITES))
        self.assertEqual(set(attempts.values()),{2})
        self.assertFalse(result["signature_verified"])
        self.assertTrue(all("20261001T000000Z" in url for url in attempts))

    def test_nontransient_status_is_immediately_refused(self):
        requests=[]
        def opener(url,timeout):
            requests.append(url)
            raise HTTPError(url,404,"not found",{},None)
        with self.assertRaisesRegex(ValueError,"HTTP 404"):
            checker.probe("20261001T000000Z",opener=opener,
                          sleeper=lambda seconds:None)
        self.assertEqual(len(requests),1)

    def test_persistent_503_is_bounded(self):
        requests=[]
        def opener(url,timeout):
            requests.append(url)
            raise HTTPError(url,503,"temporary outage",{},None)
        with self.assertRaisesRegex(ValueError,"HTTP 503"):
            checker.probe("20261001T000000Z",opener=opener,
                          sleeper=lambda seconds:None)
        self.assertEqual(len(requests),3)

    def test_injected_snapshot_identity_rejected_before_network(self):
        with self.assertRaisesRegex(ValueError,"invalid pinned"):
            checker.probe("20261001T000000Z/../latest",
                          opener=lambda *args,**kw: self.fail("network was called"))

if __name__=="__main__":
    unittest.main()
