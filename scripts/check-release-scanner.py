#!/usr/bin/env python3
"""Independently check pinned scanner SPDX output, never authorize a release."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import tarfile
from pathlib import Path

MAX_JSON=8*1024*1024
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location(
    "release1_oci_contract", ROOT/"scripts/verify-release-oci.py")
contract=importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)

def require(condition, reason):
    if not condition:
        raise ValueError(reason)

def inspect_scanner_output(archive: Path) -> dict:
    """This scanner-only probe verifies SPDX syntax, not image authority."""
    with tarfile.open(archive,"r:*") as tar:
        members={m.name:m for m in tar.getmembers()
                 if m.isfile() and not m.issym() and not m.islnk()}
        require(len(members)<=256, "scanner probe exceeds blob budget")
        def read(path, oid=None, expected_size=None):
            m=members.get(path)
            require(m is not None and m.size<=MAX_JSON,
                    "scanner probe missing or oversized JSON")
            if expected_size is not None:
                require(type(expected_size) is int and
                        m.size==expected_size,"scanner descriptor byte mismatch")
            stream=tar.extractfile(m)
            require(stream is not None,"scanner archive cannot read JSON")
            raw=stream.read(MAX_JSON+1)
            require(len(raw)<=MAX_JSON,"scanner probe JSON too large")
            if oid is not None:
                require("sha256:"+hashlib.sha256(raw).hexdigest()==oid,
                        "scanner descriptor digest mismatch")
            return json.loads(raw)
        index=read("index.json")
        require(type(index) is dict and
                type(index.get("manifests")) is list,
                "scanner output missing OCI index")
        spdx=[]
        def walk(descriptor,depth=0):
            require(depth<=8 and type(descriptor) is dict,
                    "scanner descriptor nesting invalid")
            oid=descriptor.get("digest")
            require(isinstance(oid,str) and
                    oid.startswith("sha256:") and len(oid)==71 and
                    all(c in "0123456789abcdef" for c in oid[7:]),
                    "scanner OCI digest invalid")
            typ=descriptor.get("mediaType")
            data=read("blobs/sha256/"+oid[7:],oid,descriptor.get("size"))
            if typ=="application/vnd.oci.image.index.v1+json":
                require(type(data.get("manifests")) is list,
                        "scanner image index malformed")
                for child in data["manifests"]:
                    walk(child,depth+1)
                return
            require(typ in ("application/vnd.oci.image.manifest.v1+json",
                            "application/vnd.docker.distribution.manifest.v2+json"),
                    "scanner unknown OCI manifest")
            annotations=descriptor.get("annotations") or {}
            if annotations.get("vnd.docker.reference.type")!="attestation-manifest":
                return
            for layer in data.get("layers",[]):
                require(type(layer) is dict and
                        layer.get("mediaType")=="application/vnd.in-toto+json",
                        "scanner attestation layer format invalid")
                lid=layer.get("digest")
                require(isinstance(lid,str) and lid.startswith("sha256:"),
                        "scanner attestation statement digest malformed")
                statement=read("blobs/sha256/"+lid[7:],lid,layer.get("size"))
                rows=statement.get("statements")
                if rows is None:
                    rows=[statement]
                require(type(rows) is list, "scanner statement envelope invalid")
                for row in rows:
                    if isinstance(row,dict) and row.get("predicateType")==contract.SPDX:
                        require(row.get("_type")==contract.STATEMENT,
                                "scanner in-toto statement invalid")
                        pred=row.get("predicate")
                        require(type(pred) is dict,"scanner SPDX predicate malformed")
                        contract.validate_spdx_document(pred)
                        spdx.append((len(pred.get("packages",[])),
                                     len(pred.get("files",[]))))
        for root in index["manifests"]:
            walk(root)
        require(len(spdx)==1 and sum(spdx[0])>=1,
                "scanner must export exactly one nonempty valid SPDX statement")
        return {"schema_version":1,"scanner_spdx_contract_verified":True,
                "spdx_document_count":len(spdx),
                "spdx_package_count":spdx[0][0],
                "spdx_file_count":spdx[0][1],
                "integrity_verified":False,"release_qualified":False,
                "public_bot_release":False}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--archive",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    args=ap.parse_args()
    report=inspect_scanner_output(args.archive)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps(report,sort_keys=True))


if __name__=="__main__":
    main()
