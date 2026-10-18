"""Offline SPDX v2 license grammar, frozen identifier and negative controls."""
from __future__ import annotations
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "release_spdx_licenses", ROOT / "scripts/spdx_license_contract.py")
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class SPDXLicenseExpressionTests(unittest.TestCase):
    def test_supported_expressions(self):
        values = (
            "MIT", "Apache-2.0 OR MIT",
            "(MIT OR Apache-2.0) AND LicenseRef-Local",
            "GPL-2.0-only WITH Classpath-exception-2.0",
            "DocumentRef-external:LicenseRef-Local",
            "GPL-2.0-or-later AND MIT",
        )
        # NONE and NOASSERTION are whole-field sentinels only.
        for value in values + ("NONE", "NOASSERTION"):
            with self.subTest(value=value):
                self.assertTrue(contract.valid_license_expression(value))

    def test_malformed_and_unknown_expressions(self):
        for value in (
            "not a license expression", "MIT AND", "MIT OR OR Apache-2.0",
            "MIT WITH", "NONE OR MIT", "NOASSERTION AND MIT",
            "GPL-2.0-only WITH not-an-exception", "MIT WITH MIT",
            "(MIT OR Apache-2.0", "(MIT) WITH Classpath-exception-2.0",
            "LicenseRef-", "DocumentRef-:LicenseRef-X",
            "DefinitelyNotAnSPDXLicense-999.0",
            "MIT\nOR Apache-2.0", "MIT\tAND Apache-2.0",
            "MIT OR PRIVATE_CANARY", "MIT OR (MIT OR )",
        ):
            with self.subTest(value=value):
                self.assertFalse(contract.valid_license_expression(value))

    def test_frozen_spdx_catalogue_contains_canonical_identifiers(self):
        licenses, exceptions = contract.approved_identifiers()
        self.assertIn("MIT", licenses)
        self.assertIn("Apache-2.0", licenses)
        self.assertIn("Classpath-exception-2.0", exceptions)
        self.assertGreaterEqual(len(licenses), 700)
        self.assertGreaterEqual(len(exceptions), 80)
        self.assertFalse(contract.valid_license_expression(
            "PRIVATE_CANARY"))

    def test_input_bounds_and_invalid_types(self):
        for value in (None, True, 0, "", "MIT ", " MIT",
                      "MIT OR " + "MIT OR " * 300 + "Apache-2.0"):
            self.assertFalse(contract.valid_license_expression(value))


if __name__ == "__main__":
    unittest.main()
