"""Current profile/status claims must agree with actual source contracts."""
from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location(
    "current_status_contract",ROOT/"scripts/check-current-status.py")
status=importlib.util.module_from_spec(spec)
spec.loader.exec_module(status)


class CurrentStatusTests(unittest.TestCase):
    def test_current_status_is_derived_from_active_source(self):
        observed=status.check(ROOT)
        self.assertEqual(observed["canonical_lc0_matrix_profile"],
                         "b4-p0-c256k-cold")
        self.assertTrue(observed["status_document_synchronized"])
        self.assertFalse(observed["public_bot_release"])

    def test_b4_and_release_boundary_mutations_fail(self):
        contract=json.loads((ROOT/status.CONTRACT).read_text())
        sources=[
            status.CONTRACT,
            contract["canonical_selection"],
            contract["canonical_resource_catalog"],
            contract["canonical_runtime"],
            contract["release_image_policy"],
            contract["release_qualification_policy"],
            contract["distribution_policy"],
            contract["summary_file"],
        ]
        with tempfile.TemporaryDirectory() as d:
            tmp=Path(d)
            for name in sources:
                target=tmp/name
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes((ROOT/name).read_bytes())
            self.assertTrue(status.check(tmp)["source_claims_only"])
            for file,patch in (
                (contract["canonical_selection"],
                 lambda x:x["selected"]["lc0"].update(matrix_profile="b7-p8-c256k-warm64")),
                (contract["canonical_resource_catalog"],
                 lambda x:x["profiles"][3]["options"][3].update(value=128)),
                (contract["canonical_runtime"],
                 lambda x:x["instances"]["lc0-shadow"]["options"].update(NNCacheSize=128)),
                (contract["release_image_policy"],
                 lambda x:x.update(public_bot_release=True)),
                (contract["release_qualification_policy"],
                 lambda x:x.update(offline_candidate_qualified=True)),
                (contract["distribution_policy"],
                 lambda x:x.update(controller_distribution_license_approved=True)),
            ):
                with self.subTest(path=file):
                    target=tmp/file
                    original=target.read_bytes()
                    j=json.loads(original)
                    patch(j)
                    target.write_text(json.dumps(j))
                    with self.assertRaises(ValueError):
                        status.check(tmp)
                    target.write_bytes(original)
            header=tmp/contract["summary_file"]
            original=header.read_bytes()
            header.write_text(original.decode().replace(
                "b4-p0-c256k-cold","b7-p8-c256k-warm64",1))
            with self.assertRaisesRegex(ValueError,"CURRENT_STATUS"):
                status.check(tmp)


if __name__=="__main__":
    unittest.main()
