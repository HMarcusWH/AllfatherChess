"""RELEASE-2B-2: private cache is a transport, not acquisition authority."""
from __future__ import annotations
import importlib.util,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location(
    "private_native_bundle",ROOT/"scripts/prepare-release-native-bundle.py")
mod=importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)

class NativeSourceCacheNegatives(unittest.TestCase):
    def test_lock_closure_contains_only_exact_signed_inputs(self):
        source,boot,suites,records,indices=mod.inputs(ROOT)
        self.assertEqual(len(records),29)
        self.assertEqual(len(indices),4)
        self.assertEqual(len(suites),4)
        self.assertEqual(set(suites),set(mod.SUITES))
        self.assertEqual(len(mod.expected_raw(suites,records,indices)),37)
        self.assertFalse(source["approved"])
        self.assertFalse(source["public_bot_release"])
        self.assertFalse(boot["artifact_distribution_authorized"])

    def fixture(self,dir):
        source,boot,suites,records,indices=mod.inputs(ROOT)
        synthetic={name:{
            "Package":record["name"],
            "Version":record["version"],
            "Architecture":record["architecture"],
            "Filename":origin["pool_filename"],
            "Size":str(origin["archive_size"]),
            "SHA256":record["sha256"],
        } for name,(_,record,origin) in records.items()}
        evidence=mod.expected_evidence(ROOT,suites,records,synthetic)
        raw=mod.expected_raw(suites,records,indices)
        for name in list(raw)+["evidence/"+e for e in evidence]+["bundle-identity.json"]:
            p=dir/name
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(b"x\n")
        return suites,records,indices,evidence,raw

    def test_bundle_requires_exact_raw_and_evidence_path_closure(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            suites,records,indices,evidence,raw=self.fixture(d)
            mod.expected_files(ROOT,d,suites,records,indices,evidence)
            (d/"unapproved-source").write_bytes(b"evil")
            with self.assertRaisesRegex(ValueError,"unexpected/missing"):
                mod.expected_files(ROOT,d,suites,records,indices,evidence)

    def test_bundle_refuses_symlinks_and_missing_archive(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td)
            suites,records,indices,evidence,raw=self.fixture(d)
            first=d/next(x for x in raw if x.startswith("archives/"))
            first.unlink()
            with self.assertRaisesRegex(ValueError,"unexpected/missing"):
                mod.expected_files(ROOT,d,suites,records,indices,evidence)
            first.symlink_to(d/"bundle-identity.json")
            with self.assertRaisesRegex(ValueError,"linked"):
                mod.expected_files(ROOT,d,suites,records,indices,evidence)

    def test_raw_blob_read_refuses_link_or_oversize(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"source"
            p.write_bytes(b"12345")
            self.assertEqual(mod.file(p,5),b"12345")
            with self.assertRaises(ValueError):mod.file(p,4)
            link=Path(td)/"link"
            link.symlink_to(p)
            with self.assertRaises(ValueError):mod.file(link,5)

    def test_no_floating_or_unclassified_network_fallback(self):
        for address in (
            "https://archive.ubuntu.com/ubuntu/dists/noble/InRelease",
            "http://snapshot.ubuntu.com/ubuntu/20261001T000000Z/dists/noble/InRelease",
            mod.BASE+"/../../evil",
        ):
            with self.subTest(address=address),patch.object(
                mod.urllib.request,"urlopen",side_effect=AssertionError("network")) as fn:
                with self.assertRaises(ValueError):
                    mod.fetch(address,10)
                fn.assert_not_called()

    def test_upstream_500_fails_without_retry(self):
        from urllib.error import HTTPError
        url=mod.BASE+"/dists/noble/InRelease"
        e=HTTPError(url,500,"broken",{},None)
        with patch.object(mod.urllib.request,"urlopen",side_effect=e) as fn:
            with self.assertRaisesRegex(ValueError,"HTTP 500 refused"):
                mod.fetch(url,100)
        fn.assert_called_once()
if __name__=="__main__":unittest.main()
