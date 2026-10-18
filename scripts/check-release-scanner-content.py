#!/usr/bin/env python3
"""Independently verify real file bytes against the pinned Syft SPDX document.

Never accept a schema-shaped SHA1 string or Syft's nonregular-file zero SHA1
sentinel as proof that a regular file was hashed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import tarfile
from pathlib import Path

MAX_BYTES=12*1024*1024
SPDX="https://spdx.dev/Document"
STATEMENT="https://in-toto.io/Statement/v1"
EXPECTED_FILE=frozenset(("./usr/bin/dpkg","/usr/bin/dpkg","usr/bin/dpkg"))


def insist(condition, reason):
    if not condition:
        raise ValueError(reason)


def file_checksum_evidence(document: dict, contents: bytes) -> dict:
    insist(type(document) is dict and type(contents) is bytes and
           0<len(contents)<=MAX_BYTES,
           "scanner known regular file witness missing")
    files=document.get("files")
    insist(type(files) is list, "scanner real SPDX file inventory missing")
    target=[item for item in files if
            type(item) is dict and item.get("fileName") in EXPECTED_FILE]
    insist(len(target)==1,"scanner known package-owned file missing/ambiguous")
    checksums=target[0].get("checksums")
    insist(type(checksums) is list,"scanner known file checksums missing")
    digest_map={}
    for entry in checksums:
        insist(type(entry) is dict and
               type(entry.get("algorithm")) is str and
               type(entry.get("checksumValue")) is str and
               entry["algorithm"] not in digest_map,
               "scanner known file checksum shape malformed")
        digest_map[entry["algorithm"]]=entry["checksumValue"]
    sha1=hashlib.sha1(contents).hexdigest()  # noqa: S324 SPDX requires SHA1
    sha256=hashlib.sha256(contents).hexdigest()
    insist(digest_map.get("SHA1")==sha1 and
           digest_map.get("SHA256")==sha256 and
           sha1!="0"*40,
           "scanner known regular file SHA1/SHA256 content mismatch")
    return {"schema_version":1,"source_file_hashes_verified":True,
            "sha1_verified":True,"sha256_verified":True,
            "public_bot_release":False,
            "artifact_distribution_authorized":False}


def actual_spdx_document(archive: Path) -> dict:
    with tarfile.open(archive,"r:*") as tar:
        members={m.name:m for m in tar.getmembers() if m.isfile()}
        def raw_json(key: str, descriptor:dict|None=None):
            m=members.get(key)
            insist(m is not None and 0<m.size<=MAX_BYTES,
                   "scanner OCI evidence object missing")
            f=tar.extractfile(m)
            insist(f is not None,"scanner OCI evidence unreadable")
            data=f.read(MAX_BYTES+1)
            insist(len(data)==m.size and len(data)<=MAX_BYTES,
                   "scanner OCI evidence truncated")
            if descriptor is not None:
                insist(descriptor.get("size")==len(data) and
                       descriptor.get("digest")==
                       "sha256:"+hashlib.sha256(data).hexdigest(),
                       "scanner OCI evidence digest/size mismatch")
            return json.loads(data)
        root=raw_json("index.json")
        found=[]
        def walk(d:dict,depth:int=0):
            insist(type(d) is dict and depth<=8,
                   "scanner OCI graph invalid")
            digest=d.get("digest")
            insist(type(digest) is str and digest.startswith("sha256:") and
                   len(digest)==71, "scanner OCI descriptor invalid")
            data=raw_json("blobs/sha256/"+digest[7:],d)
            if d.get("mediaType")=="application/vnd.oci.image.index.v1+json":
                insist(type(data.get("manifests")) is list,
                       "scanner OCI nested index malformed")
                for child in data["manifests"]:
                    walk(child,depth+1)
                return
            if ((d.get("annotations") or {}).get(
                    "vnd.docker.reference.type")!="attestation-manifest"):
                return
            for layer in data.get("layers",[]):
                insist(type(layer) is dict and
                       layer.get("mediaType")=="application/vnd.in-toto+json",
                       "scanner attestation layer invalid")
                lid=layer.get("digest")
                insist(type(lid) is str and lid.startswith("sha256:") and
                       len(lid)==71,"scanner statement descriptor invalid")
                stmt=raw_json("blobs/sha256/"+lid[7:],layer)
                records=stmt["statements"] if "statements" in stmt else [stmt]
                insist(type(records) is list and bool(records),
                       "scanner statement envelope invalid")
                for item in records:
                    insist(type(item) is dict,"scanner statement row invalid")
                    if item.get("predicateType")==SPDX:
                        insist(item.get("_type")==STATEMENT and
                               type(item.get("predicate")) is dict,
                               "scanner SPDX statement invalid")
                        found.append(item["predicate"])
        for item in root["manifests"]:
            walk(item)
        insist(len(found)==1,"scanner SPDX document missing or duplicate")
        return found[0]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--archive",type=Path,required=True)
    parser.add_argument("--reference-file",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    try:
        insist(args.reference_file.is_file() and
               not args.reference_file.is_symlink() and
               args.reference_file.stat().st_size<=MAX_BYTES,
               "scanner reference file invalid")
        reference=args.reference_file.read_bytes()
        report=file_checksum_evidence(actual_spdx_document(args.archive),
                                      reference)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
        print(json.dumps(report,sort_keys=True))
    except (ValueError,KeyError,OSError,tarfile.TarError,TypeError) as exc:
        parser.exit(2,"scanner real file hash refused: "+type(exc).__name__+"\n")


if __name__=="__main__":
    main()
