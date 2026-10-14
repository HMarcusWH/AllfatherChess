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
                slsa_case="valid", spdx_case="valid",
                bad_metadata_descriptor=False,
                application_platform="linux/amd64",
                index_platform="absent", inner_index_platform="absent",
                double_nested=False, embedded_media="absent",
                config_platform="valid"):
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
        cfg = {
            "architecture": "amd64", "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": [diff_id]},
            "config": {"User": "10001:10001",
                       "Entrypoint": ["/opt/online3b/bin/python",
                                      "/opt/release-tools/deploy/release/entrypoint.py"]}
        }
        config_changes = {
            "valid": {},
            "variant-v3": {"variant": "v3"},
            "valid-baseline": {"variant": "v1", "os.features": [],
                               "os.version": ""},
            "os-features-text": {"os.features": "avx512"},
            "os-features-extra": {"os.features": ["avx512"]},
            "os-version-extra": {"os.version": "2026"},
            "wrong-os": {"os": "windows"},
            "wrong-arch": {"architecture": "arm64"},
        }
        if config_platform not in config_changes:
            raise ValueError("unsupported image configuration fixture variant")
        cfg.update(config_changes[config_platform])
        config = store(encode(cfg))
        cfg_desc = descriptor("application/vnd.oci.image.config.v1+json", config)
        layer_desc = descriptor("application/vnd.oci.image.layer.v1.tar+gzip",
                                tar_layer)
        if wrong_size == "config":
            cfg_desc["size"] += 1
        if wrong_size == "layer":
            layer_desc["size"] += 1
        application_doc = {
            "schemaVersion": 2, "config": cfg_desc, "layers": [layer_desc]}
        if embedded_media in ("app-valid", "app-wrong"):
            application_doc["mediaType"] = (
                oci_manifest if embedded_media == "app-valid" else oci_index)
        application = store(encode(application_doc))
        app_desc = descriptor(oci_manifest, application)
        if application_platform == "linux/amd64":
            app_desc["platform"] = {"os": "linux", "architecture": "amd64"}
        elif application_platform == "valid-extras":
            app_desc["platform"] = {"os": "linux", "architecture": "amd64",
                                    "variant": "v1", "os.features": [],
                                    "features": [], "os.version": ""}
        elif application_platform == "variant-v3":
            app_desc["platform"] = {"os": "linux", "architecture": "amd64",
                                    "variant": "v3"}
        elif application_platform == "os-features-text":
            app_desc["platform"] = {"os": "linux", "architecture": "amd64",
                                    "os.features": "avx512"}
        elif application_platform == "os-features-extra":
            app_desc["platform"] = {"os": "linux", "architecture": "amd64",
                                    "os.features": ["avx512"]}
        elif application_platform == "features-extra":
            app_desc["platform"] = {"os": "linux", "architecture": "amd64",
                                    "features": ["unqualified"]}
        elif application_platform == "os-version-extra":
            app_desc["platform"] = {"os": "linux", "architecture": "amd64",
                                    "os.version": "2026"}
        elif application_platform == "unknown-extra":
            app_desc["platform"] = {"os": "linux", "architecture": "amd64",
                                    "arbitrary": "not qualified"}
        elif application_platform == "linux/arm64":
            app_desc["platform"] = {"os": "linux", "architecture": "arm64"}
        elif application_platform == "windows/amd64":
            app_desc["platform"] = {"os": "windows", "architecture": "amd64"}
        elif application_platform == "malformed":
            app_desc["platform"] = "linux/amd64"
        elif application_platform != "absent":
            raise ValueError("unknown synthetic application-platform variant")
        att_config = store(encode({"architecture": "unknown", "os": "unknown"}))
        attest_layers = []
        for predicate in ("https://spdx.dev/Document",
                          "https://slsa.dev/provenance/v1"):
            subject = {"image": application[7:], "config": config[7:],
                       "layer": tar_layer[7:], "empty": None}[subject_kind]
            if bad_subject:
                subject = "f" * 64
            body = ({
                "SPDXID": "SPDXRef-DOCUMENT",
                "spdxVersion": "SPDX-2.3",
                "dataLicense": "CC0-1.0",
                "name": "AllfatherChess-offline-image",
                "documentNamespace": "https://spdx.org/spdxdocs/allfather-release1-fixture",
                "creationInfo": {
                    "created": "2026-10-14T00:00:00Z",
                    "creators": ["Tool: BuildKit fixture"]},
                "packages": [{
                    "SPDXID": "SPDXRef-Package-offline",
                    "name": "offline-test-package",
                    "downloadLocation": "NOASSERTION"}],
            } if predicate == "https://spdx.dev/Document" else {
                    "buildDefinition": {
                        "buildType": "https://mobyproject.org/buildkit@v1",
                        "externalParameters": {}},
                    "runDetails": {"builder": {
                        "id": "https://github.com/moby/buildkit"}}})
            if predicate == "https://spdx.dev/Document":
                if spdx_case == "missing-license":
                    del body["dataLicense"]
                elif spdx_case == "wrong-document-id":
                    body["SPDXID"] = "SPDXRef-Other"
                elif spdx_case == "missing-name":
                    del body["name"]
                elif spdx_case == "bad-namespace":
                    body["documentNamespace"] = "invalid"
                elif spdx_case == "missing-created":
                    del body["creationInfo"]["created"]
                elif spdx_case == "invalid-created":
                    body["creationInfo"]["created"] = "2026-99-14T00:00:00Z"
                elif spdx_case == "missing-creators":
                    body["creationInfo"]["creators"] = []
                elif spdx_case == "bad-creator":
                    body["creationInfo"]["creators"] = ["anonymous"]
                elif spdx_case == "empty-inventory":
                    body["packages"] = []
                elif spdx_case == "bad-package":
                    body["packages"] = [{"SPDXID":"SPDXRef-Package-X"}]
                elif spdx_case == "missing-download":
                    del body["packages"][0]["downloadLocation"]
                elif spdx_case == "invalid-download":
                    body["packages"][0]["downloadLocation"] = "garbage"
                elif spdx_case == "bad-package-checksum":
                    body["packages"][0]["checksums"] = [
                        {"algorithm":"BOGUS","checksumValue":"0"}]
                elif spdx_case == "short-sha256":
                    body["packages"] = []
                    body["files"] = [{"SPDXID":"SPDXRef-File-A",
                                      "fileName":"./opt/example",
                                      "checksums":[{"algorithm":"SHA256",
                                                    "checksumValue":"0"}]}]
                elif spdx_case == "bogus-algorithm":
                    body["packages"] = []
                    body["files"] = [{"SPDXID":"SPDXRef-File-A",
                                      "fileName":"./opt/example",
                                      "checksums":[{"algorithm":"BOGUS",
                                                    "checksumValue":"0"}]}]
                elif spdx_case == "file-only-valid":
                    body["packages"] = []
                    body["files"] = [{"SPDXID": "SPDXRef-File-A",
                                      "fileName": "./usr/bin/allfather",
                                      "checksums": [{"algorithm": "SHA256",
                                                     "checksumValue": "a" * 64}]}]
                elif spdx_case == "missing-file-checksum":
                    body["packages"] = []
                    body["files"] = [{"SPDXID": "SPDXRef-File-A",
                                      "fileName": "./usr/bin/allfather"}]
                elif spdx_case == "bad-file-checksum":
                    body["packages"] = []
                    body["files"] = [{"SPDXID": "SPDXRef-File-A",
                                      "fileName": "./usr/bin/allfather",
                                      "checksums": [{"algorithm":"SHA256",
                                                     "checksumValue":"not-hex"}]}]
                elif spdx_case == "duplicate-id":
                    body["files"] = [{"SPDXID":"SPDXRef-Package-offline",
                                      "fileName":"./file",
                                      "checksums":[{"algorithm":"SHA256",
                                                    "checksumValue":"a"*64}]}]
                elif spdx_case == "namespace-fragment":
                    body["documentNamespace"] += "#other"
                elif spdx_case == "namespace-trailing-fragment":
                    body["documentNamespace"] += "#"
                elif spdx_case != "valid":
                    raise ValueError("unknown SPDX fixture variant")
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
            subject = descriptor(oci_manifest, application)
            if manifest_subject_kind == "wrong":
                subject["digest"] = "sha256:" + "f"*64
            elif manifest_subject_kind == "missing-size":
                del subject["size"]
            elif manifest_subject_kind == "boolean-size":
                subject["size"] = True
            elif manifest_subject_kind == "float-size":
                subject["size"] = float(subject["size"])
            elif manifest_subject_kind == "negative-size":
                subject["size"] = -1
            elif manifest_subject_kind == "incorrect-size":
                subject["size"] += 1
            elif manifest_subject_kind == "wrong-media-type":
                subject["mediaType"] = oci_index
            elif manifest_subject_kind != "image":
                raise ValueError("unknown synthetic subject variant")
            attest_data["subject"] = subject
        attest_manifest = store(encode(attest_data))
        att_desc = descriptor(oci_manifest, attest_manifest,
            annotations={"vnd.docker.reference.type": "attestation-manifest",
                         "vnd.docker.reference.digest": (
                             "sha256:" + "f"*64 if bad_reference else application)},
            platform={"os": "unknown", "architecture": "unknown"})
        if wrong_size == "application":
            app_desc["size"] += 1
        children = [app_desc, att_desc]

        def advertise_index(entry, value):
            if value == "absent":
                return
            if value == "linux/amd64":
                entry["platform"] = {"os":"linux", "architecture":"amd64"}
            elif value == "linux/arm64":
                entry["platform"] = {"os":"linux", "architecture":"arm64"}
            elif value == "windows/amd64":
                entry["platform"] = {"os":"windows", "architecture":"amd64"}
            elif value == "malformed":
                entry["platform"] = ["linux", "amd64"]
            elif value in ("valid-extras", "variant-v3", "os-features-text",
                           "os-features-extra", "features-extra",
                           "os-version-extra", "unknown-extra"):
                platform = {"os":"linux", "architecture":"amd64"}
                changes = {
                    "valid-extras": {"variant":"v1", "os.features": [],
                                     "features": [], "os.version": ""},
                    "variant-v3": {"variant":"v3"},
                    "os-features-text": {"os.features":"bad"},
                    "os-features-extra": {"os.features":["bad"]},
                    "features-extra": {"features":["bad"]},
                    "os-version-extra": {"os.version":"22"},
                    "unknown-extra": {"unknown-feature":"bad"},
                }
                entry["platform"] = {**platform, **changes[value]}
            else:
                raise ValueError("unrecognized nested-index platform fixture")

        if nested:
            nested_id = store(encode({
                "schemaVersion": 2,
                "mediaType": (oci_manifest if embedded_media == "index-wrong"
                              else oci_index),
                "manifests": children}))
            if double_nested:
                inner_desc = descriptor(oci_index, nested_id)
                advertise_index(inner_desc, inner_index_platform)
                nested_id = store(encode({
                    "schemaVersion": 2, "mediaType": oci_index,
                    "manifests": [inner_desc]}))
            root_desc = descriptor(oci_index, nested_id)
            advertise_index(root_desc, index_platform)
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

    def test_nested_application_platform_matches_config(self):
        for kind in ("linux/arm64", "windows/amd64", "malformed", "absent"):
            with self.subTest(platform=kind), tempfile.TemporaryDirectory() as d:
                archive, meta, inspection = self.fixture(
                    Path(d), application_platform=kind)
                with self.assertRaisesRegex(ValueError, "platform"):
                    oci.verify_archive(archive, meta, docker_inspect=inspection)

    def test_flat_manifest_without_platform_is_supported(self):
        with tempfile.TemporaryDirectory() as d:
            archive, meta, inspection = self.fixture(
                Path(d), nested=False, application_platform="absent")
            self.assertTrue(
                oci.verify_archive(archive, meta, docker_inspect=inspection)[
                    "oci_application_identity_verified"])

    def test_attestation_subject_descriptor_sizes_and_types(self):
        for kind in ("missing-size", "boolean-size", "float-size",
                     "negative-size", "incorrect-size", "wrong-media-type"):
            with self.subTest(subject=kind), tempfile.TemporaryDirectory() as d:
                archive, meta, inspection = self.fixture(
                    Path(d), manifest_subject_kind=kind)
                with self.assertRaisesRegex(ValueError, "manifest subject"):
                    oci.verify_archive(archive, meta, docker_inspect=inspection)

    def test_valid_optional_attestation_subject_in_nested_image(self):
        with tempfile.TemporaryDirectory() as d:
            archive, meta, inspection = self.fixture(
                Path(d), manifest_subject_kind="image")
            report = oci.verify_archive(archive, meta, docker_inspect=inspection)
            self.assertTrue(report["oci_attestation_references_verified"])

    def test_nested_document_embedded_media_type_matches_descriptor(self):
        for variant in ("app-wrong", "index-wrong"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as d:
                archive, meta, inspection = self.fixture(
                    Path(d), embedded_media=variant)
                with self.assertRaisesRegex(ValueError, "media type"):
                    oci.verify_archive(archive, meta, docker_inspect=inspection)
        with tempfile.TemporaryDirectory() as d:
            archive, meta, inspection = self.fixture(
                Path(d), embedded_media="app-valid")
            self.assertTrue(oci.verify_archive(
                archive, meta, docker_inspect=inspection)["integrity_verified"])

    def test_actual_image_config_platform_is_independently_qualified(self):
        invalid = ("variant-v3", "os-features-text", "os-features-extra",
                   "os-version-extra", "wrong-os", "wrong-arch")
        for variant in invalid:
            with self.subTest(config=variant), tempfile.TemporaryDirectory() as d:
                archive, meta, inspected = self.fixture(
                    Path(d), config_platform=variant)
                # Fixture rehashes config -> manifest -> nested index ->
                # BuildKit metadata, and inspected image ID, by construction.
                with self.assertRaisesRegex(ValueError, "OCI runnable platform"):
                    oci.verify_archive(archive, meta, docker_inspect=inspected)
        with tempfile.TemporaryDirectory() as d:
            archive, meta, inspected = self.fixture(
                Path(d), config_platform="valid-baseline")
            self.assertTrue(oci.verify_archive(
                archive, meta, docker_inspect=inspected)["integrity_verified"])

    def test_runtime_platform_optional_fields_fail_closed(self):
        invalid = ("variant-v3", "os-features-text", "os-features-extra",
                   "features-extra", "os-version-extra", "unknown-extra")
        for where in ("root", "inner", "app"):
            for variant in invalid:
                with self.subTest(where=where, kind=variant):
                    with tempfile.TemporaryDirectory() as d:
                        kwargs = ({"index_platform":variant}
                                  if where=="root" else
                                  {"inner_index_platform":variant}
                                  if where=="inner" else
                                  {"application_platform":variant})
                        archive, meta, inspection = self.fixture(
                            Path(d), double_nested=True, **kwargs)
                        with self.assertRaisesRegex(ValueError, "OCI runnable platform"):
                            oci.verify_archive(archive, meta,
                                               docker_inspect=inspection)
        with tempfile.TemporaryDirectory() as d:
            archive, meta, inspection = self.fixture(
                Path(d), double_nested=True,
                index_platform="valid-extras",
                inner_index_platform="valid-extras",
                application_platform="valid-extras")
            self.assertTrue(oci.verify_archive(
                archive, meta, docker_inspect=inspection)["integrity_verified"])

    def test_nested_index_advertised_platform_must_match_runnable_image(self):
        for advertised in ("linux/arm64", "windows/amd64", "malformed"):
            for inner in (False, True):
                with self.subTest(advertised=advertised, inner=inner):
                    with tempfile.TemporaryDirectory() as d:
                        kwargs = ({"inner_index_platform":advertised}
                                  if inner else {"index_platform":advertised})
                        archive, meta, inspection = self.fixture(
                            Path(d), double_nested=inner, **kwargs)
                        with self.assertRaisesRegex(ValueError, "OCI runnable platform"):
                            oci.verify_archive(archive, meta,
                                               docker_inspect=inspection)

    def test_valid_advertised_platform_at_every_nested_index_level(self):
        with tempfile.TemporaryDirectory() as d:
            archive, meta, inspection = self.fixture(
                Path(d), double_nested=True,
                index_platform="linux/amd64",
                inner_index_platform="linux/amd64")
            self.assertTrue(
                oci.verify_archive(archive, meta,
                                   docker_inspect=inspection)["integrity_verified"])

    def test_supported_spdx_download_locations(self):
        accepted = ("NONE", "NOASSERTION",
                    "https://example.test/archive.tar.gz",
                    "ftp://example.test/private.tar.gz",
                    "git+https://example.test/repo.git@5d2fa",
                    "git+ssh://git@example.test/repo.git@5d2fa")
        refused = ("", "garbage", "noassertion", "none", "example.test/repo",
                   "https:///missing-host", "https://example.test/a b",
                   "https://user:password@example.test/x",
                   "http://user@example.test/x", "javascript:evil()",
                   "https://[bad-ipv6/x", "https://example.test:invalid/path",
                   "NOASSERTION\n")
        for location in accepted:
            with self.subTest(location=location):
                self.assertTrue(oci.valid_spdx_download_location(location))
        for location in refused:
            with self.subTest(location=location):
                self.assertFalse(oci.valid_spdx_download_location(location))

    def test_spdx_algorithm_lengths_and_digest_canonicality(self):
        for algorithm, width in oci.SPDX_CHECKSUM_HEX_LENGTH.items():
            with self.subTest(algorithm=algorithm):
                self.assertTrue(oci.valid_spdx_checksums([
                    {"algorithm": algorithm, "checksumValue": "a" * width}]))
                for bad in ("a"*(width-1), "A"*width, "g"*width, "a"*(width+1)):
                    self.assertFalse(oci.valid_spdx_checksums([
                        {"algorithm": algorithm, "checksumValue": bad}]))
        self.assertFalse(oci.valid_spdx_checksums(
            [{"algorithm":"BOGUS","checksumValue":"0"}]))
        self.assertFalse(oci.valid_spdx_checksums(
            [{"algorithm":"MD6","checksumValue":"a"*64}]))
        self.assertFalse(oci.valid_spdx_checksums(
            [{"algorithm":"SHA256","checksumValue":"a"*64}]*2))
        self.assertFalse(oci.valid_spdx_checksums([]))

    def test_spdx_file_only_inventory_uses_fileName_not_package_name(self):
        with tempfile.TemporaryDirectory() as d:
            archive, meta, inspect = self.fixture(Path(d), spdx_case="file-only-valid")
            self.assertTrue(oci.verify_archive(
                archive, meta, docker_inspect=inspect)["oci_sbom_verified"])

    def test_sanitized_diagnostics_report_spdx_shape_without_file_names(self):
        mod_spec = importlib.util.spec_from_file_location(
            "release1_inventory_diagnostic",
            ROOT/"scripts/inspect-release-oci.py")
        mod = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            archive, meta, _ = self.fixture(Path(d), spdx_case="file-only-valid")
            summary = mod.inspect(archive, meta)
            attest = summary["index_manifests"][0]["children"][1]
            spdx = next(s for l in attest["layers"] for s in l["statements"]
                        if s["predicate_type"] == "https://spdx.dev/Document")
            self.assertEqual(spdx["spdx_package_count"], 0)
            self.assertEqual(spdx["spdx_file_count"], 1)
            self.assertIn("fileName", spdx["spdx_file_field_keys"])
            self.assertNotIn("./usr/bin/allfather", json.dumps(summary))
            self.assertFalse(summary["integrity_verified"])

    def test_incomplete_spdx_sbom_never_qualifies(self):
        cases = ("missing-license", "wrong-document-id", "missing-name",
                 "bad-namespace", "missing-created", "invalid-created",
                 "missing-creators", "bad-creator", "empty-inventory",
                 "bad-package", "missing-download", "invalid-download",
                 "bad-package-checksum", "short-sha256", "bogus-algorithm",
                 "missing-file-checksum", "bad-file-checksum", "duplicate-id", "namespace-fragment",
                 "namespace-trailing-fragment")
        for variant in cases:
            with self.subTest(spdx=variant), tempfile.TemporaryDirectory() as d:
                archive, meta, inspection = self.fixture(
                    Path(d), spdx_case=variant)
                with self.assertRaisesRegex(ValueError, "SPDX"):
                    oci.verify_archive(archive, meta, docker_inspect=inspection)

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
            with self.assertRaisesRegex(ValueError,"SPDX|SLSA|predicate"):
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

    def test_metadata_descriptor_size_must_be_integer_even_if_numerically_equal(self):
        with tempfile.TemporaryDirectory() as d:
            archive, meta, inspection = self.fixture(Path(d))
            report = json.loads(meta.read_text())
            report["containerimage.descriptor"]["size"] = float(
                report["containerimage.descriptor"]["size"])
            meta.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "descriptor identity"):
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

    def test_retained_oci_diagnostics_do_not_echo_untrusted_subject_names(self):
        mod_spec=importlib.util.spec_from_file_location(
            "release1_safe_diag",ROOT/"scripts/inspect-release-oci.py")
        mod=importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            archive, meta, _ = self.fixture(Path(d))
            report=mod.inspect(archive,meta)
            payload=json.dumps(report)
            self.assertNotIn("offline-fixture",payload)
            self.assertNotIn("SPDXRef-Package-offline",payload)
            self.assertNotIn("offline-test-package",payload)
            self.assertFalse(report["integrity_verified"])

    def test_diagnostic_reports_optional_subject_declared_and_actual_sizes(self):
        inspect_spec = importlib.util.spec_from_file_location(
            "release1_oci_diag_subject", ROOT/"scripts/inspect-release-oci.py")
        mod = importlib.util.module_from_spec(inspect_spec)
        inspect_spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            archive, metadata, _ = self.fixture(
                Path(d), manifest_subject_kind="image")
            report = mod.inspect(archive, metadata)
            self.assertFalse(report["integrity_verified"])
            att = report["index_manifests"][0]["children"][1]
            self.assertEqual(att["subject_declared_size"],
                             att["subject_actual_size"])
            self.assertGreater(att["subject_actual_size"],0)

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
            self.assertIsNone(attest["manifest_subject"])
            self.assertEqual(len(attest["layers"]),2)
            self.assertEqual(attest["layers"][0]["statements"][0]["subject_count"],1)

if __name__=="__main__":
    unittest.main()
