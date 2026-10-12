#!/usr/bin/env python3
"""Independently reconstruct OCI image layout and signed-content *claims*.

Checks manifest/config/layers, archive integrity and SBOM/provenance presence;
it does NOT claim a trusted publisher signature or legal distribution.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath

HEX = re.compile(r"^[0-9a-f]{64}$")


def require(ok, why):
    if not ok:
        raise ValueError(why)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path):
    sha = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1 << 20), b""):
            sha.update(part)
    return sha.hexdigest()


def verify_image_config_link(*, image_config_digest: str,
                             metadata_config_digest: str | None,
                             docker_inspection: dict | None) -> bool:
    """OCI index exporters can omit containerimage.config.digest metadata.

    In that case require the independently inspected *loaded* image to
    supply the exact config digest. Never compare OCI index digest to a
    config digest, and never infer equality from a green build label.
    """
    require(isinstance(image_config_digest, str) and
            image_config_digest.startswith("sha256:") and
            HEX.fullmatch(image_config_digest[7:]) is not None,
            "invalid archive image-config digest")
    if metadata_config_digest is not None:
        require(metadata_config_digest == image_config_digest,
                "BuildKit config metadata contradicts OCI archive image")
    if docker_inspection is not None:
        require(docker_inspection.get("Id") == image_config_digest and
                docker_inspection.get("Architecture") == "amd64",
                "tested Docker image differs from exported OCI config")
    require(metadata_config_digest is not None or docker_inspection is not None,
            "OCI config identity has neither BuildKit metadata nor image inspection")
    return True


def verify_archive(archive: Path, metadata: Path, *, docker_inspect: Path | None = None):
    meta = json.loads(metadata.read_text(encoding="utf-8"))
    blobs = {}
    with tarfile.open(archive, "r:*") as inp:
        members = inp.getmembers()
        by_name = {}
        for obj in members:
            name = str(PurePosixPath(obj.name))
            require(not obj.issym() and not obj.islnk() and
                    not PurePosixPath(name).is_absolute() and
                    ".." not in PurePosixPath(name).parts and
                    name not in by_name,
                    "OCI archive includes unsafe or duplicate path")
            by_name[name] = obj
        require("oci-layout" in by_name and "index.json" in by_name,
                "not an OCI-layout archive")
        require(json.load(inp.extractfile(by_name["oci-layout"])).get("imageLayoutVersion") ==
                "1.0.0", "unsupported OCI layout")
        index_member = inp.extractfile(by_name["index.json"])
        require(index_member is not None, "OCI index not readable")
        index_raw = index_member.read()
        index_digest = "sha256:" + _digest(index_raw)
        index = json.loads(index_raw)
        require(index.get("schemaVersion") == 2 and
                index.get("mediaType") in (None,"application/vnd.oci.image.index.v1+json"),
                "invalid OCI index")
        referenced = set()
        def get(oid):
            if not isinstance(oid, str) or not oid.startswith("sha256:") or not HEX.fullmatch(oid[7:]):
                raise ValueError("invalid OCI digest")
            name = "blobs/sha256/" + oid[7:]
            if name in blobs:
                return blobs[name]
            require(name in by_name and by_name[name].isfile(), "missing OCI blob "+oid)
            f = inp.extractfile(by_name[name])
            require(f is not None, "unreadable OCI blob")
            sha = hashlib.sha256()
            body = bytearray() if by_name[name].size <= 8 * 1024 * 1024 else None
            while True:
                data = f.read(1 << 20)
                if not data: break
                sha.update(data)
                if body is not None: body.extend(data)
            require(sha.hexdigest() == oid[7:], "OCI blob hash mismatch "+oid)
            referenced.add(name)
            if body is not None:
                blobs[name] = bytes(body)
                return blobs[name]
            return None

        images, attestations = [], []
        def inspect_descriptor(descriptor, depth=0):
            require(depth < 4 and isinstance(descriptor, dict),
                    "OCI descriptor nesting invalid")
            raw = get(descriptor.get("digest"))
            require(raw is not None, "OCI descriptor JSON too large or missing")
            document = json.loads(raw)
            typ = descriptor.get("mediaType")
            if typ == "application/vnd.oci.image.index.v1+json":
                for next in document.get("manifests", []):
                    inspect_descriptor(next, depth+1)
                return
            require(typ in ("application/vnd.oci.image.manifest.v1+json",
                            "application/vnd.docker.distribution.manifest.v2+json"),
                    "unsupported OCI descriptor kind")
            cfg = document.get("config") or {}
            config_bytes = get(cfg.get("digest"))
            require(config_bytes is not None, "OCI config cannot be independently parsed")
            details = json.loads(config_bytes)
            layers = document.get("layers")
            require(isinstance(layers, list) and bool(layers), "OCI image missing layers")
            for layer in layers:
                get(layer.get("digest"))
            if descriptor.get("annotations", {}).get("vnd.docker.reference.type") == "attestation-manifest":
                for layer in layers:
                    content = get(layer["digest"])
                    require(content is not None, "oversized attestation refused")
                    attestations.append(json.loads(content))
            elif details.get("architecture") == "amd64" and details.get("os") == "linux":
                images.append({"manifest_digest":descriptor["digest"],
                               "config_digest":cfg["digest"],
                               "config":details,
                               "layer_count":len(layers)})
        for x in index.get("manifests",[]):
            inspect_descriptor(x)
        require(len(images)==1, "require exactly one linux/amd64 application image")
        image=images[0]
        reported_manifest=meta.get("containerimage.digest")
        reported_config=meta.get("containerimage.config.digest")
        # BuildKit OCI index metadata can omit this config digest when
        # SBOM/provenance attestation manifests are attached. In that case
        # use the independent loaded-image inspection as the mandatory link.
        inspection = None
        if docker_inspect:
            inspection = json.loads(docker_inspect.read_text(encoding="utf-8"))
            if isinstance(inspection,list):
                require(len(inspection) == 1, "ambiguous Docker inspection")
                inspection = inspection[0]
        verify_image_config_link(
            image_config_digest=image["config_digest"],
            metadata_config_digest=reported_config,
            docker_inspection=inspection,
        )
        # BuildKit reports a descriptor digest, not the sha256 of OCI tar bytes.
        accepted_digests = {image["manifest_digest"], index_digest}
        accepted_digests.update(x.get("digest") for x in index.get("manifests",[]))
        require(reported_manifest in accepted_digests,
                "BuildKit descriptor digest does not match OCI index or image")
        config=image["config"].get("config") or {}
        require(config.get("User") in ("10001:10001", "10001") and
                config.get("Entrypoint")==[
                    "/opt/online3b/bin/python",
                    "/opt/release-tools/deploy/release/entrypoint.py"],
                "OCI image launched with unapproved identity/entrypoint")
        require(not config.get("ExposedPorts"), "offline candidate must not expose ports")
        predicates=set()
        for att in attestations:
            if isinstance(att,dict):
                if att.get("predicateType"):
                    predicates.add(att["predicateType"])
                for row in att.get("statements",[]):
                    if isinstance(row,dict) and row.get("predicateType"):
                        predicates.add(row["predicateType"])
        require(any("spdx" in x.lower() for x in predicates),
                "required image SBOM attestation not exported")
        require(any("slsa" in x.lower() for x in predicates),
                "required image provenance attestation not exported")
        # The OCI layout may contain unreferenced internal artifacts that are
        # independently hashed but cannot grant authority.
        return {
            "schema_version":1,"oci_archive_sha256":file_digest(archive),
            "oci_image_manifest_digest":image["manifest_digest"],
            "oci_index_digest":index_digest,
            "oci_config_digest":image["config_digest"],
            "buildkit_digest":reported_manifest,
            "buildkit_config_digest_metadata_present": reported_config is not None,
            "independent_loaded_image_digest_verified": inspection is not None,
            "buildkit_digest_kind":("oci-index" if reported_manifest == index_digest
                                    else "image-or-index-child"),
            "attestation_predicate_types":sorted(predicates),
            "oci_layers":image["layer_count"],
            "integrity_verified":True,"publisher_signature_verified":False,
            "public_bot_release":False,"artifact_distribution_authorized":False,
        }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--archive",type=Path,required=True)
    p.add_argument("--metadata",type=Path,required=True)
    p.add_argument("--docker-inspect",type=Path)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    try:
        report=verify_archive(a.archive,a.metadata,docker_inspect=a.docker_inspect)
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
        print(json.dumps(report,sort_keys=True))
    except (OSError,ValueError,KeyError,TypeError,tarfile.TarError) as exc:
        p.exit(2,"RELEASE-1 OCI attestation refused: "+str(exc)+"\n")


if __name__=="__main__":
    main()
