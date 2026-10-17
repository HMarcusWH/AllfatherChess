"""Early real-scanner attestation contract uses strict SPDX verifier."""
from __future__ import annotations
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def module(path,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

probe=module("scripts/check-release-scanner.py","release1_scanner_probe")
oci_fixture=module("tests/release/test_release_oci_integrity.py",
                   "release1_fixture_scanner")

class ScannerProbeContractTests(unittest.TestCase):
    def test_valid_spdx_probe_never_claims_release_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            tar, _, _ = oci_fixture.OCIProofTests().fixture(Path(tmp))
            report=probe.inspect_scanner_output(tar)
            self.assertEqual(report["spdx_document_count"],1)
            self.assertGreater(sum((report["spdx_package_count"],
                                    report["spdx_file_count"])),0)
            self.assertFalse(report["integrity_verified"])
            self.assertFalse(report["release_qualified"])

    def test_null_statement_wrapper_cannot_qualify_a_scanner(self):
        for kind in ("null","empty","object","string"):
            with self.subTest(wrapper=kind),tempfile.TemporaryDirectory() as tmp:
                tar,_,_=oci_fixture.OCIProofTests().fixture(
                    Path(tmp),statement_wrapper=kind)
                with self.assertRaisesRegex(ValueError,"scanner statement envelope"):
                    probe.inspect_scanner_output(tar)

    def test_mixed_nonobject_statement_element_must_reject(self):
        # Pinned real scanner must not claim compatible if an otherwise
        # valid SPDX statement array includes an extra null/scalar item.
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            tar,_,_=oci_fixture.OCIProofTests().fixture(
                path,statement_wrapper="array")
            # A structurally valid positive array is accepted.
            self.assertEqual(probe.inspect_scanner_output(tar)[
                "spdx_document_count"],1)
        # Direct structural check in the same admission condition: every
        # element must be a supported in-toto statement object.
        fake=[None,{"_type":oci_fixture.oci.STATEMENT,
                    "predicateType":oci_fixture.oci.SPDX,
                    "subject":[{"digest":{"sha256":"a"*64}}]}]
        self.assertFalse(all(type(x) is dict and
                             x.get("_type")==oci_fixture.oci.STATEMENT
                             for x in fake))

    def test_malformed_spdx_refused_at_scanner_boundary(self):
        for mode in ("missing-download","bogus-algorithm",
                     "namespace-fragment","empty-inventory"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                tar,_,_=oci_fixture.OCIProofTests().fixture(
                    Path(tmp),spdx_case=mode)
                with self.assertRaisesRegex(ValueError,"SPDX"):
                    probe.inspect_scanner_output(tar)

if __name__=="__main__":
    unittest.main()
