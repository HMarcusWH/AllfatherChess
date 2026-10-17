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
                config_platform="valid", diagnostic_canary=None,
                malformed_diagnostic=None, statement_wrapper="singleton"):
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
        cfg_desc = descriptor(
            diagnostic_canary if diagnostic_canary else
            "application/vnd.oci.image.config.v1+json", config)
        layer_desc = descriptor("application/vnd.oci.image.layer.v1.tar+gzip",
                                tar_layer)
        if wrong_size == "config":
            cfg_desc["size"] += 1
        if wrong_size == "layer":
            layer_desc["size"] += 1
        application_doc = {
            "schemaVersion": 2, "config": cfg_desc, "layers": [layer_desc]}
        if malformed_diagnostic == "app-config-list":
            application_doc["config"] = [cfg_desc]
        elif malformed_diagnostic == "app-layers-null":
            application_doc["layers"] = None
        elif malformed_diagnostic == "app-layer-scalar":
            application_doc["layers"] = [7]
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
                    "downloadLocation": "NOASSERTION",
                    "filesAnalyzed": False,
                    "copyrightText": "NOASSERTION",
                    "licenseConcluded": "NOASSERTION",
                    "licenseDeclared": "NOASSERTION"}],
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
                elif spdx_case == "missing-copyright":
                    del body["packages"][0]["copyrightText"]
                elif spdx_case == "empty-copyright":
                    body["packages"][0]["copyrightText"] = ""
                elif spdx_case == "filesAnalyzed-invalid":
                    body["packages"][0]["filesAnalyzed"] = "false"
                elif spdx_case == "missing-filesAnalyzed-without-code":
                    del body["packages"][0]["filesAnalyzed"]
                elif spdx_case == "filesAnalyzed-without-code":
                    body["packages"][0]["filesAnalyzed"] = True
                elif spdx_case == "unanalysed-with-code":
                    body["packages"][0]["packageVerificationCode"] = {
                        "packageVerificationCodeValue": "a"*40}
                elif spdx_case == "unanalysed-with-file-licenses":
                    body["packages"][0]["licenseInfoFromFiles"] = ["MIT"]
                elif spdx_case == "invalid-license":
                    body["packages"][0]["licenseDeclared"] = 3
                elif spdx_case == "missing-download":
                    del body["packages"][0]["downloadLocation"]
                elif spdx_case == "invalid-download":
                    body["packages"][0]["downloadLocation"] = "garbage"
                elif spdx_case == "download-unicode":
                    body["packages"][0]["downloadLocation"] = "https://example.test/☃"
                elif spdx_case == "download-braces":
                    body["packages"][0]["downloadLocation"] = "https://example.test/{bad}"
                elif spdx_case == "download-pipe":
                    body["packages"][0]["downloadLocation"] = "https://example.test/a|b"
                elif spdx_case == "invalid-percent":
                    body["packages"][0]["downloadLocation"] = "https://example.test/%zz"
                elif spdx_case == "invalid-percent-host":
                    body["packages"][0]["downloadLocation"] = "https://%zz/path"
                elif spdx_case == "invalid-port":
                    body["packages"][0]["downloadLocation"] = "https://example.test:bad/p"
                elif spdx_case == "invalid-authority":
                    body["packages"][0]["downloadLocation"] = "https://bad..host/path"
                elif spdx_case == "encoded-control":
                    body["packages"][0]["downloadLocation"] = "https://example.test/%00"
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
                elif spdx_case == "namespace-bracket-path":
                    body["documentNamespace"] = "https://example.test/a[b]"
                elif spdx_case == "namespace-bracket-query":
                    body["documentNamespace"] = "https://example.test/?q=[x]"
                elif spdx_case == "download-bracket-path":
                    body["packages"][0]["downloadLocation"] = "https://example.test/a[b]"
                elif spdx_case == "namespace-unicode":
                    body["documentNamespace"] = "https://example.test/snowman-☃"
                elif spdx_case == "namespace-braces":
                    body["documentNamespace"] = "https://example.test/{bad}"
                elif spdx_case == "namespace-pipe":
                    body["documentNamespace"] = "https://example.test/a|b"
                elif spdx_case == "namespace-percent":
                    body["documentNamespace"] = "https://%zz/path"
                elif spdx_case == "namespace-port":
                    body["documentNamespace"] = "https://example.test:bogus/x"
                elif spdx_case == "namespace-control":
                    body["documentNamespace"] = "https://example.test/path\x00"
                elif spdx_case == "namespace-userinfo":
                    body["documentNamespace"] = "https://user:pw@example.test/a"
                elif spdx_case == "namespace-encoded-path":
                    body["documentNamespace"] = "https://example.test/a%zz"
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
                if slsa_case == "resolved-as-string":
                    body["buildDefinition"]["resolvedDependencies"] = "not-an-array"
                if slsa_case == "metadata-as-string":
                    body["runDetails"]["metadata"] = "not-an-object"
                if slsa_case == "byproducts-as-map":
                    body["runDetails"]["byproducts"] = {}
                if slsa_case == "version-as-string":
                    body["runDetails"]["builder"]["version"] = "not-an-object"
                if slsa_case == "resource-item-nonobject":
                    body["buildDefinition"]["resolvedDependencies"] = [17]
                if slsa_case == "resource-empty":
                    body["buildDefinition"]["resolvedDependencies"] = [{}]
                if slsa_case == "resource-name-only":
                    body["runDetails"]["byproducts"] = [{"name":"not-identity"}]
                if slsa_case == "resource-bad-digest":
                    body["buildDefinition"]["resolvedDependencies"] = [
                        {"digest":{"sha256":"not-hex"}}]
                if slsa_case == "resource-empty-digest":
                    body["buildDefinition"]["resolvedDependencies"] = [
                        {"digest":{}}]
                if slsa_case == "resource-bad-content":
                    body["buildDefinition"]["resolvedDependencies"] = [
                        {"content":"not+base64!"}]
                if slsa_case == "metadata-bad-start":
                    body["runDetails"]["metadata"] = {"startedOn":"not-a-time"}
                if slsa_case == "metadata-bad-finish":
                    body["runDetails"]["metadata"] = {"finishedOn":"garbage"}
                if slsa_case == "metadata-reversed":
                    body["runDetails"]["metadata"] = {
                        "startedOn":"2026-10-18T00:00:00Z",
                        "finishedOn":"2026-10-17T00:00:00Z"}
                if slsa_case == "valid-optionals":
                    body["buildDefinition"]["resolvedDependencies"] = [
                        {"uri":"https://example.test/source.tar.gz",
                         "digest":{"sha256":"a"*64}}]
                    body["buildDefinition"]["internalParameters"] = {}
                    body["runDetails"]["builder"]["version"] = {
                        "buildkit":"v0.20"}
                    body["runDetails"]["metadata"] = {
                        "invocationId":"offline-test"}
                    body["runDetails"]["byproducts"] = []
            if invalid_predicate:
                body = {"schema_version": 1}
            statement_document = {
                "_type": diagnostic_canary or "https://in-toto.io/Statement/v1",
                "predicateType": diagnostic_canary or predicate,
                "subject": ([] if subject is None else
                            [{"name": diagnostic_canary or "offline-fixture",
                              "digest": {"sha256": subject}}]),
                "predicate": body}
            if predicate == "https://spdx.dev/Document":
                if statement_wrapper == "array-bad-subject":
                    statement_document = {"statements":[
                        {**statement_document,"subject":[None]},
                        statement_document]}
                elif statement_wrapper == "array-empty-predicate":
                    statement_document = {"statements":[
                        {**statement_document,"predicate":{}},
                        statement_document]}
                elif statement_wrapper == "array-unknown":
                    statement_document = {"statements":[
                        {**statement_document,"predicateType":
                            "https://unknown.test/forged-predicate"},
                        statement_document]}
                elif statement_wrapper == "array":
                    statement_document = {"statements": [statement_document]}
                elif statement_wrapper == "array-null":
                    statement_document = {"statements":[None,statement_document]}
                elif statement_wrapper in ("null", "object", "empty", "string"):
                    statement_document["statements"] = {
                        "null": None, "object": {}, "empty": [],
                        "string": "malformed",
                    }[statement_wrapper]
                elif statement_wrapper != "singleton":
                    raise ValueError("unsupported statement-wrapper fixture")
            lid = store(encode(statement_document))
            layer = descriptor("application/vnd.in-toto+json", lid)
            if wrong_size == "statement":
                layer["size"] += 1
            attest_layers.append(layer)
        attest_data = {
            "schemaVersion": 2,
            "config": descriptor("application/vnd.oci.image.config.v1+json",
                                  att_config),
            "layers": attest_layers}
        if malformed_diagnostic == "attest-config-list":
            attest_data["config"] = [attest_data["config"]]
        elif malformed_diagnostic == "attest-layer-null":
            attest_data["layers"] = [None]
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
        annotations={"vnd.docker.reference.type": "attestation-manifest",
                     "vnd.docker.reference.digest": (
                         "sha256:" + "f"*64 if bad_reference else application)}
        if diagnostic_canary:
            annotations["secret.debug"] = diagnostic_canary
        att_desc = descriptor(oci_manifest, attest_manifest,
            annotations=annotations,
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
            "image.name": diagnostic_canary or "docker.io/allfather/release1:offline",
            "containerimage.digest": diagnostic_canary or reported,
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
                    "https://example.test/archive%20name.tar.gz",
                    "ftp://example.test/private.tar.gz",
                    "git+https://example.test/repo.git@5d2fa",
                    "git+ssh://git@example.test/repo.git@5d2fa")
        refused = ("", "garbage", "noassertion", "none", "example.test/repo",
                   "https:///missing-host", "https://example.test/a b",
                   "https://user:password@example.test/x",
                   "http://user@example.test/x", "javascript:evil()",
                   "https://[bad-ipv6/x", "https://example.test:invalid/path",
                   "https://%zz/path", "https://example.test/a%2",
                   "https://example.test/%00", "https://bad..host/p",
                   "https://user@@example.test/p", "NOASSERTION\n")
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

    def test_uri_grammar_is_shared_without_collapsing_identity_policies(self):
        for uri in ("https://example.test/build@v1", "urn:allfather:build:v1"):
            self.assertTrue(oci.valid_identity_uri(uri))
        self.assertTrue(oci.valid_spdx_namespace(
            "https://example.test/spdx%20namespace"))
        self.assertTrue(oci.valid_spdx_namespace(
            "https://example.test/%E2%98%83"))
        self.assertTrue(oci.valid_identity_uri(
            "https://example.test/build?key=value&v=2"))
        self.assertTrue(oci.valid_identity_uri(
            "https://[2001:db8::1]/x?q=%5By%5D"))
        for uri in ("https://%zz/path", "https://example.test:bogus/x",
                    "https://user:secret@example.test/x",
                    "https://example.test/a\x00", "https://example.test/a%2",
                    "https://example.test/☃", "https://example.test/{bad}",
                    "https://example.test/a|b", "https://example.test/^x",
                    "https://example.test/a[b]",
                    "https://example.test/?q=[x]", "urn:example:a[b]",
                    "https://example.test/foo#"):
            with self.subTest(uri=uri):
                self.assertFalse(oci.valid_spdx_namespace(uri))
        self.assertTrue(oci.valid_spdx_download_location("NOASSERTION"))
        self.assertFalse(oci.valid_spdx_namespace("NOASSERTION"))

    def test_spdx_package_optional_defaults_and_conditional_verification(self):
        pkg={"SPDXID":"SPDXRef-Package-complete",
             "name":"sample", "downloadLocation":"NOASSERTION",
             "copyrightText":"NOASSERTION", "filesAnalyzed":False}
        self.assertTrue(oci.valid_spdx_download_location(pkg["downloadLocation"]))
        # Optional field omission means files were analyzed; require the
        # SHA1 verification code in that case, rather than guessing False.
        with tempfile.TemporaryDirectory() as d:
            archive,meta,inspection=self.fixture(Path(d),
                                      spdx_case="missing-filesAnalyzed-without-code")
            with self.assertRaisesRegex(ValueError,"verification code"):
                oci.verify_archive(archive,meta,docker_inspect=inspection)

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
                 "missing-copyright", "empty-copyright",
                 "filesAnalyzed-invalid","missing-filesAnalyzed-without-code",
                 "filesAnalyzed-without-code", "unanalysed-with-code",
                 "unanalysed-with-file-licenses","invalid-license",
                 "download-unicode", "download-braces", "download-pipe",
                 "invalid-percent", "invalid-percent-host", "invalid-port",
                 "invalid-authority", "encoded-control", "bad-package-checksum", "short-sha256", "bogus-algorithm",
                 "missing-file-checksum", "bad-file-checksum", "duplicate-id", "namespace-fragment",
                 "namespace-trailing-fragment", "namespace-percent",
                 "namespace-port", "namespace-control", "namespace-userinfo",
                 "namespace-encoded-path", "namespace-unicode", "namespace-braces",
                 "namespace-pipe", "namespace-bracket-path",
                 "namespace-bracket-query", "download-bracket-path")
        for variant in cases:
            with self.subTest(spdx=variant), tempfile.TemporaryDirectory() as d:
                archive, meta, inspection = self.fixture(
                    Path(d), spdx_case=variant)
                with self.assertRaisesRegex(ValueError, "SPDX"):
                    oci.verify_archive(archive, meta, docker_inspect=inspection)

    def test_in_toto_wrapper_requires_absence_or_nonempty_array(self):
        diagnostic_spec=importlib.util.spec_from_file_location(
            "release1_statement_wrapper_diagnostic",
            ROOT/"scripts/inspect-release-oci.py")
        mod=importlib.util.module_from_spec(diagnostic_spec)
        diagnostic_spec.loader.exec_module(mod)
        for kind in ("singleton", "array"):
            with self.subTest(valid=kind),tempfile.TemporaryDirectory() as d:
                archive,meta,inspection=self.fixture(
                    Path(d),statement_wrapper=kind)
                self.assertTrue(oci.verify_archive(
                    archive,meta,docker_inspect=inspection)["integrity_verified"])
                summary=mod.inspect(archive,meta)
                self.assertFalse(summary["integrity_verified"])
                self.assertEqual(
                    len(summary["index_manifests"][0]["children"][1]
                        ["layers"][0]["statements"]),1)
        for kind in ("null", "object", "empty", "string"):
            with self.subTest(invalid=kind),tempfile.TemporaryDirectory() as d:
                archive,meta,inspection=self.fixture(
                    Path(d),statement_wrapper=kind)
                with self.assertRaisesRegex(ValueError,
                        "in-toto statements wrapper"):
                    oci.verify_archive(archive,meta,docker_inspect=inspection)
                with self.assertRaisesRegex(ValueError,
                        "invalid OCI diagnostic statement list"):
                    mod.inspect(archive,meta)

    def test_real_small_oci_graph_binds_every_layer_and_attestation(self):
        with tempfile.TemporaryDirectory() as d:
            archive,meta,inspection=self.fixture(Path(d))
            report=oci.verify_archive(archive,meta,docker_inspect=inspection)
            self.assertTrue(report["oci_diff_ids_verified"])
            self.assertTrue(report["oci_attestation_subjects_verified"])
            self.assertEqual(report["oci_diff_id_count"],1)
            self.assertTrue(report["integrity_verified"])

    def test_replacing_oci_path_during_verification_cannot_swap_digest(self):
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            first=folder/"first"
            second=folder/"second"
            first.mkdir()
            second.mkdir()
            archive,meta,inspection=self.fixture(first)
            replacement,_,_=self.fixture(second,spdx_case="file-only-valid")
            orig=oci.verify_attestation_subjects
            def swap(*args):
                os.replace(replacement,archive)
                return orig(*args)
            with mock.patch.object(oci,"verify_attestation_subjects",
                                   side_effect=swap):
                with self.assertRaisesRegex(ValueError,
                                             "archive file was mutated or replaced"):
                    oci.verify_archive(archive,meta,docker_inspect=inspection)

    def test_oci_mutated_in_place_during_verification_is_refused(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            archive,meta,inspection=self.fixture(Path(d))
            orig=oci.verify_attestation_subjects
            def mutate(*args):
                with archive.open("ab") as writable:
                    writable.write(b"tampered trailing bytes")
                return orig(*args)
            with mock.patch.object(oci,"verify_attestation_subjects",
                                   side_effect=mutate):
                with self.assertRaisesRegex(ValueError,
                                             "archive file was mutated or replaced"):
                    oci.verify_archive(archive,meta,docker_inspect=inspection)

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

    def test_resource_descriptors_require_meaningful_identity(self):
        good=(
            {"uri":"https://example.test/file"},
            {"uri":"pkg:docker/ubuntu@sha256%3Aabc123"},
            {"digest":{"sha256":"a"*64}},
            {"content":"e30="},
            {"uri":"git+https://example.test/code.git@abc123",
             "digest":{"sha1":"b"*40}},
        )
        bad=(
            {},{"name":"name-is-not-an-identity"},{"uri":""},
            {"digest":{}},{"digest":{"sha256":"garbage"}},
            {"content":"not+base64!"},{"uri":"https://example.test/x",
                                    "digest":{}},
            {"uri":"https://example.test/x","content":"*"},
        )
        for row in good:
            with self.subTest(valid=row):
                self.assertTrue(oci._valid_resource_descriptor(row))
        for row in bad:
            with self.subTest(invalid=row):
                self.assertFalse(oci._valid_resource_descriptor(row))

    def test_metadata_timestamps_require_rfc3339_and_monotonic_order(self):
        good={"startedOn":"2026-10-17T08:00:00Z",
              "finishedOn":"2026-10-17T08:00:01+00:00"}
        self.assertTrue(oci._valid_slsa_metadata(good,version="v1"))
        self.assertTrue(oci._valid_slsa_metadata(
            {"startedOn":None,"finishedOn":""},version="v1"))
        for bad in (
            {"startedOn":"not-a-time"},
            {"finishedOn":"2026-15-17T18:00:00Z"},
            {"startedOn":"2026-10-17T19:00:00Z",
             "finishedOn":"2026-10-17T18:00:00Z"},
            {"startedOn":"2026-10-17T08:00:00"},
        ):
            with self.subTest(bad=bad):
                self.assertFalse(oci._valid_slsa_metadata(bad,version="v1"))

    def test_ipvfuture_authority_is_valid_but_brackets_in_paths_are_not(self):
        self.assertTrue(oci.valid_identity_uri(
            "https://[v1.a:b]/resource"))
        self.assertTrue(oci.valid_identity_uri(
            "https://[2001:db8::1]/resource"))
        self.assertFalse(oci.valid_identity_uri(
            "https://example.test/a[b]"))
        self.assertFalse(oci.valid_identity_uri(
            "https://[v1.bad!]/a[b]"))

    def test_slsa_optional_fields_have_independent_schema_contract(self):
        with tempfile.TemporaryDirectory() as d:
            archive,meta,inspected=self.fixture(
                Path(d),slsa_case="valid-optionals")
            self.assertTrue(oci.verify_archive(
                archive,meta,docker_inspect=inspected)["oci_provenance_verified"])
        v02={"builder":{"id":"https://builder.example.test/v02"},
             "buildType":"https://example.test/buildtype",
             "invocation":{}}
        oci.validate_slsa("https://slsa.dev/provenance/v0.2",v02)
        for bad in (
            {**v02,"materials":"not-an-array"},
            {**v02,"metadata":"not-an-object"},
            {**v02,"builder":{**v02["builder"],"version":"bad"}},
            {**v02,"invocation":{"environment":"not-an-object"}},
            {**v02,"buildConfig":[]},
        ):
            with self.subTest(bad=bad),self.assertRaisesRegex(ValueError,
                                                              "SLSA"):
                oci.validate_slsa("https://slsa.dev/provenance/v0.2",bad)

    def test_slsa_predicates_cannot_be_schema_only(self):
        for mode in ("empty", "builder-empty", "bad-build-type", "no-external",
                     "resolved-as-string", "metadata-as-string",
                     "byproducts-as-map", "version-as-string",
                     "resource-item-nonobject", "resource-empty",
                     "resource-name-only","resource-bad-digest",
                     "resource-empty-digest","resource-bad-content",
                     "metadata-bad-start","metadata-bad-finish",
                     "metadata-reversed"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as d:
                archive, meta, inspection = self.fixture(Path(d),
                                                         slsa_case=mode)
                with self.assertRaisesRegex(ValueError, "SLSA"):
                    oci.verify_archive(archive, meta, docker_inspect=inspection)

    def test_all_diagnostic_statement_values_are_allowlisted(self):
        mod_spec=importlib.util.spec_from_file_location(
            "release1_diag_canary",ROOT/"scripts/inspect-release-oci.py")
        mod=importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(mod)
        marker="CANARY-private-access-token-DO-NOT-RETAIN"
        with tempfile.TemporaryDirectory() as d:
            archive,meta,_=self.fixture(Path(d),diagnostic_canary=marker)
            report=mod.inspect(archive,meta)
            serialized=json.dumps(report)
            self.assertNotIn(marker,serialized)
            self.assertFalse(report["integrity_verified"])
            self.assertIsNone(report["buildkit_digest"])
            attest=report["index_manifests"][0]["children"][1]
            for layer in attest["layers"]:
                for statement in layer["statements"]:
                    self.assertEqual(statement["_type"],"unrecognized")
                    self.assertEqual(statement["predicate_type"],"unrecognized")
            self.assertEqual(
                report["index_manifests"][0]["children"][0]["config_type"],
                "unrecognized")

    def test_malformed_oci_containers_yield_sanitized_error_and_strict_refusal(self):
        mod_spec=importlib.util.spec_from_file_location(
            "release1_bad_oci_diagnostic",ROOT/"scripts/inspect-release-oci.py")
        mod=importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(mod)
        import sys
        for mode in ("app-config-list", "app-layers-null",
                     "app-layer-scalar", "attest-config-list",
                     "attest-layer-null"):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as d:
                archive,meta,inspection=self.fixture(
                    Path(d),malformed_diagnostic=mode)
                output=Path(d)/"diagnostic.json"
                before=sys.argv
                try:
                    sys.argv=["diag","--archive",str(archive),
                              "--metadata",str(meta),"--output",str(output)]
                    self.assertEqual(mod.main(),0)
                finally:
                    sys.argv=before
                report=json.loads(output.read_text())
                self.assertFalse(report["integrity_verified"])
                self.assertTrue(report["inspection_failed"])
                self.assertEqual(report["inspection_error"],"ValueError")
                with self.assertRaises((ValueError, TypeError)):
                    oci.verify_archive(archive,meta,docker_inspect=inspection)

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

    def test_diagnostic_accepts_valid_single_statement_envelope(self):
        spec=importlib.util.spec_from_file_location(
            "release1_single_statement_diag",ROOT/"scripts/inspect-release-oci.py")
        mod=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as d:
            archive,meta,_=self.fixture(Path(d))
            report=mod.inspect(archive,meta)
            attest=report["index_manifests"][0]["children"][1]
            self.assertEqual(len(attest["layers"]),2)
            self.assertEqual(
                sum(len(layer["statements"]) for layer in attest["layers"]),2)
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
