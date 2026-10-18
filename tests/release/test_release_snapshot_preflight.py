"""Snapshot preflight: complete signed envelopes, bounded I/O and fail-closed report."""
from __future__ import annotations
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "release1_snapshot_check", ROOT / "scripts/check-release-snapshot.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def envelope(suite: str) -> bytes:
    metadata = ("Origin: Ubuntu\nLabel: Ubuntu\nSuite: " + suite +
                "\nArchitectures: amd64\nComponents: main universe restricted multiverse\n" +
                "Date: Thu, 01 Oct 2026 00:00:00 UTC\n" + "SHA256:\n" +
                ("a" * 72 + " 170 Contents-amd64.gz\n") * 20)
    return ("-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n" +
            metadata + "-----BEGIN PGP SIGNATURE-----\n" +
            "iQIz" + "a" * 120 + "\n=ABCD\n-----END PGP SIGNATURE-----\n").encode()


class Response:
    def __init__(self, url, data, *, final_url=None, declared=None,
                 error=None):
        self.url = final_url or url
        self.stream = io.BytesIO(data)
        self.headers = {} if declared is None else {"Content-Length":str(declared)}
        self.error = error
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def geturl(self): return self.url
    def read(self, n):
        if self.error:
            raise self.error
        return self.stream.read(n)


class SnapshotAvailabilityTests(unittest.TestCase):
    def test_complete_signed_reachable_after_transient_503(self):
        tries = {}
        def opener(url, timeout):
            suite = url.split("/dists/")[1].split("/")[0]
            tries[url] = tries.get(url, 0) + 1
            if tries[url] == 1:
                raise HTTPError(url, 503, "temporary outage", {}, None)
            content = envelope(suite)
            return Response(url,content,declared=len(content))
        result = checker.probe("20261001T000000Z",opener=opener,
                               sleeper=lambda _:None)
        self.assertEqual(set(result["suites"]),set(checker.SUITES))
        self.assertEqual(set(tries.values()), {2})
        self.assertFalse(result["signature_verified"])

    def test_nontransient_status_is_immediately_refused(self):
        requests=[]
        def opener(url,timeout):
            requests.append(url)
            raise HTTPError(url,404,"not found",{},None)
        with self.assertRaisesRegex(ValueError,"HTTP 404"):
            checker.probe("20261001T000000Z",opener=opener,
                          sleeper=lambda _:None)
        self.assertEqual(len(requests),1)

    def test_persistent_503_is_bounded(self):
        attempts=[]
        def opener(url,timeout):
            attempts.append(url)
            raise HTTPError(url,503,"temporary",{},None)
        with self.assertRaisesRegex(ValueError,"HTTP 503"):
            checker.probe("20261001T000000Z",opener=opener,
                          sleeper=lambda _:None)
        self.assertEqual(len(attempts),3)

    def test_network_timeout_retries_then_classifies_failure(self):
        attempts=[]
        def opener(url,timeout):
            attempts.append(url)
            raise TimeoutError("read operation timed out")
        with self.assertRaisesRegex(ValueError,"network/read unavailable"):
            checker.probe("20261001T000000Z",opener=opener,
                          sleeper=lambda _:None)
        self.assertEqual(len(attempts),3)

    def test_read_timeout_retries_and_does_not_skip_suite(self):
        attempts=[]
        def opener(url,timeout):
            attempts.append(url)
            suite=url.split("/dists/")[1].split("/")[0]
            return Response(url,envelope(suite),error=TimeoutError("read timeout"))
        with self.assertRaisesRegex(ValueError,"network/read unavailable"):
            checker.probe("20261001T000000Z",opener=opener,
                          sleeper=lambda _:None)
        self.assertEqual(len(attempts),3)

    def test_empty_truncated_invalid_suite_and_redirect_refused(self):
        for kind in ("empty","truncated","missing-signature","wrong-suite",
                     "redirect","wrong-content-length","oversized"):
            attempts=[]
            def opener(url,timeout):
                attempts.append(url)
                suite=url.split("/dists/")[1].split("/")[0]
                body=envelope(suite)
                kwargs={}
                if kind=="empty": body=b""
                elif kind=="truncated": body=body[:400]
                elif kind=="missing-signature": body=body.split(
                    b"-----BEGIN PGP SIGNATURE-----")[0]
                elif kind=="wrong-suite": body=envelope("wrong-suite")
                elif kind=="redirect": kwargs["final_url"]="https://evil.test/other"
                elif kind=="wrong-content-length": kwargs["declared"]=len(body)+3
                elif kind=="oversized": body=b"x"*(checker.MAX_INRELEASE_BYTES+1)
                return Response(url,body,**kwargs)
            with self.subTest(kind=kind), self.assertRaisesRegex(
                    ValueError,"InRelease|redirected|suite"):
                checker.probe("20261001T000000Z",opener=opener,
                              sleeper=lambda _:None)
            self.assertEqual(len(attempts),1)

    def test_sources_require_exact_two_signed_stanzas(self):
        raw=(ROOT/"deploy/release/ubuntu.sources").read_text()
        checker.validate_sources(raw,"20261001T000000Z")
        for poisoned in (
            raw+"\nTypes: deb\nURIs: http://untrusted.test\n",
            raw.replace("Signed-By:", "Signed-Of:"),
            raw.replace("Architectures: amd64","Architectures: arm64"),
            raw.replace("https://security.ubuntu.com",
                        "http://security.ubuntu.com"),
        ):
            with self.subTest(change=poisoned[-60:]), self.assertRaises(ValueError):
                checker.validate_sources(poisoned,"20261001T000000Z")

    def test_streaming_read_cannot_extend_global_deadline_indefinitely(self):
        moment = [0.0]
        class DelayedResponse(Response):
            def read(self,n):
                moment[0] = 150.0
                return b"a"
        def opener(url,timeout):
            return DelayedResponse(url,envelope("noble"))
        with self.assertRaisesRegex(ValueError,"budget expired"):
            checker.probe("20261001T000000Z",
                          opener=opener, sleeper=lambda _:None,
                          clock=lambda:moment[0])

    def test_bad_snapshot_identity_rejected_before_network(self):
        with self.assertRaisesRegex(ValueError,"invalid pinned"):
            checker.probe("20261001T000000Z/../latest",
                          opener=lambda *a,**kw:self.fail("network called"))

    def test_main_failure_report_survives_invalid_sources(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            policy=root/"policy.json"
            sources=root/"sources"
            output=root/"evidence.json"
            policy.write_text(json.dumps({"apt_snapshot":"20261001T000000Z"}))
            sources.write_text("Types: deb\nURIs: http://unsafe.test\n")
            original_argv=checker.__dict__.get("_test_argv")
            import sys
            old=sys.argv
            try:
                sys.argv=["snapshot", "--policy",str(policy),
                          "--sources",str(sources),"--output",str(output)]
                self.assertEqual(checker.main(),2)
            finally:
                sys.argv=old
            report=json.loads(output.read_text())
            self.assertFalse(report["signature_verified"])
            self.assertTrue(report["unavailable_or_invalid"])

if __name__=="__main__":
    unittest.main()
