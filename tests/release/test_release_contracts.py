"""Hermetic RELEASE-1 fail-closed policy and staging controls."""
from __future__ import annotations
import copy
import importlib.util
import json
import tempfile
import subprocess
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def module(path,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

builder=module("scripts/build-release-candidate.py","release1_builder")
verifier=module("scripts/verify-release-candidate.py","release1_verifier")
oci=module("scripts/verify-release-oci.py","release1_oci")
policy=module("scripts/release-promotion-policy.py","release1_promotion")


class OfflineReleaseContracts(unittest.TestCase):
    def test_all_live_release_permissions_are_frozen_false(self):
        result=policy.audit()
        self.assertFalse(result["public_release_authorized"])
        self.assertFalse(result["artifact_distribution_authorized"])
        checks=[
            ("qualification/release-image-v1.json",["public_bot_release",
                 "allow_real_credentials","deployment_host_qualified"]),
            ("qualification/license-compliance-v1.json",["legal_review_completed",
                 "artifact_distribution_authorized","controller_distribution_license_approved"]),
            ("qualification/release-qualification-v1.json",
                 ["release_promotion_authorized","artifact_distribution_authorized"]),
        ]
        for path, names in checks:
            document=json.loads((ROOT/path).read_text())
            for name in names:
                self.assertIs(document[name],False)

    def test_cannot_whitelist_unauthorized_distribution_through_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            scratch=Path(temp)
            for p in ("release-image-v1.json","license-compliance-v1.json",
                      "release-qualification-v1.json"):
                target=scratch/"qualification"/p
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes((ROOT/"qualification"/p).read_bytes())
            doc=scratch/"qualification/license-compliance-v1.json"
            bad=json.loads(doc.read_text())
            bad["artifact_distribution_authorized"]=True
            doc.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError,"artifact_distribution_authorized"):
                policy.audit(scratch)

    def test_manifest_rejects_path_traversal_and_bad_digest(self):
        for name in ("allfather/../../root/.ssh/id_rsa",
                     "/opt/bridge/lib/lichess.py", "tools/../bridge/lib/config.py",
                     "bridge\\\\lib\\\\lichess.py","unlisted/file"):
            with self.subTest(path=name),self.assertRaises(ValueError):
                verifier._safe_name(name)
        with self.assertRaises(ValueError):
            oci.require(False,"missing OCI digest")

    def test_manifest_requires_packaged_policy_before_accepting_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ("allfather","bridge","tools","wheels"):
                (root/name).mkdir()
            file=root/"allfather/probe"
            file.write_text("trusted")
            result={"schema_version":1,"release_kind":"RELEASE-1-OFFLINE-CANDIDATE",
                    "public_bot_release":False,"artifact_distribution_authorized":False,
                    "network_recovery_qualified":False,"deployment_host_qualified":False,
                    "source_commit":"a"*40,"source_tree":"b"*40,"bridge_commit":"c"*40,
                    "files":[{"path":"allfather/probe","sha256":builder.digest(file),
                              "bytes":file.stat().st_size}]}
            with self.assertRaises((ValueError, OSError)):
                verifier.verify(result,verifier.stage_roots(root),installed=False,policy={})

    def test_pinned_base_and_apt_snapshot_are_not_placeholders(self):
        doc=json.loads((ROOT/"qualification/release-image-v1.json").read_text())
        self.assertRegex(doc["base_image"],r"^docker\.io/library/ubuntu@sha256:[0-9a-f]{64}$")
        self.assertRegex(doc["apt_snapshot"],r"^[0-9]{8}T[0-9]{6}Z$")
        src=(ROOT/"deploy/release/ubuntu.sources").read_text()
        self.assertEqual(src.count("Snapshot: "+doc["apt_snapshot"]),2)
        self.assertIn("Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg",src)
        self.assertFalse(doc["os_package_lock_complete"])
        self.assertFalse(doc["wheel_lock_complete"])
        self.assertTrue(doc["ca_bootstrap_signed_http_only"])
        self.assertFalse(doc["ca_bootstrap_snapshot_locked"])
        bootstrap=(ROOT/doc["ca_bootstrap_sources"]).read_text()
        self.assertEqual(bootstrap.count("Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg"),2)
        self.assertIn("http://archive.ubuntu.com/ubuntu",bootstrap)
        self.assertIn("http://security.ubuntu.com/ubuntu",bootstrap)
        for forbidden in ("trusted=yes", "allow-unauthenticated", "Verify-Peer=false",
                          "Verify-Host=false"):
            self.assertNotIn(forbidden,bootstrap)
        docker=(ROOT/"deploy/release/Dockerfile").read_text()
        self.assertLess(docker.index("COPY deploy/release/bootstrap.sources"),
                        docker.index("COPY deploy/release/ubuntu.sources"))
        self.assertIn("test -s /etc/ssl/certs/ca-certificates.crt",docker)

    def test_pinned_snapshot_retries_are_bounded_without_mirror_fallback(self):
        docker=(ROOT/"deploy/release/Dockerfile").read_text()
        self.assertEqual(docker.count("Acquire::Retries=2"),2)
        self.assertEqual(docker.count("Acquire::https::Timeout=30"),2)
        self.assertIn("apt-get",docker)
        self.assertIn("COPY deploy/release/ubuntu.sources",docker)
        instructions = "\n".join(
            line for line in docker.splitlines()
            if not line.lstrip().startswith("#"))
        for unsafe in ("--fix-missing", "trusted=yes", "allow-unauthenticated",
                       "Acquire::https::Verify-Peer=false",
                       "Acquire::https::Verify-Host=false"):
            self.assertNotIn(unsafe,instructions)

    def test_sbom_generator_is_reviewed_immutable_oci_index(self):
        image=json.loads((ROOT/"qualification/release-image-v1.json").read_text())
        scanner=image["sbom_generator_image"]
        self.assertRegex(scanner,
            r"^docker\.io/docker/buildkit-syft-scanner@sha256:[0-9a-f]{64}$")
        content=(ROOT/".github/workflows/release-candidate.yml").read_text()
        self.assertIn('["sbom_generator_image"]',content)
        self.assertIn('--attest "type=sbom,generator=$SCANNER"',content)
        self.assertIn('docker buildx imagetools inspect "$SCANNER" --raw',content)
        self.assertNotIn("--attest type=sbom \\",content)

    def test_readonly_runtime_requires_separate_replay_bind_mount(self):
        p=json.loads((ROOT/"qualification/release-image-v1.json").read_text())
        runtime=json.loads((ROOT/"deploy/release/runtime-contract.json").read_text())
        mount="/opt/allfather/build/replays-online-hybrid-v2"
        self.assertEqual(p["required_mounts"]["replay"],mount)
        self.assertEqual(runtime["replay_mount"],mount)
        self.assertIn("replays:/opt/allfather/build/replays-online-hybrid-v2:rw",
                      (ROOT/".github/workflows/release-candidate.yml").read_text())

    def test_bytecode_or_transient_code_never_enters_image_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            cached=root/"allfather/scripts/__pycache__/unsafe.cpython-312.pyc"
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b"transient")
            with self.assertRaisesRegex(ValueError,"forbidden transient path"):
                builder.checksum_tree(root)

    def test_oci_index_metadata_without_config_uses_exact_loaded_image(self):
        digest="sha256:"+"a"*64
        observed={"Id":digest, "Architecture":"amd64"}
        self.assertTrue(oci.verify_image_config_link(
            image_config_digest=digest,
            metadata_config_digest=None,
            docker_inspection=observed))
        for metadata, inspection in (
            (None, None),
            ("sha256:"+"b"*64, observed),
            (None, {"Id":"sha256:"+"b"*64,"Architecture":"amd64"}),
            (None, {"Id":digest,"Architecture":"arm64"}),
            (None, {"Id":digest,"Architecture":"amd64","Os":"windows"}),
        ):
            with self.subTest(metadata=metadata, inspected=inspection), self.assertRaises(ValueError):
                oci.verify_image_config_link(
                    image_config_digest=digest,
                    metadata_config_digest=metadata,
                    docker_inspection=inspection)

    def test_ca_bootstrap_is_bound_to_source_manifest(self):
        self.assertIn("deploy/release/bootstrap.sources",builder.SOURCE_FILES)

    def test_only_tracked_upstream_bridge_files_enter_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            upstream=root/"upstream"
            upstream.mkdir()
            subprocess.run(["git","init","--quiet",str(upstream)],check=True)
            (upstream/"safe.py").write_text("print('tracked')\\n")
            subprocess.run(["git","-C",str(upstream),"add","safe.py"],check=True)
            (upstream/".env").write_text("SECRET=NEVER_COPY")
            target=root/"export"
            builder.copy_tracked_bridge(upstream,target)
            self.assertEqual((target/"safe.py").read_text(),
                             (upstream/"safe.py").read_text())
            self.assertFalse((target/".env").exists())

    def test_secret_named_file_is_rejected_even_from_other_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)
            (source/"bridge").mkdir()
            (source/"bridge/config.yml").write_text("token: secret")
            with self.assertRaisesRegex(ValueError,"secret-bearing filename"):
                builder.checksum_tree(source)

    def test_replay_data_can_only_escape_source_manifest_under_a_real_mount(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)/"allfather"
            replay=base/"build/replays-online-hybrid-v2"
            replay.mkdir(parents=True)
            generated=replay/"from-an-earlier-game.json"
            generated.write_text("{}")
            self.assertFalse(verifier.installed_mutable_replay(
                "allfather",base,generated,installed=False))
            self.assertFalse(verifier.installed_mutable_replay(
                "bridge",base,generated,installed=True))
            with self.assertRaisesRegex(ValueError,"separately mounted storage"):
                verifier.installed_mutable_replay(
                    "allfather",base,generated,installed=True)
            source=base/"controller/runtime.py"
            source.parent.mkdir(parents=True)
            source.write_text("print('not replay')")
            self.assertFalse(verifier.installed_mutable_replay(
                "allfather",base,source,installed=True))

    def test_source_tree_staging_requires_clean_exact_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                builder.assemble(Path(temp),Path(temp)/"out",Path(temp),
                                 Path(temp),Path(temp))


    def test_every_authentication_and_host_release_flag_fails_policy(self):
        source = ROOT / "qualification/release-qualification-v1.json"
        for flag in ("authenticated_publication_authorized",
                     "destination_host_qualified", "public_bot_release",
                     "artifact_distribution_authorized",
                     "release_promotion_authorized"):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                for name in ("release-image-v1.json",
                             "release-qualification-v1.json",
                             "license-compliance-v1.json"):
                    target = root / "qualification" / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((ROOT / "qualification" / name).read_bytes())
                target = root / "qualification/release-qualification-v1.json"
                policy_doc = json.loads(target.read_text())
                policy_doc[flag] = True
                target.write_text(json.dumps(policy_doc))
                with self.assertRaisesRegex(ValueError, flag):
                    policy.audit(root)

    def test_replay_mount_documentation_matches_ci(self):
        ops = (ROOT / "docs/RELEASE_1_OPERATIONS.md").read_text()
        workflow = (ROOT / ".github/workflows/release-candidate.yml").read_text()
        replay = ":/opt/allfather/build/replays-online-hybrid-v2:rw"
        self.assertIn(replay, ops)
        self.assertIn(replay, workflow)
        for flag in ("--network none", "--read-only", "--cap-drop ALL",
                     "--security-opt no-new-privileges", "10001:10001"):
            self.assertIn(flag, ops)


if __name__=="__main__":
    unittest.main()
