"""RELEASE-2B-2 native dependency-stage source and destructive contracts."""
from __future__ import annotations
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location(
    "release2b2_native",ROOT/"scripts/verify-release-dependency-build.py")
assert spec and spec.loader
v=importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


class NativeDependencyBuildTests(unittest.TestCase):
    def test_production_image_inherits_same_single_dependency_stage(self):
        df=(ROOT/"deploy/release/Dockerfile").read_text()
        self.assertIn(" AS release-dependencies",df)
        self.assertIn("FROM release-dependencies AS release-application",df)
        self.assertEqual(df.count("/opt/online3b/bin/python -m pip install"),1)
        self.assertEqual(df.count("/bin/sh /usr/local/bin/probe-native-bootstrap --acquire-only"),1)
        self.assertEqual(df.count("/bin/sh /usr/local/bin/preinstall-native-gate bootstrap --install"),1)
        self.assertEqual(df.count("/bin/sh /usr/local/bin/preinstall-native-gate runtime --install"),1)
        self.assertEqual(df.count("RUN --network=none"),2)
        self.assertLess(df.index("preinstall-native-gate bootstrap --install"),
                        df.index("preinstall-native-gate runtime --install"))
        self.assertLess(df.index("AS release-dependencies"),
                        df.index("FROM release-dependencies AS release-application"))
        self.assertIn("COPY build/online3b-wheels/",df)
        self.assertNotIn("COPY build/release1-stage/wheels/",df)
        self.assertIn("scripts/verify-release-candidate.py --installed",df)

    def test_native_selections_are_exact_subset_of_full_inventory(self):
        lock=json.loads((ROOT/"qualification/release-os-packages.lock").read_text())
        rows=(ROOT/"qualification/release-os-apt-selections-v1.txt").read_text().splitlines()
        self.assertEqual(len(rows),10)
        self.assertEqual(len(set(rows)),10)
        self.assertEqual(set(rows).difference(lock["expected_final_inventory"]),set())
        self.assertEqual(len(lock["expected_final_inventory"]),120)
        self.assertFalse(lock["approved"])
        self.assertFalse(lock["public_bot_release"])

    def test_dated_signed_sources_have_no_unsafe_fallback(self):
        source=(ROOT/"deploy/release/prepare-direct-snapshot-sources.sh").read_text()
        install=(ROOT/"deploy/release/prepare-native-acquisition.sh").read_text()
        self.assertIn("snapshot.ubuntu.com/ubuntu/20261001T000000Z",source)
        self.assertIn("snapshot.ubuntu.com_ubuntu_20261001T000000Z",install)
        self.assertIn("gpgv --keyring",install)
        self.assertIn("--download-only",install)
        for forbidden in ("trusted=yes", "allow-unauthenticated",
                          "Verify-Peer=false", "Verify-Host=false"):
            self.assertNotIn(forbidden,install)

    def test_identical_package_count_but_changed_version_fails(self):
        lock=json.loads((ROOT/"qualification/release-os-packages.lock").read_text())
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/"native.txt"
            rows=lock["expected_final_inventory"].copy()
            rows[0]="apt=2.8.999"
            target.write_text("\n".join(rows)+"\n")
            with self.assertRaisesRegex(ValueError,"diverges"):
                v.exact_inventory(target,lock["expected_final_inventory"],"final")
            target.write_text("\n".join(lock["expected_final_inventory"])+"\n")
            self.assertEqual(len(v.exact_inventory(
                target,lock["expected_final_inventory"],"final")),120)

    def test_native_ledger_detects_unchanged_name_changed_hash(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);(d/"index").mkdir()
            (d/"native.tsv").write_text("openssl\t3.0.13-0ubuntu3.16\tamd64\t"+
                                        "b"*64+"\topenssl_3.0.13_amd64.deb\n")
            (d/"index/openssl.txt").write_text(
                "Package: openssl\nVersion: 3.0.13-0ubuntu3.16\n"
                "Architecture: amd64\nSHA256: "+"a"*64+"\n")
            with self.assertRaisesRegex(ValueError,"not in signed"):
                v.ledger(d,"native.tsv","index",{"openssl":"3.0.13-0ubuntu3.16"})

    def test_missing_or_extra_acquired_package_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);(d/"index").mkdir()
            (d/"native.tsv").write_text("openssl\t3.0.13-0ubuntu3.16\tamd64\t"+
                                        "b"*64+"\topenssl_3.0.13_amd64.deb\n")
            with self.assertRaises(ValueError):
                v.ledger(d,"native.tsv","index",{"openssl":"3.0.13-0ubuntu3.16",
                                                  "libssl3t64":"3.0.13-0ubuntu3.16"})

    def test_input_denials_preserve_public_release_prohibition(self):
        release=json.loads((ROOT/"qualification/release-image-v1.json").read_text())
        native=json.loads((ROOT/"qualification/release-native-build-v1.json").read_text())
        lock=json.loads((ROOT/"qualification/release-os-packages.lock").read_text())
        self.assertFalse(release["public_bot_release"])
        self.assertFalse(release["os_package_lock_complete"])
        self.assertFalse(release["wheel_lock_complete"])
        self.assertFalse(native["lock_approved"])
        self.assertFalse(lock["approved"])

if __name__=="__main__":
    unittest.main()
