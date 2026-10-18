"""Known real-file SHA1/SHA256 must match bytes, not checksum shape."""
from __future__ import annotations
import hashlib
import importlib.util
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location(
    "release1_file_hash_witness",
    ROOT/"scripts/check-release-scanner-content.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ScannerRealFileHashTests(unittest.TestCase):
    FILE=b"fixture constant regular file contents\x00\n"

    def record(self, *, sha1=None, sha256=None, path="./usr/bin/dpkg"):
        one=sha1 if sha1 is not None else hashlib.sha1(self.FILE).hexdigest()
        two=sha256 if sha256 is not None else hashlib.sha256(self.FILE).hexdigest()
        return {"files":[{"fileName":path,"checksums":[
            {"algorithm":"SHA1","checksumValue":one},
            {"algorithm":"SHA256","checksumValue":two}]}]}

    def test_independently_hashed_fixture_matches(self):
        result=module.file_checksum_evidence(self.record(),self.FILE)
        self.assertTrue(result["sha1_verified"])
        self.assertTrue(result["sha256_verified"])
        self.assertFalse(result["public_bot_release"])

    def test_hash_shapes_dont_replace_real_content_identity(self):
        for corrupted in (
            self.record(sha1="f"*40),
            self.record(sha1="0"*40),
            self.record(sha256="e"*64),
            {"files":[{"fileName":"./usr/bin/dpkg",
                       "checksums":[{"algorithm":"SHA256",
                                     "checksumValue":hashlib.sha256(self.FILE).hexdigest()}]}]},
            self.record(path="../../usr/bin/dpkg"),
            {"files":[self.record()["files"][0]]*2},
        ):
            with self.subTest(corruption=corrupted):
                with self.assertRaises(ValueError):
                    module.file_checksum_evidence(corrupted,self.FILE)


if __name__=="__main__":
    unittest.main()
