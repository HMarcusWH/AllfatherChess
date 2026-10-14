#!/usr/bin/env python3
"""Independently reconstruct OCI image layout and signed-content *claims*.

Checks manifest/config/layers, archive integrity and SBOM/provenance presence;
it does NOT claim a trusted publisher signature or legal distribution.
"""
from __future__ import annotations
import argparse
import hashlib
import gzip
import zlib
import json
import re
import tarfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

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


def verify_layer_diff_ids(archive: tarfile.TarFile, members: dict,
                          layers: list[dict], rootfs: dict) -> list[str]:
    """Independently hash the expanded application layers in rootfs order."""
    require(rootfs.get("type") == "layers" and
            isinstance(rootfs.get("diff_ids"), list) and
            len(rootfs["diff_ids"]) == len(layers),
            "OCI rootfs does not enumerate every application layer")
    computed = []
    for layer, expected in zip(layers, rootfs["diff_ids"]):
        oid = layer.get("digest")
        kind = layer.get("mediaType")
        require(isinstance(oid, str) and oid.startswith("sha256:") and
                HEX.fullmatch(oid[7:]) is not None and
                isinstance(expected, str) and expected.startswith("sha256:") and
                HEX.fullmatch(expected[7:]) is not None,
                "invalid application layer/rootfs digest")
        require(kind in (
            "application/vnd.oci.image.layer.v1.tar",
            "application/vnd.oci.image.layer.v1.tar+gzip",
            "application/vnd.docker.image.rootfs.diff.tar",
            "application/vnd.docker.image.rootfs.diff.tar.gzip",
        ), "unsupported OCI layer compression type")
        member = members.get("blobs/sha256/" + oid[7:])
        require(member is not None and member.isfile(),
                "application layer blob missing from archive")
        stream = archive.extractfile(member)
        require(stream is not None, "unreadable OCI application layer")
        compressed = kind.endswith(("+gzip", ".gzip"))
        with stream:
            decoded = gzip.GzipFile(fileobj=stream, mode="rb") if compressed else stream
            h = hashlib.sha256()
            expanded = 0
            try:
                while True:
                    piece = decoded.read(1 << 20)
                    if not piece:
                        break
                    expanded += len(piece)
                    require(expanded <= (4 << 30),
                            "OCI application layer exceeds bounded expanded size")
                    h.update(piece)
            finally:
                if decoded is not stream:
                    decoded.close()
        observed = "sha256:" + h.hexdigest()
        require(observed == expected,
                "application OCI layer differs from config.rootfs.diff_ids")
        computed.append(observed)
    return computed


SPDX = "https://spdx.dev/Document"
SLSA = {"https://slsa.dev/provenance/v0.2", "https://slsa.dev/provenance/v1"}
STATEMENT = "https://in-toto.io/Statement/v1"


def valid_identity_uri(value: object) -> bool:
    """Require an actual URI for a SLSA build type or builder identity."""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        return False
    parsed = urlsplit(value)
    return ((parsed.scheme in ("https", "http") and bool(parsed.netloc)
             and not any(ch.isspace() for ch in value))
            or (parsed.scheme == "urn" and bool(parsed.path)
                and ":" in parsed.path and not any(ch.isspace() for ch in value)))


def validate_slsa(kind: str, predicate: dict) -> None:
    if kind == "https://slsa.dev/provenance/v1":
        build = predicate.get("buildDefinition")
        run = predicate.get("runDetails")
        require(isinstance(build, dict) and isinstance(run, dict),
                "SLSA provenance v1 predicate incomplete")
        require(valid_identity_uri(build.get("buildType")),
                "SLSA v1 build type missing or invalid")
        require(isinstance(build.get("externalParameters"), dict),
                "SLSA v1 external parameters missing")
        builder = run.get("builder")
        require(isinstance(builder, dict) and
                valid_identity_uri(builder.get("id")),
                "SLSA v1 builder identity missing or invalid")
    elif kind == "https://slsa.dev/provenance/v0.2":
        builder = predicate.get("builder")
        require(isinstance(builder, dict) and
                valid_identity_uri(builder.get("id")) and
                valid_identity_uri(predicate.get("buildType")) and
                isinstance(predicate.get("invocation"), dict),
                "SLSA v0.2 build type, builder or invocation missing")
    else:
        raise ValueError("unsupported SLSA version")


def validate_spdx_document(predicate: dict) -> None:
    """Validate required SPDX 2.x document creation identity and inventory.

    This is schema/structural validation, not an authentication of the tool
    that generated the SBOM or of a publisher's signature.
    """
    creation = predicate.get("creationInfo")
    creators = creation.get("creators") if isinstance(creation, dict) else None
    created = creation.get("created") if isinstance(creation, dict) else None
    valid_date = (isinstance(created, str) and
                  re.fullmatch(
                      r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z",
                      created) is not None)
    if valid_date:
        try:
            datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError:
            valid_date = False
    require(
        predicate.get("spdxVersion") in ("SPDX-2.2", "SPDX-2.3") and
        predicate.get("SPDXID") == "SPDXRef-DOCUMENT" and
        predicate.get("dataLicense") == "CC0-1.0" and
        isinstance(predicate.get("name"), str) and
        bool(predicate["name"].strip()) and
        valid_identity_uri(predicate.get("documentNamespace")) and
        "#" not in predicate["documentNamespace"] and
        valid_date and
        isinstance(creators, list) and bool(creators) and
        all(isinstance(creator, str) and
            creator.startswith(("Tool: ", "Person: ", "Organization: ")) and
            bool(creator.partition(": ")[2].strip()) for creator in creators),
        "SPDX SBOM lacks complete document creation identity")
    # SPDX 2.3 distinguishes package.name from file.fileName.
    # A single shared 'name' predicate wrongly rejected genuine Syft files.
    packages = predicate.get("packages", [])
    files = predicate.get("files", [])
    require(type(packages) is list and type(files) is list and
            bool(packages or files),
            "SPDX SBOM lacks a usable package/file inventory")
    unique = {"SPDXRef-DOCUMENT"}

    def spdx_id(item):
        identifier = item.get("SPDXID") if isinstance(item, dict) else None
        require(isinstance(identifier, str) and
                re.fullmatch(r"SPDXRef-[A-Za-z0-9.-]+", identifier) is not None and
                identifier not in unique,
                "SPDX SBOM inventory has malformed or duplicate SPDXID")
        unique.add(identifier)

    for item in packages:
        spdx_id(item)
        require(isinstance(item.get("name"), str) and
                bool(item["name"].strip()) and
                isinstance(item.get("downloadLocation"), str) and
                bool(item["downloadLocation"].strip()),
                "SPDX SBOM package lacks name or downloadLocation")
    for item in files:
        spdx_id(item)
        checksums = item.get("checksums")
        require(isinstance(item.get("fileName"), str) and
                bool(item["fileName"].strip()) and
                type(checksums) is list and bool(checksums),
                "SPDX SBOM file lacks fileName or checksums")
        require(all(isinstance(checksum, dict) and
                    isinstance(checksum.get("algorithm"), str) and
                    bool(checksum["algorithm"].strip()) and
                    isinstance(checksum.get("checksumValue"), str) and
                    re.fullmatch(r"[A-Fa-f0-9]+",
                                 checksum["checksumValue"]) is not None
                    for checksum in checksums),
                "SPDX SBOM file contains malformed checksums")


def validate_target_platform(platform: object) -> None:
    """Fail closed on unqualified runtime requirements for linux/amd64 v1."""
    require(type(platform) is dict and
            platform.get("os") == "linux" and
            platform.get("architecture") == "amd64",
            "OCI runnable platform must be linux/amd64")
    allowed = {"os", "architecture", "variant", "os.version",
               "os.features", "features"}
    require(not (set(platform) - allowed),
            "OCI runnable platform contains unsupported requirements")
    if "variant" in platform:
        require(type(platform["variant"]) is str and
                platform["variant"] == "v1",
                "OCI runnable platform variant not qualified")
    if "os.version" in platform:
        require(type(platform["os.version"]) is str and
                platform["os.version"] == "",
                "OCI runnable OS version requirement not qualified")
    for field in ("os.features", "features"):
        if field in platform:
            require(type(platform[field]) is list and
                    all(type(item) is str and bool(item.strip())
                        for item in platform[field]) and
                    not platform[field],
                    "OCI runnable platform feature requirements not qualified")


def verify_attestation_subjects(attestations: list[dict], image: dict) -> set[str]:
    """Independently bind BuildKit's in-toto subjects to the application manifest.

    For an image-level qualification the image config or one component layer is
    not an acceptable alternative subject. Attestations are NOT signatures.
    """
    image_digest = image["manifest_digest"]
    predicates = set()
    require(attestations, "OCI archive has no attestation manifests")
    for item in attestations:
        descriptor, statement = item["descriptor"], item["statement"]
        annotations = descriptor.get("annotations") or {}
        require(annotations.get("vnd.docker.reference.type") == "attestation-manifest"
                and annotations.get("vnd.docker.reference.digest") == image_digest,
                "OCI attestation descriptor refers to a different application image")
        platform = descriptor.get("platform") or {}
        require(platform.get("architecture") == "unknown"
                and platform.get("os") == "unknown",
                "OCI attestation artifact uses invalid image platform")
        manifest_subject = item.get("manifest_subject")
        if manifest_subject is not None:
            require(isinstance(manifest_subject, dict) and
                    manifest_subject.get("digest") == image_digest and
                    manifest_subject.get("mediaType") == image["manifest_media_type"] and
                    type(manifest_subject.get("size")) is int and
                    manifest_subject["size"] == image["manifest_size"],
                    "OCI attestation manifest subject differs from application image")
        require(item.get("layer_media_type") == "application/vnd.in-toto+json",
                "unexpected attestation statement layer type")
        require(isinstance(statement, dict), "OCI attestation statement missing")
        statements = statement.get("statements")
        if statements is None:
            statements = [statement]
        require(isinstance(statements, list) and bool(statements),
                "OCI attestation contains no in-toto statements")
        for row in statements:
            require(isinstance(row, dict) and row.get("_type") == STATEMENT,
                    "OCI attestation is not a supported in-toto statement")
            kind = row.get("predicateType")
            require(kind == SPDX or kind in SLSA,
                    "unknown attestation predicate type")
            subjects = row.get("subject")
            require(isinstance(subjects, list) and bool(subjects),
                    "OCI attestation has no image subjects")
            for subject in subjects:
                if not isinstance(subject, dict):
                    raise ValueError("malformed in-toto image subject")
                digest = subject.get("digest")
                require(isinstance(subject.get("name"), str) and
                        bool(subject["name"].strip()) and
                        isinstance(digest, dict) and
                        set(digest) == {"sha256"} and
                        digest["sha256"] == image_digest.removeprefix("sha256:"),
                        "OCI attestation subject does not name application manifest")
            predicate = row.get("predicate")
            require(isinstance(predicate, dict) and bool(predicate),
                    "OCI attestation predicate document empty")
            if kind == SPDX:
                validate_spdx_document(predicate)
            else:
                validate_slsa(kind, predicate)
            predicates.add(kind)
    require(SPDX in predicates, "required image SBOM attestation not exported")
    require(bool(predicates.intersection(SLSA)),
            "required image provenance attestation not exported")
    return predicates

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

        def get(oid, expected_size):
            """Hash the actual tar member and validate its descriptor byte length."""
            require(isinstance(oid, str) and oid.startswith("sha256:") and
                    HEX.fullmatch(oid[7:]) is not None, "invalid OCI digest")
            require(type(expected_size) is int and expected_size >= 0,
                    "missing or invalid OCI descriptor size")
            name = "blobs/sha256/" + oid[7:]
            member = by_name.get(name)
            require(member is not None and member.isfile(), "missing OCI blob " + oid)
            require(member.size == expected_size,
                    "OCI descriptor size differs from referenced blob")
            if name in blobs:
                return blobs[name]
            stream = inp.extractfile(member)
            require(stream is not None, "unreadable OCI blob")
            sha = hashlib.sha256()
            body = bytearray() if member.size <= 8 * 1024 * 1024 else None
            with stream:
                while True:
                    data = stream.read(1 << 20)
                    if not data:
                        break
                    sha.update(data)
                    if body is not None:
                        body.extend(data)
            require(sha.hexdigest() == oid[7:], "OCI blob hash mismatch " + oid)
            referenced.add(name)
            if body is not None:
                blobs[name] = bytes(body)
                return blobs[name]
            return None

        INDEX_TYPE = "application/vnd.oci.image.index.v1+json"
        MANIFEST_TYPES = ("application/vnd.oci.image.manifest.v1+json",
                          "application/vnd.docker.distribution.manifest.v2+json")
        CONFIG_TYPES = ("application/vnd.oci.image.config.v1+json",
                        "application/vnd.docker.container.image.v1+json",
                        "application/vnd.oci.empty.v1+json")
        LAYER_TYPES = ("application/vnd.oci.image.layer.v1.tar",
                       "application/vnd.oci.image.layer.v1.tar+gzip",
                       "application/vnd.docker.image.rootfs.diff.tar",
                       "application/vnd.docker.image.rootfs.diff.tar.gzip")
        top = index.get("manifests")
        require(isinstance(top, list) and bool(top),
                "OCI layout has no root descriptors")
        reported_manifest = meta.get("containerimage.digest")
        matches = [d for d in top if isinstance(d, dict)
                   and d.get("digest") == reported_manifest]
        require(len(matches) == 1,
                "BuildKit descriptor digest does not match exported OCI root")
        root_descriptor = matches[0]
        root_type = root_descriptor.get("mediaType")
        require(root_type == INDEX_TYPE or root_type in MANIFEST_TYPES,
                "BuildKit root descriptor has unsupported media type")
        if root_type == INDEX_TYPE:
            require(len(top) == 1,
                    "nested BuildKit root must be the sole layout root")
        else:
            require(all(d is root_descriptor or
                        (isinstance(d, dict) and
                         (d.get("annotations") or {}).get(
                             "vnd.docker.reference.type") == "attestation-manifest")
                        for d in top),
                    "flat OCI layout has unrelated root descriptors")
        reported_descriptor = meta.get("containerimage.descriptor")
        require(isinstance(reported_descriptor, dict) and
                type(reported_descriptor.get("size")) is int and
                all(reported_descriptor.get(k) == root_descriptor.get(k)
                    for k in ("digest", "mediaType", "size")) and
                type(root_descriptor.get("size")) is int,
                "BuildKit image descriptor identity/size differs from OCI root")
        images, attestations = [], []
        visited_nodes = [0]

        def inspect_descriptor(descriptor, depth=0, ancestors=frozenset()):
            require(depth <= 8 and isinstance(descriptor, dict),
                    "OCI descriptor nesting invalid")
            visited_nodes[0] += 1
            require(visited_nodes[0] <= 256,
                    "OCI descriptor graph exceeds node budget")
            typ = descriptor.get("mediaType")
            require(typ == INDEX_TYPE or typ in MANIFEST_TYPES,
                    "unsupported OCI descriptor kind")
            oid = descriptor.get("digest")
            require(oid not in ancestors, "OCI descriptor graph contains a cycle")
            raw = get(oid, descriptor.get("size"))
            require(raw is not None, "OCI descriptor JSON too large or missing")
            document = json.loads(raw)
            require(isinstance(document, dict) and
                    document.get("schemaVersion") == 2,
                    "invalid nested OCI index or image manifest")
            require(document.get("mediaType") in (None, typ),
                    "OCI document media type differs from descriptor")
            if typ == INDEX_TYPE:
                children = document.get("manifests")
                require(isinstance(children, list) and bool(children),
                        "nested OCI index has no child descriptors")
                # Validate every *advertised* index platform on the path to
                # the runnable image. Attestation-only subindexes do not
                # determine the runner's platform.
                contains_application = False
                for child in children:
                    child_application = inspect_descriptor(
                        child, depth + 1, ancestors | {oid})
                    contains_application = child_application or contains_application
                if contains_application and "platform" in descriptor:
                    validate_target_platform(descriptor["platform"])
                return contains_application
            cfg = document.get("config")
            require(isinstance(cfg, dict) and
                    cfg.get("mediaType") in CONFIG_TYPES,
                    "OCI image has invalid configuration media type")
            config_bytes = get(cfg.get("digest"), cfg.get("size"))
            require(config_bytes is not None,
                    "OCI config cannot be independently parsed")
            details = json.loads(config_bytes)
            require(isinstance(details, dict), "malformed OCI configuration")
            layers = document.get("layers")
            require(isinstance(layers, list) and bool(layers),
                    "OCI image missing layers")
            attested = (descriptor.get("annotations") or {}).get(
                "vnd.docker.reference.type") == "attestation-manifest"
            for layer in layers:
                require(isinstance(layer, dict) and
                        layer.get("mediaType") in (
                            ("application/vnd.in-toto+json",) if attested
                            else LAYER_TYPES),
                        "unsupported OCI statement or application layer mediaType")
                get(layer.get("digest"), layer.get("size"))
            if attested:
                for layer in layers:
                    content = get(layer["digest"], layer["size"])
                    require(content is not None,
                            "oversized attestation statement refused")
                    attestations.append({
                        "descriptor": descriptor,
                        "manifest_subject": document.get("subject"),
                        "layer_media_type": layer.get("mediaType"),
                        "statement": json.loads(content)})
                return False
            else:
                # The index descriptor is how an OCI consumer selects a platform.
                # A flat manifest may not advertise one, but any advertised
                # platform must agree with the configuration bytes.
                advertised = descriptor.get("platform")
                if advertised is None:
                    require(root_type in MANIFEST_TYPES,
                            "nested OCI application descriptor lacks platform")
                else:
                    validate_target_platform(advertised)
                require(details.get("architecture") == "amd64" and
                        details.get("os") == "linux",
                        "unexpected non-attestation OCI application manifest")
                images.append({"manifest_digest": oid,
                               "manifest_media_type": typ,
                               "manifest_size": descriptor["size"],
                               "config_digest": cfg["digest"],
                               "config": details, "layers": layers,
                               "layer_count": len(layers)})
                return True

        for entry in top:
            inspect_descriptor(entry)
        require(len(images) == 1,
                "require exactly one linux/amd64 application image")
        image = images[0]
        if root_type in MANIFEST_TYPES:
            require(image["manifest_digest"] == root_descriptor["digest"],
                    "flat OCI root is not the runnable application")
        diff_ids=verify_layer_diff_ids(inp,by_name,image["layers"],
                                        image["config"].get("rootfs") or {})
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
        # The identity was selected from the exported layout root descriptor,
        # not from an unrestricted set of digests reachable in the archive.
        require(reported_manifest == root_descriptor["digest"],
                "BuildKit root descriptor changed during traversal")
        config=image["config"].get("config") or {}
        require(config.get("User") in ("10001:10001", "10001") and
                config.get("Entrypoint")==[
                    "/opt/online3b/bin/python",
                    "/opt/release-tools/deploy/release/entrypoint.py"],
                "OCI image launched with unapproved identity/entrypoint")
        require(not config.get("ExposedPorts"), "offline candidate must not expose ports")
        predicates=verify_attestation_subjects(attestations,image)
        # The OCI layout may contain unreferenced internal artifacts that are
        # independently hashed but cannot grant authority.
        return {
            "schema_version":1,"oci_archive_sha256":file_digest(archive),
            "oci_image_manifest_digest":image["manifest_digest"],
            "oci_index_digest":index_digest,
            "oci_exported_root_digest":root_descriptor["digest"],
            "oci_exported_root_size":root_descriptor["size"],
            "oci_config_digest":image["config_digest"],
            "buildkit_digest":reported_manifest,
            "buildkit_config_digest_metadata_present": reported_config is not None,
            "independent_loaded_image_digest_verified": inspection is not None,
            "buildkit_digest_kind":("oci-index" if root_type == INDEX_TYPE
                                    else "application-manifest"),
            "oci_application_identity_verified":True,
            "oci_attestation_references_verified":True,
            "oci_sbom_verified":SPDX in predicates,
            "oci_provenance_verified":bool(predicates.intersection(SLSA)),
            "oci_layers_verified":True,
            "attestation_predicate_types":sorted(predicates),
            "oci_layers":image["layer_count"],
            "oci_diff_ids_verified":True,
            "oci_diff_id_count":len(diff_ids),
            "oci_attestation_subjects_verified":True,
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
    except (OSError,ValueError,KeyError,TypeError,EOFError,
            zlib.error,tarfile.TarError) as exc:
        p.exit(2,"RELEASE-1 OCI attestation refused: "+str(exc)+"\n")


if __name__=="__main__":
    main()
