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
    def fixture(self, target:Path, *, replace_layer=False, bad_subject=False,
                bad_reference=False, corrupt_rootfs=False):
        base=b"original uncompressed OCI application layer\n"
        expanded=(b"substituted uncompressed OCI application layer\n"
                  if replace_layer else base)
        blobs={}
        def store(data):
            oid=sha(data)
            blobs["blobs/sha256/"+oid[7:]]=data
            return oid
        tar_layer=store(gzip.compress(expanded,mtime=0))
        diff_id=sha(b"mismatched expected diff" if corrupt_rootfs else base)
        config=store(encode({"architecture":"amd64","os":"linux",
                  "rootfs":{"type":"layers","diff_ids":[diff_id]},
                  "config":{"User":"10001:10001",
                  "Entrypoint":["/opt/online3b/bin/python",
                                "/opt/release-tools/deploy/release/entrypoint.py"]}}))
        application=store(encode({
            "schemaVersion":2,
            "config":{"mediaType":"application/vnd.oci.image.config.v1+json",
                      "digest":config},
            "layers":[{"mediaType":"application/vnd.oci.image.layer.v1.tar+gzip",
                       "digest":tar_layer}],
        }))
        att_config=store(encode({"architecture":"unknown","os":"unknown"}))
        att_layers=[]
        for predicate in ("https://spdx.dev/Document","https://slsa.dev/provenance/v1"):
            subject="f"*64 if bad_subject else application[7:]
            data=encode({"_type":"https://in-toto.io/Statement/v1",
                         "predicateType":predicate,
                         "subject":[{"name":"offline-fixture",
                                     "digest":{"sha256":subject}}],
                         "predicate":{"schema_version":1}})
            oid=store(data)
            att_layers.append({"mediaType":"application/vnd.in-toto+json","digest":oid})
        att_manifest=store(encode({"schemaVersion":2,
            "config":{"mediaType":"application/vnd.oci.image.config.v1+json",
                      "digest":att_config},"layers":att_layers}))
        index=encode({"schemaVersion":2,
            "mediaType":"application/vnd.oci.image.index.v1+json",
            "manifests":[
                {"mediaType":"application/vnd.oci.image.manifest.v1+json",
                 "digest":application,"platform":{"os":"linux","architecture":"amd64"}},
                {"mediaType":"application/vnd.oci.image.manifest.v1+json",
                 "digest":att_manifest,
                 "annotations":{"vnd.docker.reference.type":"attestation-manifest",
                                "vnd.docker.reference.digest":(
                                    "sha256:"+"f"*64 if bad_reference else application)},
                 "platform":{"os":"unknown","architecture":"unknown"}}]})
        archive=target/"test.oci.tar"
        with tarfile.open(archive,"w") as out:
            payloads={"oci-layout":encode({"imageLayoutVersion":"1.0.0"}),
                      "index.json":index,**blobs}
            for name,body in payloads.items():
                info=tarfile.TarInfo(name)
                info.size=len(body)
                out.addfile(info,io.BytesIO(body))
        meta=target/"metadata.json"
        meta.write_text(json.dumps({"containerimage.digest":application}))
        inspected=target/"docker-inspect.json"
        inspected.write_text(json.dumps({"Id":config,"Architecture":"amd64"}))
        return archive,meta,inspected

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
            with self.assertRaisesRegex(ValueError,"unrelated"):
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


if __name__=="__main__":
    unittest.main()
