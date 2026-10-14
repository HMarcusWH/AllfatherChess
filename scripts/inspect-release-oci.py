#!/usr/bin/env python3
"""Diagnostic-only OCI structure inspection; never grants image qualification.

Read only bounded JSON descriptors and statement metadata from the archive.
No binary layer, complete SBOM/provenance predicate, or credentials are exported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import Path

MAX_JSON = 8 * 1024 * 1024
SAFE_ANNOTATION_KEYS = {"vnd.docker.reference.type", "vnd.docker.reference.digest"}
SAFE_SPDX_KEYS = {
    "SPDXID", "spdxVersion", "name", "downloadLocation", "fileName",
    "checksums", "filesAnalyzed", "licenseConcluded", "licenseDeclared",
    "licenseInfoFromFiles", "licenseInfoInFiles", "copyrightText",
    "versionInfo", "externalRefs", "primaryPackagePurpose",
    "sourceInfo", "supplier", "originator", "verificationCode",
    "packageVerificationCode", "fileTypes", "description",
}


def digest_only(value):
    if (isinstance(value, str) and len(value) == 71 and
            value.startswith("sha256:") and
            all(ch in "0123456789abcdef" for ch in value[7:])):
        return value
    return None


def approved_annotations(value):
    if type(value) is not dict:
        return {}
    result = {}
    for key in SAFE_ANNOTATION_KEYS:
        observed = value.get(key)
        if key.endswith(".digest"):
            verified = digest_only(observed)
            if verified:
                result[key] = verified
        elif observed == "attestation-manifest":
            result[key] = observed
    return result


def approved_platform(value):
    if type(value) is not dict:
        return None
    return {
        "os": value.get("os") if value.get("os") in ("linux", "unknown") else "other",
        "architecture": value.get("architecture")
        if value.get("architecture") in ("amd64", "unknown") else "other",
        "additional_field_count": max(len(value)-2, 0),
    }



def inspect(archive: Path, metadata: Path) -> dict:
    """Bounded diagnostic traversal of indexes, manifests, and statement summaries."""
    result = {"schema_version": 1, "diagnostic_only": True,
              "integrity_verified": False, "public_bot_release": False}
    build = json.loads(metadata.read_text(encoding="utf-8"))
    result["buildkit_digest"] = digest_only(build.get("containerimage.digest"))
    with tarfile.open(archive, "r:*") as tar:
        members = {}
        for member in tar.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or member.name in members:
                raise ValueError("invalid or duplicate OCI diagnostic archive path")
            if member.issym() or member.islnk():
                raise ValueError("linked OCI diagnostic archive member")
            members[member.name] = member

        def document(path: str):
            member = members.get(path)
            if member is None or not member.isfile() or member.size > MAX_JSON:
                raise ValueError("missing or oversized descriptor: " + path)
            stream = tar.extractfile(member)
            if stream is None:
                raise ValueError("unreadable descriptor: " + path)
            with stream:
                content = stream.read(MAX_JSON + 1)
            if len(content) > MAX_JSON:
                raise ValueError("oversized descriptor: " + path)
            return json.loads(content)

        def bounded_descriptor(desc, ancestry):
            if not isinstance(desc, dict):
                raise ValueError("malformed OCI descriptor")
            oid = desc.get("digest")
            size = desc.get("size")
            typ = desc.get("mediaType")
            if (not isinstance(oid, str) or len(oid) != 71 or
                    not oid.startswith("sha256:") or
                    any(c not in "0123456789abcdef" for c in oid[7:]) or
                    type(size) is not int or size < 0):
                raise ValueError("malformed OCI diagnostic descriptor identity")
            if oid in ancestry:
                raise ValueError("cyclic OCI descriptor graph")
            path = "blobs/sha256/" + oid[7:]
            actual = members.get(path)
            if actual is None or not actual.isfile():
                raise ValueError("missing OCI referenced blob")
            return {"digest": oid,
                    "media_type": typ if isinstance(typ, str) and
                        typ.startswith("application/vnd.") and len(typ)<100
                        else "unrecognized",
                    "declared_size": size, "actual_size": actual.size,
                    "descriptor_path": list(ancestry) + [oid],
                    "platform": approved_platform(desc.get("platform")),
                    "annotations": approved_annotations(desc.get("annotations"))}

        index = document("index.json")
        if not isinstance(index, dict) or not isinstance(index.get("manifests"), list):
            raise ValueError("invalid layout index")
        roots = index["manifests"]
        result["index_manifests"] = []
        result["application_manifest_digest"] = None
        result["attestation_manifest_digests"] = []
        result["exported_root_digest"] = build.get("containerimage.digest")
        inspected = [0]

        def traverse(desc, ancestry=()):
            inspected[0] += 1
            if inspected[0] > 256 or len(ancestry) > 8:
                raise ValueError("OCI diagnostic graph too deep or large")
            rec = bounded_descriptor(desc, ancestry)
            oid = rec["digest"]
            if rec["declared_size"] != rec["actual_size"]:
                raise ValueError("OCI diagnostic descriptor size mismatch")
            typ = rec["media_type"]
            if typ == "application/vnd.oci.image.index.v1+json":
                node = document("blobs/sha256/" + oid[7:])
                if not isinstance(node, dict) or not isinstance(node.get("manifests"), list):
                    raise ValueError("malformed nested OCI index")
                rec["children"] = [traverse(child, ancestry + (oid,))
                                   for child in node["manifests"]]
                rec["artifact_type"] = "nested-index"
                return rec
            if typ not in ("application/vnd.oci.image.manifest.v1+json",
                           "application/vnd.docker.distribution.manifest.v2+json"):
                raise ValueError("unexpected descriptor media type")
            manifest = document("blobs/sha256/" + oid[7:])
            rec["artifact_type"] = (
                "application" if not (
                    (rec["annotations"] or {}).get("vnd.docker.reference.type")
                        == "attestation-manifest") else "attestation")
            subject = manifest.get("subject")
            if isinstance(subject, dict):
                oid_subject = digest_only(subject.get("digest"))
                referenced = (members.get("blobs/sha256/" + oid_subject[7:])
                              if oid_subject else None)
                rec["manifest_subject"] = {
                    "digest": oid_subject,
                    "media_type": subject.get("mediaType")
                        if subject.get("mediaType") in (
                            "application/vnd.oci.image.manifest.v1+json",
                            "application/vnd.docker.distribution.manifest.v2+json")
                        else "unrecognized",
                    "size": subject.get("size") if type(subject.get("size")) is int
                        else None,
                }
                rec["subject_declared_size"] = rec["manifest_subject"]["size"]
                rec["subject_actual_size"] = (
                    referenced.size if referenced is not None and referenced.isfile()
                    else None)
            else:
                rec["manifest_subject"] = None
            rec["config_type"] = (manifest.get("config") or {}).get("mediaType")
            attested = (rec["annotations"] or {}).get(
                "vnd.docker.reference.type") == "attestation-manifest"
            if attested:
                result["attestation_manifest_digests"].append(oid)
            else:
                result["application_manifest_digest"] = oid
            rec["layers"] = []
            for layer in manifest.get("layers", []):
                lid = layer.get("digest", "")
                member = members.get("blobs/sha256/" + str(lid)[7:])
                row = {"digest": digest_only(lid),
                       "media_type": layer.get("mediaType")
                            if layer.get("mediaType") in (
                                "application/vnd.in-toto+json",
                                "application/vnd.oci.image.layer.v1.tar",
                                "application/vnd.oci.image.layer.v1.tar+gzip",
                                "application/vnd.docker.image.rootfs.diff.tar",
                                "application/vnd.docker.image.rootfs.diff.tar.gzip")
                            else "unrecognized",
                       "annotations": approved_annotations(layer.get("annotations")),
                       "declared_size": layer.get("size") if type(layer.get("size")) is int
                            else None,
                       "actual_size": member.size if member else None}
                if attested and layer.get("mediaType") == "application/vnd.in-toto+json":
                    statement = document("blobs/sha256/" + str(lid)[7:])
                    statements = statement.get("statements")
                    if not isinstance(statements, list):
                        statements = [statement]
                    row["statements"] = []
                    for st in statements:
                        if not isinstance(st, dict):
                            continue
                        predicate = st.get("predicate")
                        safe_keys = (sorted(predicate.keys())
                                     if isinstance(predicate, dict) else [])
                        summary = {
                            "_type": st.get("_type"),
                            "predicate_type": st.get("predicateType"),
                            "subject_count": len(st.get("subject", []))
                                if isinstance(st.get("subject"), list) else None,
                            # Subject names and untrusted predicate values
                            # must never reach retained CI artifacts.
                            "subject_digests": sorted({
                                digest for sub in st.get("subject", [])
                                if isinstance(sub, dict)
                                and isinstance(sub.get("digest"), dict)
                                for digest in [digest_only(
                                    "sha256:" + str(sub["digest"].get("sha256", "")))]
                                if digest
                            })[:8] if type(st.get("subject")) is list else [],
                            "predicate_keys": [k for k in safe_keys
                                               if k in SAFE_SPDX_KEYS][:32],
                        }
                        if st.get("predicateType") == "https://spdx.dev/Document":
                            packages = (predicate.get("packages")
                                        if isinstance(predicate, dict) else None)
                            files = (predicate.get("files")
                                     if isinstance(predicate, dict) else None)
                            summary["spdx_package_count"] = (
                                len(packages) if type(packages) is list else None)
                            summary["spdx_file_count"] = (
                                len(files) if type(files) is list else None)
                            summary["spdx_package_field_keys"] = (
                                sorted({key for item in packages[:32]
                                        if isinstance(item, dict) for key in item
                                        if key in SAFE_SPDX_KEYS})[:32]
                                if type(packages) is list else [])
                            summary["spdx_file_field_keys"] = (
                                sorted({key for item in files[:32]
                                        if isinstance(item, dict) for key in item
                                        if key in SAFE_SPDX_KEYS})[:32]
                                if type(files) is list else [])
                        row["statements"].append(summary)
                rec["layers"].append(row)
            return rec

        result["index_manifests"] = [traverse(root) for root in roots]
        result["descriptor_count"] = inspected[0]
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", type=Path, required=True)
    ap.add_argument("--metadata", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    try:
        report = inspect(args.archive, args.metadata)
    except (OSError, ValueError, TypeError, KeyError, tarfile.TarError) as exc:
        report = {"schema_version": 1, "diagnostic_only": True,
                  "integrity_verified": False, "public_bot_release": False,
                  "inspection_error": type(exc).__name__ + ": " + str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps({"diagnostic_only": True,
                      "descriptor_count": len(report.get("index_manifests", [])),
                      "inspection_error": report.get("inspection_error")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
