"""Destructive OCI proof tests with actual tar/index/blob digests."""
from __future__ import annotations
import gzip
import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("release1_oci_integrity",
                                           ROOT/"scripts/verify-release-oci.py")
oci=importlib.util.module_from_spec(spec)
spec.loader.exec_module(oci)


def encode(obj):
    return json.dumps(obj,sort_keys=True,separators=(",",":")).encode()


def sha(body):
    return "sha256:"+hashlib.sha256(body).hexdigest()


class OCIProofTests(unittest.TestCase):
    def fixture(self, target: Path, *, replace_layer=False, bad_subject=False,
                bad_reference=False, corrupt_rootfs=False, subject_kind="image",
                metadata_kind="root", manifest_subject_kind="none",
                invalid_predicate=False, nested=True, wrong_size=None,
                slsa_case="valid", bad_metadata_descriptor=False):
        base = b"original uncompressed OCI application layer\n"
        expanded = (b"substituted uncompressed OCI application layer\n"
                    if replace_layer else base)
        blobs = {}

        def store(data):
            oid = sha(data)
            blobs["blobs/sha256/" + oid[7:]] = data
            return oid

        def descriptor(kind, digest, **extra):
            return {"mediaType": kind, "digest": digest,
                    "size": len(blobs["blobs/sha256/" + digest[7:]]), **extra}

        oci_manifest = "application/vnd.oci.image.manifest.v1+json"
        oci_index = "application/vnd.oci.image.index.v1+json"
        tar_layer = store(gzip.compress(expanded, mtime=0))
        diff_id = sha(b"mismatched expected diff" if corrupt_rootfs else base)
        config = store(encode({
            "architecture": "amd64", "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": [diff_id]},
            "config": {"User": "10001:10001",
                       "Entrypoint": ["/opt/online3b/bin/python",
                                      "/opt/release-tools/deploy/release/entrypoint.py"]}
        }))
        cfg_desc = descriptor("application/vnd.oci.image.config.v1+json", config)
        layer_desc = descriptor("application/vnd.oci.image.layer.v1.tar+gzip",
                                tar_layer)
        if wrong_size == "config":
            cfg_desc["size"] += 1
        if wrong_size == "layer":
            layer_desc["size"] += 1
        application = store(encode({
            "schemaVersion": 2, "config": cfg_desc, "layers": [layer_desc]}))
        app_desc = descriptor(oci_manifest, application,
                              platform={"os": "linux", "architecture": "amd64"})
        att_config = store(encode({"architecture": "unknown", "os": "unknown"}))
        attest_layers = []
        for predicate in ("https://spdx.dev/Document",
                          "https://slsa.dev/provenance/v1"):
            subject = {"image": application[7:], "config": config[7:],
                       "layer": tar_layer[7:], "empty": None}[subject_kind]
            if bad_subject:
                subject = "f" * 64
            body = ({"SPDXID": "SPDXRef-DOCUMENT", "spdxVersion": "SPDX-2.3"}
                    if predicate == "https://spdx.dev/Document" else {
                    "buildDefinition": {
                        "buildType": "https://mobyproject.org/buildkit@v1",
                        "externalParameters": {}},
                    "runDetails": {"builder": {
                        "id": "https://github.com/moby/buildkit"}}})
            if predicate.endswith("/v1"):
                if slsa_case == "empty":
                    body = {"buildDefinition": {}, "runDetails": {}}
                if slsa_case == "builder-empty":
                    body["runDetails"]["builder"]["id"] = ""
                if slsa_case == "bad-build-type":
                    body["buildDefinition"]["buildType"] = "not-a-uri"
                if slsa_case == "no-external":
                    del body["buildDefinition"]["externalParameters"]
            if invalid_predicate:
                body = {"schema_version": 1}
            statement = encode({
                "_type": "https://in-toto.io/Statement/v1",
                "predicateType": predicate,
                "subject": ([] if subject is None else
                            [{"name": "offline-fixture",
                              "digest": {"sha256": subject}}]),
                "predicate": body})
            lid = store(statement)
            layer = descriptor("application/vnd.in-toto+json", lid)
            if wrong_size == "statement":
                layer["size"] += 1
            attest_layers.append(layer)
        attest_data = {
            "schemaVersion": 2,
            "config": descriptor("application/vnd.oci.image.config.v1+json",
                                  att_config),
            "layers": attest_layers}
        if manifest_subject_kind != "none":
            attest_data["subject"] = {
                "mediaType": oci_manifest,
                "digest": (application if manifest_subject_kind == "image"
                           else "sha256:" + "f"*64)}
        attest_manifest = store(encode(attest_data))
        att_desc = descriptor(oci_manifest, attest_manifest,
            annotations={"vnd.docker.reference.type": "attestation-manifest",
                         "vnd.docker.reference.digest": (
                             "sha256:" + "f"*64 if bad_reference else application)},
            platform={"os": "unknown", "architecture": "unknown"})
        if wrong_size == "application":
            app_desc["size"] += 1
        children = [app_desc, att_desc]
        if nested:
            nested_id = store(encode({"schemaVersion": 2, "mediaType": oci_index,
                                      "manifests": children}))
            root_desc = descriptor(oci_index, nested_id)
            outer_children = [root_desc]
        else:
            root_desc = app_desc
            outer_children = children
        if wrong_size == "root":
            root_desc["size"] += 1
        if wrong_size == "boolean":
            root_desc["size"] = True
        if wrong_size == "negative":
            root_desc["size"] = -1
        index = encode({"schemaVersion": 2, "mediaType": oci_index,
                        "manifests": outer_children})
        archive = target / "test.oci.tar"
        with tarfile.open(archive, "w") as out:
            payloads = {"oci-layout": encode({"imageLayoutVersion": "1.0.0"}),
                        "index.json": index, **blobs}
            for name, body in payloads.items():
                info = tarfile.TarInfo(name)
                info.size = len(body)
                out.addfile(info, io.BytesIO(body))
        meta = target / "metadata.json"
        reported = (attest_manifest if metadata_kind == "attestation"
                    else sha(index) if metadata_kind == "outer"
                    else application if metadata_kind == "wrong-flat"
                    else root_desc["digest"])
        reported_desc = dict(root_desc)
        if bad_metadata_descriptor:
            reported_desc["size"] += 1
        meta.write_text(json.dumps({
            "image.name": "docker.io/allfather/release1:offline",
            "containerimage.digest": reported,
            "containerimage.descriptor": reported_desc}))
        inspected = target / "docker-inspect.json"
        inspected.write_text(json.dumps({"Id": config, "Architecture": "amd64"}))
        return archive, meta, inspected

    def test_real_small_oci_graph_binds_every_layer_and_attestation(self):
        with tempfile.TemporaryDirectory() as d:
            archive,meta,inspection=self.fixture(Path(d))
            report=oci.verify_archive(archive,meta,docker_inspect=inspection)
            self.assertTrue(report["oci_diff_ids_verified"])
            self.assertTrue(report["oci_attestation_subjects_verified"])
            self.assertEqual(report["oci_diff_id_count"],1)
            self.assertTrue(report["integrity_verified"])

    def test_rehashed_replacement_layer_still_rejected_by_rootfs(self):
        with tempfile.TemporaryDirectory() as d:
            archive,meta,inspection=self.fixture(Path(d),replace_layer=True)
            with self.assertRaisesRegex(ValueError,"rootfs.diff_ids"):
                oci.verify_archive(archive,meta,docker_inspect=inspection)

    def test_attestation_subject_cannot_name_an_unrelated_image(self):
        with tempfile.TemporaryDirectory() as d:
            archive,meta,inspection=self.fixture(Path(d),bad_subject=True)
            with self.assertRaisesRegex(ValueError,"application manifest"):
                oci.verify_archive(archive,meta,docker_inspect=inspection)

    def test_attestation_descriptor_cannot_point_to_other_image(self):
        with tempfile.TemporaryDirectory() as d:
            archive,meta,inspection=self.fixture(Path(d),bad_reference=True)
            with self.assertRaisesRegex(ValueError,"different application image"):
                oci.verify_archive(archive,meta,docker_inspect=inspection)

    def test_rootfs_diff_identity_is_mandatory(self):
        with tempfile.TemporaryDirectory() as d:
            archive,meta,inspection=self.fixture(Path(d),corrupt_rootfs=True)
            with self.assertRaisesRegex(ValueError,"rootfs.diff_ids"):
                oci.verify_archive(archive,meta,docker_inspect=inspection)


    def test_config_and_layer_subjects_cannot_authorize_image(self):
        for kind in ("config","layer","empty"):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as d:
                archive,meta,inspection=self.fixture(Path(d),subject_kind=kind)
                with self.assertRaisesRegex(ValueError,
                        "subjects|application manifest"):
                    oci.verify_archive(archive,meta,docker_inspect=inspection)

    def test_index_digest_is_a_valid_metadata_reference(self):
        with tempfile.TemporaryDirectory() as d:
            archive,meta,inspection=self.fixture(Path(d),metadata_kind="index",
                                                  manifest_subject_kind="image")
            report=oci.verify_archive(archive,meta,docker_inspect=inspection)
            self.assertEqual(report["buildkit_digest_kind"],"oci-index")
            self.assertTrue(report["oci_attestation_references_verified"])

    def test_attestation_descriptor_digest_is_not_image_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            archive,meta,inspection=self.fixture(Path(d),
                                                  metadata_kind="attestation")
            with self.assertRaisesRegex(ValueError,"BuildKit descriptor digest"):
                oci.verify_archive(archive,meta,docker_inspect=inspection)

    def test_attestation_manifest_subject_must_match_image(self):
        with tempfile.TemporaryDirectory() as d:
            archive,meta,inspection=self.fixture(Path(d),
                                                  manifest_subject_kind="wrong")
            with self.assertRaisesRegex(ValueError,"manifest subject"):
                oci.verify_archive(archive,meta,docker_inspect=inspection)

    def test_garbled_predicate_cannot_claim_sbom_or_provenance(self):
        with tempfile.TemporaryDirectory() as d:
            archive,meta,inspection=self.fixture(Path(d),invalid_predicate=True)
            with self.assertRaisesRegex(ValueError,"predicate"):
                oci.verify_archive(archive,meta,docker_inspect=inspection)



    def test_nested_exported_root_is_independently_bound(self):
        with tempfile.TemporaryDirectory() as d:
            archive, meta, inspection = self.fixture(Path(d))
            report = oci.verify_archive(archive, meta, docker_inspect=inspection)
            self.assertEqual(report["buildkit_digest_kind"], "oci-index")
            self.assertEqual(report["oci_exported_root_digest"],
                             report["buildkit_digest"])

    def test_explicit_flat_image_export_still_supported(self):
        with tempfile.TemporaryDirectory() as d:
            archive, meta, inspection = self.fixture(Path(d), nested=False)
            report = oci.verify_archive(archive, meta, docker_inspect=inspection)
            self.assertEqual(report["buildkit_digest_kind"],
                             "application-manifest")

    def test_outer_entrypoint_and_unrelated_image_are_not_buildkit_roots(self):
        for kind in ("outer", "wrong-flat", "attestation"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as d:
                archive, meta, inspection = self.fixture(Path(d),
                                                         metadata_kind=kind)
                with self.assertRaisesRegex(ValueError, "BuildKit descriptor"):
                    oci.verify_archive(archive, meta, docker_inspect=inspection)

    def test_wrong_buildkit_descriptor_size_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            archive, meta, inspection = self.fixture(
                Path(d), bad_metadata_descriptor=True)
            with self.assertRaisesRegex(ValueError, "descriptor identity"):
                oci.verify_archive(archive, meta, docker_inspect=inspection)

    def test_all_descriptors_require_exact_nonboolean_sizes(self):
        for mode in ("root", "boolean", "negative", "application",
                     "config", "layer", "statement"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                archive, meta, inspection = self.fixture(Path(d),
                                                         wrong_size=mode)
                with self.assertRaisesRegex(ValueError, r"descriptor.*size"):
                    oci.verify_archive(archive, meta, docker_inspect=inspection)

    def test_slsa_predicates_cannot_be_schema_only(self):
        for mode in ("empty", "builder-empty", "bad-build-type", "no-external"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                archive, meta, inspection = self.fixture(Path(d),
                                                         slsa_case=mode)
                with self.assertRaisesRegex(ValueError, "SLSA"):
                    oci.verify_archive(archive, meta, docker_inspect=inspection)

    def test_diagnostic_structure_is_not_an_integrity_verdict(self):
        inspect_spec=importlib.util.spec_from_file_location(
            "release1_oci_diagnostic",ROOT/"scripts/inspect-release-oci.py")
        mod=importlib.util.module_from_spec(inspect_spec)
        inspect_spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            archive,meta,_=self.fixture(Path(d))
            report=mod.inspect(archive,meta)
            self.assertFalse(report["integrity_verified"])
            self.assertEqual(len(report["index_manifests"]),1)
            self.assertEqual(report["exported_root_digest"],report["buildkit_digest"])
            self.assertEqual(report["application_manifest_digest"],report["index_manifests"][0]["children"][0]["digest"])
            attest=report["index_manifests"][0]["children"][1]
            self.assertEqual(len(attest["layers"]),2)
            self.assertEqual(attest["layers"][0]["statements"][0]["subject_count"],1)

if __name__=="__main__":
    unittest.main()
