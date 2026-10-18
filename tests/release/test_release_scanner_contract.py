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
    def test_real_scanner_declares_same_builder_identity_as_release(self):
        workflow=(ROOT/".github/workflows/release-candidate.yml").read_text()
        probe=workflow.split("  scanner-compatibility:",1)[1].split(
            "  release-oci-integration:",1)[0]
        self.assertIn(
            "--attest type=provenance,mode=min,builder-id="
            "https://github.com/HMarcusWH/AllfatherChess/actions/"
            "workflows/release-candidate.yml",probe)
        self.assertIn('--attest "type=sbom,generator=$SCANNER"',probe)

    def test_valid_spdx_probe_never_claims_release_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            tar, _, _ = oci_fixture.OCIProofTests().fixture(Path(tmp))
            report=probe.inspect_scanner_output(tar)
            self.assertEqual(report["spdx_document_count"],1)
            self.assertGreater(sum((report["spdx_package_count"],
                                    report["spdx_file_count"])),0)
            self.assertTrue(report["scanner_slsa_v1_contract_verified"])
            self.assertTrue(report["builder_identity_matched"])
            self.assertEqual(report["slsa_statement_count"],1)
            self.assertFalse(report["integrity_verified"])
            self.assertFalse(report["release_qualified"])

    def test_scanner_requires_one_exact_version_and_builder_provenance(self):
        variants=(
            ({"omit_slsa":True},"exactly one expected SLSA"),
            ({"slsa_case":"other-builder"},"builder identity"),
            ({"slsa_case":"v02"},"provenance version"),
            ({"duplicate_slsa":True},"exactly one expected SLSA"),
        )
        for kwargs,reason in variants:
            with self.subTest(kind=kwargs),tempfile.TemporaryDirectory() as td:
                archive,_,_=oci_fixture.OCIProofTests().fixture(Path(td),**kwargs)
                with self.assertRaisesRegex(ValueError,reason):
                    probe.inspect_scanner_output(archive)

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
        with tempfile.TemporaryDirectory() as tmp:
            tar,_,_=oci_fixture.OCIProofTests().fixture(
                Path(tmp),statement_wrapper="array-null")
            with self.assertRaisesRegex(ValueError,
                                        "scanner in-toto statement element"):
                probe.inspect_scanner_output(tar)

    def test_invalid_statement_subject_or_predicate_not_skipped(self):
        for variant,why in (("array-bad-subject","subject identity"),
                            ("array-empty-predicate","predicate missing")):
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as td:
                tar,_,_=oci_fixture.OCIProofTests().fixture(
                    Path(td),statement_wrapper=variant)
                with self.assertRaisesRegex(ValueError,why):
                    probe.inspect_scanner_output(tar)

    def test_unknown_in_toto_row_cannot_be_skipped_next_to_spdx(self):
        with tempfile.TemporaryDirectory() as td:
            tar,_,_=oci_fixture.OCIProofTests().fixture(
                Path(td),statement_wrapper="array-unknown")
            with self.assertRaisesRegex(ValueError,
                                        "scanner unsupported in-toto predicate"):
                probe.inspect_scanner_output(tar)

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
