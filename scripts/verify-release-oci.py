#!/usr/bin/env python3
"""Independently reconstruct OCI image layout and signed-content *claims*.

Checks manifest/config/layers, archive integrity and SBOM/provenance presence;
it does NOT claim a trusted publisher signature or legal distribution.
"""
from __future__ import annotations
import argparse
import base64
import binascii
from datetime import datetime
import os
import stat
import hashlib
import gzip
import zlib
import json
import re
import ipaddress
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


def digest_verified_archive(fileobj, archive: Path, before) -> str:
    """Hash the *opened/verified inode*, not a possibly replaced pathname.

    Detect both in-place mutation and atomic pathname replacement. A later
    aggregate recomputes the digest from the supplied archive path, so any
    replacement after this function also fails admission.
    """
    fileobj.seek(0)
    h = hashlib.sha256()
    for part in iter(lambda: fileobj.read(1 << 20), b""):
        h.update(part)
    after = os.fstat(fileobj.fileno())
    path_stat = os.stat(archive, follow_symlinks=False)
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size,
                          s.st_mtime_ns, s.st_ctime_ns)
    require(stat.S_ISREG(after.st_mode) and
            stat.S_ISREG(path_stat.st_mode) and
            identity(before) == identity(after) == identity(path_stat),
            "OCI archive file was mutated or replaced during verification")
    return h.hexdigest()


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
        require(isinstance(docker_inspection, dict) and
                docker_inspection.get("Id") == image_config_digest and
                docker_inspection.get("Architecture") == "amd64" and
                docker_inspection.get("Os", "linux") == "linux",
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


def valid_uri_syntax(value: object, allowed_schemes: set[str], *,
                     allow_git_ssh_user: bool = False) -> bool:
    """One RFC3986 URI grammar for SPDX identities, downloads and SLSA.

    URI syntax is common; field-specific schemes, fragments and special tokens
    remain caller policy. Require percent-encoding for non-ASCII bytes.
    """
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    # RFC3986 unreserved + reserved delimiters and encoded %HH octets.
    if re.fullmatch(r"[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", value,
                    re.ASCII) is None:
        return False
    if (re.search(r"%(?![0-9A-Fa-f]{2})", value) or
            re.search(r"%(?:0[0-9A-Fa-f]|1[0-9A-Fa-f]|7[fF])",
                      value, re.IGNORECASE)):
        return False
    try:
        uri = urlsplit(value)
        # RFC3986: "[" and "]" are only valid around an IP-literal
        # *authority*, never in a path, query, fragment or URN NSS.
        # The component grammar also prevents reserved gen-delimiters
        # being treated as ordinary pchar at any position.
        pchar = r"A-Za-z0-9._~!$&'()*+,;=:@%-"
        if (re.fullmatch("[" + pchar + r"/]*", uri.path,
                         re.ASCII) is None or
                re.fullmatch("[" + pchar + r"/?]*", uri.query,
                             re.ASCII) is None or
                re.fullmatch("[" + pchar + r"/?]*", uri.fragment,
                             re.ASCII) is None):
            return False
        if uri.scheme not in allowed_schemes:
            return False
        if uri.scheme == "urn":
            return not uri.netloc and bool(uri.path) and ":" in uri.path
        if (not uri.netloc or not uri.hostname or
                "%" in uri.netloc or uri.netloc.count("@") > 1 or
                uri.password is not None):
            return False
        port = uri.port
        if port is not None and not (1 <= port <= 65535):
            return False
        if uri.username is not None and not (
                allow_git_ssh_user and uri.username == "git" and
                (uri.scheme == "ssh" or uri.scheme.endswith("+ssh"))):
            return False
        host = uri.hostname
        if ":" in host:
            if host.lower().startswith("v"):
                # RFC3986 IPvFuture: v HEXDIG+ '.' 1*(unreserved/subdelims/:)
                if re.fullmatch(
                        r"v[0-9A-Fa-f]+\.[A-Za-z0-9._~!        if ":" in host:
            try:
                ipaddress.IPv6Address(host)
            except ValueError:
                return False'()*+,;=:-]+",
                        host,re.IGNORECASE) is None:
                    return False
            else:
                try:
                    ipaddress.IPv6Address(host)
                except ValueError:
                    return False
        else:
            labels = host.split(".")
            if (len(host) > 253 or not all(re.fullmatch(
                    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
                    label) for label in labels)):
                return False
        return True
    except ValueError:
        return False


def valid_identity_uri(value: object) -> bool:
    """SLSA identities support HTTP(S) and valid URNs, not user credentials."""
    return valid_uri_syntax(value, {"https", "http", "urn"})


def valid_spdx_namespace(value: object) -> bool:
    """SPDX namespace uses a valid identity URI without any # delimiter."""
    return valid_identity_uri(value) and "#" not in value


def _optional_object(container: dict, field: str) -> bool:
    value = container.get(field)
    return value is None or type(value) is dict


def _optional_string(container: dict, field: str) -> bool:
    value = container.get(field)
    return value is None or type(value) is str


def _optional_string_map(container: dict, field: str) -> bool:
    value = container.get(field)
    return (value is None or
            (type(value) is dict and all(
                type(key) is str and type(item) is str
                for key, item in value.items())))


def _resource_uri(value: object) -> bool:
    """External resource identifiers are URI, not arbitrary strings.

    A conservative set of schemes includes git+https and OCI resource URIs;
    general SLSA builder identities remain governed by valid_identity_uri.
    """
    if type(value) is not str or not value:
        return False
    schemes={"http","https","git+https","git+http","git+ssh",
             "git","ssh","ftp","ftps","oci","docker","urn"}
    return valid_uri_syntax(value,schemes,allow_git_ssh_user=True)


def _valid_digest_set(value: object) -> bool:
    if type(value) is not dict or not value:
        return False
    sizes={"sha1":40,"sha256":64,"sha384":96,"sha512":128}
    for algorithm,raw in value.items():
        if (type(algorithm) is not str or
                re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}",algorithm) is None or
                type(raw) is not str or
                re.fullmatch(r"[0-9a-fA-F]+",raw) is None):
            return False
        expected=sizes.get(algorithm.lower())
        if expected is not None:
            if len(raw)!=expected:
                return False
        elif not (16 <= len(raw) <= 128 and len(raw)%2==0):
            return False
    return True


def _valid_resource_descriptor(value: object) -> bool:
    """Typed in-toto ResourceDescriptor with at least one meaningful identity.

    The normative minimum is one of uri/digest/content. We also validate
    nonselected identity fields if present; combining one good URI with an
    empty digest or malformed base64 cannot bypass the contract.
    """
    if type(value) is not dict:
        return False
    if not all(_optional_string(value, name) for name in
               ("uri", "name", "downloadLocation", "mediaType", "content",
                "localName")):
        return False
    uri=value.get("uri")
    digest=value.get("digest")
    content=value.get("content")
    has_uri=type(uri) is str and _resource_uri(uri)
    has_digest=_valid_digest_set(digest)
    has_content=type(content) is str
    if "uri" in value and uri is not None and not has_uri:
        return False
    if "digest" in value and digest is not None and not has_digest:
        return False
    if has_content:
        try:
            base64.b64decode(content,validate=True)
        except (ValueError,binascii.Error):
            return False
    if "content" in value and content is not None and not has_content:
        return False
    location=value.get("downloadLocation")
    if location not in (None,"") and not _resource_uri(location):
        return False
    return bool(has_uri or has_digest or has_content) and _optional_object(
        value,"annotations")


def _rfc3339_timestamp(value: object) -> bool:
    if value in (None,""):
        return True
    if type(value) is not str or re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
            r"(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})",value) is None:
        return False
    try:
        observed=datetime.fromisoformat(value.replace("Z","+00:00"))
    except ValueError:
        return False
    return observed.tzinfo is not None and observed.utcoffset() is not None


def _optional_resource_list(container: dict, field: str) -> bool:
    value = container.get(field)
    return (value is None or
            (type(value) is list and
             all(_valid_resource_descriptor(item) for item in value)))


def _valid_slsa_metadata(value: object, *, version: str) -> bool:
    if value is None:
        return True
    if type(value) is not dict:
        return False
    keys = ("invocationId", "startedOn", "finishedOn") if version == "v1" else (
        "buildInvocationId", "buildStartedOn", "buildFinishedOn")
    if not all((_rfc3339_timestamp(value.get(field))
                if field in ("startedOn","finishedOn",
                             "buildStartedOn","buildFinishedOn")
                else _optional_string(value,field))
               for field in keys):
        return False
    first=value.get("startedOn" if version=="v1" else "buildStartedOn")
    last=value.get("finishedOn" if version=="v1" else "buildFinishedOn")
    if first not in (None,"") and last not in (None,""):
        start=datetime.fromisoformat(first.replace("Z","+00:00"))
        finish=datetime.fromisoformat(last.replace("Z","+00:00"))
        if finish < start:
            return False
    if version == "v0.2":
        completeness = value.get("completeness")
        if completeness is not None and (
                type(completeness) is not dict or
                any(type(item) is not bool for item in completeness.values())):
            return False
        reproducible = value.get("reproducible")
        if reproducible is not None and type(reproducible) is not bool:
            return False
    return True


def validate_slsa(kind: str, predicate: dict) -> None:
    """Recognized SLSA v1/v0.2 fields must satisfy their JSON type contracts.

    Absent and null *optional* fields remain equivalent per SLSA parsing
    rules. Unrecognized extension fields do not grant extra authority.
    """
    require(type(predicate) is dict, "SLSA predicate not a JSON object")
    if kind == "https://slsa.dev/provenance/v1":
        build = predicate.get("buildDefinition")
        run = predicate.get("runDetails")
        require(type(build) is dict and type(run) is dict,
                "SLSA provenance v1 predicate incomplete")
        require(valid_identity_uri(build.get("buildType")),
                "SLSA v1 build type missing or invalid")
        require(type(build.get("externalParameters")) is dict and
                _optional_object(build, "internalParameters") and
                _optional_resource_list(build, "resolvedDependencies"),
                "SLSA v1 build inputs contain malformed optional fields")
        builder = run.get("builder")
        require(type(builder) is dict and
                valid_identity_uri(builder.get("id")) and
                _optional_string_map(builder, "version") and
                _optional_resource_list(builder, "builderDependencies"),
                "SLSA v1 builder identity or field shape invalid")
        require(_valid_slsa_metadata(run.get("metadata"),version="v1") and
                _optional_resource_list(run, "byproducts"),
                "SLSA v1 run metadata/byproducts malformed")
    elif kind == "https://slsa.dev/provenance/v0.2":
        builder = predicate.get("builder")
        invocation = predicate.get("invocation")
        require(type(builder) is dict and
                valid_identity_uri(builder.get("id")) and
                valid_identity_uri(predicate.get("buildType")) and
                type(invocation) is dict,
                "SLSA v0.2 build type, builder or invocation missing")
        require(_optional_string_map(builder, "version") and
                _optional_resource_list(builder, "builderDependencies") and
                _optional_object(predicate, "buildConfig") and
                _optional_resource_list(predicate, "materials") and
                _optional_object(invocation, "parameters") and
                _optional_object(invocation, "environment") and
                _optional_object(invocation, "configSource") and
                _valid_slsa_metadata(predicate.get("metadata"),version="v0.2"),
                "SLSA v0.2 recognized optional fields malformed")
        source = invocation.get("configSource")
        if type(source) is dict:
            require(all(_optional_string(source, name) for name in
                        ("uri", "entryPoint")) and
                    _optional_string_map(source, "digest"),
                    "SLSA v0.2 config source malformed")
    else:
        raise ValueError("unsupported SLSA version")


# Supported canonical SPDX 2.2/2.3 checksum algorithms and output widths.
# MD6 is enumerated by SPDX but its length is parameterized; this release
# profile refuses MD6 rather than accepting an arbitrary-length hex digest.
SPDX_CHECKSUM_HEX_LENGTH = {
    "SHA1": 40, "SHA224": 56, "SHA256": 64, "SHA384": 96,
    "SHA512": 128, "SHA3-256": 64, "SHA3-384": 96,
    "SHA3-512": 128, "BLAKE2b-256": 64, "BLAKE2b-384": 96,
    "BLAKE2b-512": 128, "BLAKE3": 64, "MD2": 32, "MD4": 32,
    "MD5": 32, "ADLER32": 8,
}


def valid_spdx_download_location(value: object) -> bool:
    """Recognize SPDX special tokens or supported strictly valid URL/VCS URI."""
    if value in ("NONE", "NOASSERTION"):
        return True
    return valid_uri_syntax(value, {
        "http", "https", "ftp", "ftps", "ssh", "git", "git+https",
        "git+http", "git+ssh", "git+git", "svn", "svn+https",
        "svn+ssh", "hg", "hg+https", "hg+ssh",
    }, allow_git_ssh_user=True)

def valid_spdx_checksums(value: object) -> bool:
    """Check every listed digest against the supported SPDX algorithm width."""
    if type(value) is not list or not value:
        return False
    seen = set()
    for checksum in value:
        if not isinstance(checksum, dict):
            return False
        algorithm = checksum.get("algorithm")
        digest = checksum.get("checksumValue")
        width = SPDX_CHECKSUM_HEX_LENGTH.get(algorithm) if isinstance(algorithm, str) else None
        if (width is None or algorithm in seen or
                type(digest) is not str or len(digest) != width or
                re.fullmatch(r"[0-9a-f]+", digest) is None):
            return False
        seen.add(algorithm)
    return True


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
        valid_spdx_namespace(predicate.get("documentNamespace")) and
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
                valid_spdx_download_location(item.get("downloadLocation")),
                "SPDX SBOM package has invalid downloadLocation")
        if "checksums" in item:
            require(valid_spdx_checksums(item["checksums"]),
                    "SPDX SBOM package contains invalid checksums")
    for item in files:
        spdx_id(item)
        require(isinstance(item.get("fileName"), str) and
                bool(item["fileName"].strip()) and
                valid_spdx_checksums(item.get("checksums")),
                "SPDX SBOM file contains invalid fileName or checksums")


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
                "OCI runnable platform OS version requirement not qualified")
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
        # Absent wrapper means BuildKit's valid singleton in-toto statement.
        # An explicitly present null/object/string/empty wrapper is invalid.
        if "statements" in statement:
            statements = statement["statements"]
        else:
            statements = [statement]
        require(type(statements) is list and bool(statements),
                "OCI attestation has invalid in-toto statements wrapper")
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
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(archive, flags)
    with os.fdopen(fd, "rb") as archive_fp:
        opened = os.fstat(archive_fp.fileno())
        require(stat.S_ISREG(opened.st_mode),
                "OCI archive is not a regular immutable file")
        with tarfile.open(fileobj=archive_fp, mode="r:*") as inp:
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
                    # Only OCI configuration platform fields participate in
                    # platform selection. Legitimate config/rootfs/history data
                    # must never be mistaken for descriptor platform extras.
                    image_platform = {
                        field: details[field] for field in
                        ("os", "architecture", "variant", "os.version", "os.features")
                        if field in details
                    }
                    validate_target_platform(image_platform)
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
            require(isinstance(config, dict), "malformed runnable OCI configuration")
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
                "schema_version":1,"oci_archive_sha256":digest_verified_archive(archive_fp,archive,opened),
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
