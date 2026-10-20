"""RELEASE-2B-2 OS-01/02/03/04/05 native lock and signed APT negatives."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location(
    "native_snapshot",ROOT/"scripts/verify-release-bootstrap-snapshot.py")
assert SPEC and SPEC.loader
native=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


class SignedNativeBootstrapTests(unittest.TestCase):
    def test_source_and_candidate_inventory_are_frozen(self):
        bootstrap, build, lock=native.policy(ROOT)
        self.assertEqual(len(lock["expected_final_inventory"]),120)
        self.assertEqual(bootstrap["candidate_ca"]["version"],"20260601~24.04.1")
        self.assertFalse(bootstrap["bootstrap_snapshot_proved"])
        self.assertFalse(build["lock_approved"])

    def mk_root(self, folder):
        for name in (native.BOOTSTRAP,native.NATIVE,native.OS_LOCK,native.RELEASE,
                     "qualification/release-python-builder-v1.json",
                     "deploy/release/bootstrap.sources","deploy/release/ubuntu.sources"):
            path=folder/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes((ROOT/name).read_bytes())

    def test_os05_floating_and_wrong_snapshot_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.mk_root(root)
            src=root/"deploy/release/bootstrap.sources"
            original=src.read_text()
            for replacement in (
                original.replace("Snapshot: 20261001T000000Z","",1),
                original.replace("20261001T000000Z","20261002T000000Z",1),
                original.replace("http://archive.ubuntu.com/ubuntu",
                                 "http://unauthorized.example/ubuntu"),
                original.replace("Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg",
                                 "Trusted: yes",1),
                original+"\nTypes: deb\nURIs: http://archive.ubuntu.com/ubuntu\n",
            ):
                with self.subTest(short=replacement[:100]):
                    src.write_text(replacement)
                    with self.assertRaises(ValueError):
                        native.policy(root)
            src.write_text(original)

    def test_os01_same_native_count_different_version_veto(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.mk_root(root)
            file=root/native.OS_LOCK
            lock=json.loads(file.read_text())
            self.assertEqual(len(lock["expected_final_inventory"]),120)
            lock["expected_final_inventory"][0]="apt=2.8.4"
            file.write_text(json.dumps(lock))
            with self.assertRaisesRegex(ValueError,"historical exact final native"):
                native.policy(root)

    def test_os02_different_base_or_architecture_veto(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.mk_root(root)
            file=root/native.NATIVE
            x=json.loads(file.read_text())
            for key, changed in (("platform","linux/arm64"),
                                 ("base_image",
                                  "docker.io/library/ubuntu@sha256:"+"a"*64)):
                wrong=copy.deepcopy(x)
                wrong[key]=changed
                file.write_text(json.dumps(wrong))
                with self.assertRaisesRegex(ValueError,"native lock input-closure"):
                    native.policy(root)

    def test_os03_ca_unreviewed_deb_hash_never_autopromotes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.mk_root(root)
            file=root/native.BOOTSTRAP
            x=json.loads(file.read_text())
            for key,value in (("version","20260601~24.04.2"),
                              ("deb_sha256","a"*64)):
                wrong=copy.deepcopy(x)
                wrong["candidate_ca"][key]=value
                file.write_text(json.dumps(wrong))
                with self.assertRaises(ValueError):
                    native.policy(root)

    def test_os04_prohibits_unsigned_or_untrusted_source(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.mk_root(root)
            file=root/"deploy/release/bootstrap.sources"
            src=file.read_text()
            for wrong in (
                src.replace("Signed-By:", "Untrusted-By:",1),
                src.replace("Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg",
                            "Signed-By: /tmp/forged.gpg",1),
                src+"# trusted=yes\n",
            ):
                file.write_text(wrong)
                with self.assertRaises(ValueError):
                    native.policy(root)

    def test_os06_07_mismatched_deb_and_index_unqualified(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.mk_root(root)
            proof=root/"proof";proof.mkdir()
            (proof/"base-packages.txt").write_text(
                "\n".join(f"base{i}=1.0" for i in range(21))+"\n")
            (proof/"bootstrap-packages.txt").write_text(
                (proof/"base-packages.txt").read_text()+
                "ca-certificates=20260601~24.04.1\nubuntu-keyring=2023.11.28.1\n")
            for name, value in {
                "keyring.sha256":"a"*64+"\n",
                "ca-deb.sha256":"b"*64+"\n",
                "ca-package-index.txt":"Package: ca-certificates\nVersion: 20260601~24.04.1\nArchitecture: all\nSHA256: "+"c"*64+"\n",
                "inrelease-sha256.txt":"d"*64+"  snapshot.ubuntu.com_ubuntu_20261001T000000Z_dists_noble_InRelease\n",
                "disposition.txt":"SIGNED_APT_SNAPSHOT_PROBE_COMPLETE\n"
            }.items():
                (proof/name).write_text(value)
            with self.assertRaises(ValueError):
                native.probe(ROOT,proof)

    def test_os08_claim_boundaries_remain_closed(self):
        a,b,c=native.policy(ROOT)
        self.assertFalse(a["public_bot_release"])
        self.assertFalse(b["lock_approved"])
        self.assertFalse(c["approved"])
        self.assertFalse(c["base_inventory_measured"])

if __name__=="__main__":
    unittest.main()
