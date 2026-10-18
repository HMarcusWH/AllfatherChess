"""RELEASE-2A real-byte dependency observation, no dependency lock promotion."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
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

    def test_wheel_manifest_is_bound_to_all_exact_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            temp=Path(td)
            stage=temp/"stage"
            folder=stage/"wheels"
            folder.mkdir(parents=True)
            src=temp/"source.lock"
            src.write_text("chess==1.11.2 --hash=sha256:dummy\n")
            lock=folder/"runtime-wheels.lock"
            lock.write_text("chess==1.11.2 --hash=sha256:dummy\n")
            wheels={}
            for i in range(12):
                name=f"example{i}-1.0-py3-none-any.whl"
                path=folder/name
                path.write_bytes(f"wheel fixture {i}".encode())
                wheels[name]=audit.digest_file(path)
            witness={"schema_version":1,"wheels":wheels,
                     "source_lock_sha256":audit.digest_file(src),
                     "runtime_lock_sha256":audit.digest_file(lock)}
            attest=folder/"wheel-attestation.json"
            attest.write_text(json.dumps(witness))
            manifest={"wheel_attestation_sha256":audit.digest_file(attest),
                      "runtime_wheel_lock_sha256":audit.digest_file(lock)}
            reported=audit.inspect_wheelhouse(stage,src,manifest)
            self.assertEqual(reported["wheel_count"],12)
            self.assertGreater(reported["wheel_total_bytes"],0)
            self.assertEqual(reported["wheel_attestation_sha256"],
                             manifest["wheel_attestation_sha256"])
            victim=folder/"example1-1.0-py3-none-any.whl"
            victim.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError,"wheel bytes"):
                audit.inspect_wheelhouse(stage,src,manifest)
            victim.unlink()
            outside=temp/"external.whl"
            outside.write_bytes(b"wheel fixture 1")
            victim.symlink_to(outside)
            with self.assertRaisesRegex(ValueError,"linked"):
                audit.inspect_wheelhouse(stage,src,manifest)
            victim.unlink()
            victim.write_bytes(b"wheel fixture 1")
            (folder/"rogue-1.0-py3-none-any.whl").write_bytes(b"rogue")
            with self.assertRaisesRegex(ValueError,"files differ"):
                audit.inspect_wheelhouse(stage,src,manifest)
            (folder/"rogue-1.0-py3-none-any.whl").unlink()
            src.write_bytes(b"changed source requirements")
            with self.assertRaisesRegex(ValueError,"source requirement"):
                audit.inspect_wheelhouse(stage,src,manifest)

    def test_observation_has_no_automatic_release_grant(self):
        content=(ROOT/"scripts/observe-release-dependencies.py").read_text()
        self.assertIn('"dependency_lock_approved":False',content)
        self.assertIn('"cross_run_reproducibility_qualified":False',content)
        self.assertIn('"offline_candidate_qualified":False',content)
        self.assertIn('"artifact_distribution_authorized":False',content)
        self.assertIn('"public_bot_release":False',content)


if __name__=="__main__":
    unittest.main()
