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
    result = {"schema_version": 1, "diagnostic_only": True,
              "integrity_verified": False, "public_bot_release": False}
    build = json.loads(metadata.read_text(encoding="utf-8"))
    result["buildkit_image_name"] = build.get("image.name")
    result["buildkit_digest"] = build.get("containerimage.digest")
    with tarfile.open(archive, "r:*") as tar:
        members = {m.name: m for m in tar.getmembers()
                   if m.isfile() and ".." not in Path(m.name).parts}
        def document(path: str):
            member = members.get(path)
            if member is None or member.size > MAX_JSON:
                raise ValueError("missing or oversized descriptor: " + path)
            stream = tar.extractfile(member)
            if stream is None:
                raise ValueError("unreadable descriptor: " + path)
            content = stream.read(MAX_JSON + 1)
            if len(content) > MAX_JSON:
                raise ValueError("oversized descriptor: " + path)
            return json.loads(content)
        index = document("index.json")
        result["index_manifests"] = []
        for entry in index.get("manifests", []):
            oid = entry.get("digest", "")
            rec = {"digest": oid, "platform": entry.get("platform"),
                   "annotations": entry.get("annotations", {})}
            if not isinstance(oid, str) or not oid.startswith("sha256:"):
                raise ValueError("invalid index descriptor digest")
            manifest = document("blobs/sha256/" + oid[7:])
            rec["artifact_type"] = manifest.get("artifactType")
            rec["manifest_subject"] = manifest.get("subject")
            rec["config_type"] = (manifest.get("config") or {}).get("mediaType")
            rec["layers"] = []
            if (entry.get("annotations") or {}).get(
                    "vnd.docker.reference.type") == "attestation-manifest":
                for layer in manifest.get("layers", []):
                    lid = layer.get("digest", "")
                    row = {"digest": lid,
                           "media_type": layer.get("mediaType"),
                           "annotations": layer.get("annotations", {})}
                    if isinstance(lid, str) and lid.startswith("sha256:"):
                        statement = document("blobs/sha256/" + lid[7:])
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
            result["index_manifests"].append(rec)
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
