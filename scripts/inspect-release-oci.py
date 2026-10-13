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


def inspect(archive: Path, metadata: Path) -> dict:
    """Bounded diagnostic traversal of indexes, manifests, and statement summaries."""
    result = {"schema_version": 1, "diagnostic_only": True,
              "integrity_verified": False, "public_bot_release": False}
    build = json.loads(metadata.read_text(encoding="utf-8"))
    result["buildkit_image_name"] = build.get("image.name")
    result["buildkit_digest"] = build.get("containerimage.digest")
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
            return {"digest": oid, "media_type": typ,
                    "declared_size": size, "actual_size": actual.size,
                    "descriptor_path": list(ancestry) + [oid],
                    "platform": desc.get("platform"),
                    "annotations": desc.get("annotations", {})}

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
            rec["artifact_type"] = manifest.get("artifactType")
            rec["manifest_subject"] = manifest.get("subject")
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
                row = {"digest": lid,
                       "media_type": layer.get("mediaType"),
                       "annotations": layer.get("annotations", {}),
                       "declared_size": layer.get("size"),
                       "actual_size": member.size if member else None}
                if attested and layer.get("mediaType") == "application/vnd.in-toto+json":
                    statement = document("blobs/sha256/" + str(lid)[7:])
                    statements = statement.get("statements")
                    if not isinstance(statements, list):
                        statements = [statement]
                    row["statements"] = [{
                        "_type": st.get("_type"),
                        "predicate_type": st.get("predicateType"),
                        "subject_count": len(st.get("subject", []))
                            if isinstance(st.get("subject"), list) else None,
                        "subjects": st.get("subject"),
                        "predicate_keys": sorted((st.get("predicate") or {}).keys())
                            if isinstance(st.get("predicate"), dict) else [],
                    } for st in statements if isinstance(st, dict)]
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
