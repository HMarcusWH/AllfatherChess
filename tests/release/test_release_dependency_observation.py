"""RELEASE-2A real-byte dependency observation, no dependency lock promotion."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location(
    "release2a_observer",ROOT/"scripts/observe-release-dependencies.py")
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class DependencyObservationTests(unittest.TestCase):
    def os_inventory(self):
        return ("apt=2.8.3\nca-certificates=20260601~24.04.1\n"
                "libopenblas0-pthread=0.3.26\npython3=3.12.3\n").encode()

    def python_inventory(self):
        return ("chess==1.11.2\npip==24.0\nPyYAML==6.0.3\n"
                "requests==2.34.2\n").encode()

    def test_canonical_os_and_python_inventory_positive(self):
        self.assertEqual(audit.parse_os_inventory(
            self.os_inventory(),["apt","ca-certificates","python3"],
            minimum=3),4)
        self.assertEqual(audit.parse_python_inventory(
            self.python_inventory(),minimum=3),4)

    def test_os_mutations_reject_unpinned_nonnative_and_missing_packages(self):
        base=self.os_inventory()
        bad=(
            base.replace(b"apt=2.8.3\n",b""),
            base+b"apt=2.8.3\n",
            base.replace(b"python3=3.12.3",b"python3=nonversion"),
            base.replace(b"python3=",b"python3:i386="),
            base.replace(b"ca-certificates=",b"ca-certificates=http://"),
            base.replace(b"libopenblas0-pthread=0.3.26\n",b""),
            base.replace(b"apt=2.8.3\n",b"apt=2.8.3\r\n"),
            base.rstrip(b"\n"),
        )
        for case in bad:
            with self.subTest(case=case[:90]),self.assertRaises(ValueError):
                audit.parse_os_inventory(case,[
                    "apt","ca-certificates","python3",
                    "libopenblas0-pthread"],minimum=3)

    def test_python_inventory_refuses_duplicate_url_or_unpinned_row(self):
        base=self.python_inventory()
        bad=(
            base+b"requests==2.34.2\n",
            base.replace(b"chess==1.11.2",b"chess @ https://private"),
            base.replace(b"pip==24.0",b"pip=="),
            base.replace(b"requests==2.34.2\n",b""),
            base.replace(b"PyYAML==6.0.3",b"PyYAML==6.0.3 PRIVATE"),
            base.replace(b"chess==1.11.2\n",b"chess==1.11.2\r\n"),
        )
        for case in bad:
            with self.subTest(case=case[:90]),self.assertRaises(ValueError):
                audit.parse_python_inventory(case,minimum=3)

    def test_release_policy_stays_strictly_offline_and_unapproved(self):
        policy=json.loads((ROOT/audit.POLICY).read_text())
        audit.validate_release_policy(policy)
        for field,value in (
            ("public_bot_release",True),
            ("wheel_lock_complete",True),
            ("os_package_lock_complete",True),
            ("allow_real_credentials",True),
            ("exact_os_package_inventory_sha256","a"*64),
            ("base_image","ubuntu:latest"),
        ):
            with self.subTest(field=field):
                changed=copy.deepcopy(policy)
                changed[field]=value
                with self.assertRaises(ValueError):
                    audit.validate_release_policy(changed)

    @staticmethod
    def make_wheel(path: Path, package: str, version: str = "1.0",
                   *, metadata_name: str | None = None,
                   metadata_version: str | None = None) -> None:
        with zipfile.ZipFile(path,"w",compression=zipfile.ZIP_STORED) as bundle:
            dist=f"{package}-{version}.dist-info"
            bundle.writestr(f"{dist}/METADATA",
                "Metadata-Version: 2.1\n"
                f"Name: {metadata_name or package}\n"
                f"Version: {metadata_version or version}\n\n")
            bundle.writestr(f"{dist}/WHEEL",
                            "Wheel-Version: 1.0\nGenerator: synthetic-test\n"
                            "Root-Is-Purelib: true\nTag: py3-none-any\n")

    def test_wheel_manifest_is_bound_to_all_exact_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            temp=Path(td)
            stage=temp/"stage"
            folder=stage/"wheels"
            folder.mkdir(parents=True)
            src=temp/"source.lock"
            src.write_text("chess==1.0 --hash=sha256:dummy\n")
            lock=folder/"runtime-wheels.lock"
            lock.write_text("chess==1.0 --hash=sha256:dummy\n")
            names=["chess","pyyaml","requests"]+[f"example{i}" for i in range(9)]
            installed=("\n".join(sorted(
                [f"{name}==1.0" for name in names]+["pip==24.0"]))+"\n").encode()
            wheels={}
            for name in names:
                filename=f"{name}-1.0-py3-none-any.whl"
                path=folder/filename
                self.make_wheel(path,name)
                wheels[filename]=audit.digest_file(path)
            witness={"schema_version":1,"wheels":wheels,
                     "source_lock_sha256":audit.digest_file(src),
                     "runtime_lock_sha256":audit.digest_file(lock)}
            attest=folder/"wheel-attestation.json"
            attest.write_text(json.dumps(witness))
            manifest={"wheel_attestation_sha256":audit.digest_file(attest),
                      "runtime_wheel_lock_sha256":audit.digest_file(lock)}
            def check(installed_raw=installed):
                return audit.inspect_wheelhouse(
                    stage,src,manifest,installed_inventory=installed_raw)
            reported=check()
            self.assertEqual(reported["wheel_count"],12)
            self.assertEqual(reported["installer_distribution_exceptions"],1)
            self.assertTrue(reported["installed_wheel_versions_verified"])
            self.assertTrue(reported["wheelhouse_closed"])
            self.assertGreater(reported["wheel_total_bytes"],0)
            self.assertEqual(reported["wheel_attestation_sha256"],
                             manifest["wheel_attestation_sha256"])
            for wrong in (
                installed.replace(b"chess==1.0",b"chess==9.99"),
                installed.replace(b"requests==1.0\n",b""),
                installed.replace(b"pip==24.0\n",b""),
                installed+b"unattested==1.0\n",
            ):
                with self.subTest(bad_installed=wrong[:100]),self.assertRaises(
                        ValueError):
                    check(wrong)
            victim=folder/"example1-1.0-py3-none-any.whl"
            original=victim.read_bytes()
            victim.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError,"wheel bytes"):
                check()
            victim.unlink()
            outside=temp/"external.whl"
            outside.write_bytes(original)
            victim.symlink_to(outside)
            with self.assertRaisesRegex(ValueError,"wheelhouse.*linked"):
                check()
            victim.unlink()
            victim.write_bytes(original)
            # Extra .pth/.py, arbitrary files, directories or links enter
            # the staged image even though none match the *.whl glob.
            for extra in ("injected.pth","sitecustomize.py","untracked.txt",
                          "untracked-1.0-py3-none-any.whl","nested"):
                with self.subTest(extra=extra):
                    target=folder/extra
                    if extra=="nested":
                        target.mkdir()
                    else:
                        target.write_text("unapproved payload")
                    with self.assertRaisesRegex(ValueError,"wheelhouse"):
                        check()
                    if target.is_dir():
                        target.rmdir()
                    else:
                        target.unlink()
            extra=folder/"extra-link"
            extra.symlink_to(outside)
            with self.assertRaisesRegex(ValueError,"wheelhouse"):
                check()
            extra.unlink()
            src.write_bytes(b"changed source requirements")
            with self.assertRaisesRegex(ValueError,"source requirement"):
                check()

    def test_wheel_metadata_must_match_filename_and_version(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            item=root/"chess-1.0-py3-none-any.whl"
            self.make_wheel(item,"chess")
            self.assertEqual(audit.wheel_distribution_version(item),
                             ("chess","1.0"))
            for name,version in (("another-package","1.0"),
                                 ("chess","9.9")):
                with self.subTest(name=name,version=version):
                    self.make_wheel(item,"chess",metadata_name=name,
                                    metadata_version=version)
                    with self.assertRaisesRegex(ValueError,"METADATA name/version"):
                        audit.wheel_distribution_version(item)
            with zipfile.ZipFile(item,"w") as z:
                z.writestr("chess-1.0.dist-info/METADATA",
                           "Name: chess\nVersion: 1.0\n")
                z.writestr("other-1.0.dist-info/METADATA",
                           "Name: other\nVersion: 1.0\n")
            with self.assertRaisesRegex(ValueError,"METADATA identity"):
                audit.wheel_distribution_version(item)

    def test_observation_has_no_automatic_release_grant(self):
        content=(ROOT/"scripts/observe-release-dependencies.py").read_text()
        self.assertIn('"dependency_lock_approved":False',content)
        self.assertIn('"cross_run_reproducibility_qualified":False',content)
        self.assertIn('"offline_candidate_qualified":False',content)
        self.assertIn('"artifact_distribution_authorized":False',content)
        self.assertIn('"public_bot_release":False',content)


if __name__=="__main__":
    unittest.main()
