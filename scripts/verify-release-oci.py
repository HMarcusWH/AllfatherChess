#!/usr/bin/env python3
"""Independently inspect a LOCAL-ONLY OCI-layout tar from BuildKit.

Never publish or upload this tar while combined-distribution clearance is
outstanding. Distinguish OCI manifest, config and archive SHA256 identities.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path

def need(ok, why):
    if not ok:
        raise ValueError(why)

def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def file_digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1<<20),b""):
            h.update(block)
    return h.hexdigest()

def inspect(archive: Path, *, source_commit: str) -> dict:
    need(re.fullmatch(r"[0-9a-f]{40}",source_commit) is not None,
         "missing exact source reference")
    if not archive.is_file():
        raise ValueError("offline OCI archive absent")
    with tarfile.open(archive,"r:*") as tar:
        names=set(tar.getnames())
        need("index.json" in names and "oci-layout" in names,
             "OCI layout index or layout metadata missing")
        need(not any(p.startswith("/") or ".." in Path(p).parts
                     for p in names), "unsafe OCI archive member")
        def raw(name: str) -> bytes:
            need(name in names,"OCI blob missing: "+name)
            data=tar.extractfile(name)
            need(data is not None, "OCI member unreadable")
            return data.read()
        def blob(desc:dict)->bytes:
            digest=desc.get("digest","")
            need(re.fullmatch(r"sha256:[0-9a-f]{64}",digest) is not None,
                 "OCI descriptor not SHA256-bound")
            value=raw("blobs/sha256/"+digest.split(":")[1])
            need(sha256_bytes(value)==digest.split(":")[1],
                 "OCI descriptor byte identity failed")
            return value
        layout=json.loads(raw("oci-layout"))
        need(layout.get("imageLayoutVersion")=="1.0.0", "OCI layout unsupported")
        index=json.loads(raw("index.json"))
        manifests=index.get("manifests",[])
        need(isinstance(manifests,list) and manifests,"OCI index lacks manifests")
        platform=[x for x in manifests if x.get("platform",{}).get("os")=="linux"
                  and x.get("platform",{}).get("architecture")=="amd64"]
        need(len(platform)==1, "OCI image not exactly one linux/amd64 manifest")
        image=platform[0]
        image_record=json.loads(blob(image))
        config_ref=image_record.get("config",{})
        config=json.loads(blob(config_ref))
        runtime=config.get("config",{})
        need(runtime.get("User")=="10001:10001", "OCI user is not nonroot UID 10001")
        labels=runtime.get("Labels") or {}
        need(labels.get("org.opencontainers.image.revision")==source_commit,
             "OCI image revision label does not match exact Git source")
        entrypoint=runtime.get("Entrypoint")
        need(entrypoint==["/opt/online3b/bin/python","/opt/release/entrypoint.py"],
             "OCI entrypoint is not the offline-only gate")
        need(runtime.get("Cmd")==["verify"], "OCI defaults to live API operation")
        env=runtime.get("Env") or []
        need(not any("LICHESS_BOT_TOKEN=" in value or
                     "RELEASE1_OFFLINE_ISOLATED=1" in value for value in env),
             "OCI embeds live credentials or auto-enables game execution")
        layers=image_record.get("layers",[])
        need(isinstance(layers,list) and layers,"OCI image has no layers")
        for desc in layers: blob(desc)
        # Index-attached attestation manifests MUST actually hold in-toto
        # statements covering both SPDX/SBOM and build provenance.
        attestations=[]
        predicate_types=[]
        for entry in manifests:
            if entry is image:continue
            annotations=entry.get("annotations") or {}
            if annotations.get("vnd.docker.reference.type")!="attestation-manifest":
                continue
            record=json.loads(blob(entry))
            for layer in record.get("layers",[]):
                value=blob(layer)
                try:
                    statement=json.loads(value)
                except ValueError:
                    continue
                kind=statement.get("predicateType","")
                if kind:
                    predicate_types.append(kind)
            attestations.append(entry.get("digest"))
        sbom=any("spdx" in x.lower() for x in predicate_types)
        provenance=any("slsa" in x.lower() for x in predicate_types)
        need(attestations and sbom and provenance,
             "OCI archive lacks retained SPDX SBOM and SLSA provenance attestations")
        return {
            "schema_version":1,
            "profile_id":"release-1-oci-inspection-v1",
            "source_commit":source_commit,
            "oci_archive_sha256":file_digest(archive),
            "oci_index_sha256":sha256_bytes(raw("index.json")),
            "oci_manifest_digest":image["digest"],
            "oci_config_digest":config_ref["digest"],
            "oci_layer_count":len(layers),
            "oci_attestation_manifests":attestations,
            "attestation_predicate_types":sorted(set(predicate_types)),
            "sbom_attestation_present":True,
            "provenance_attestation_present":True,
            "offline_artifact_verified":True,
            "public_distribution_authorized":False,
            "destination_host_qualified":False,
            "public_bot_release":False,
        }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--archive",required=True,type=Path)
    p.add_argument("--source-sha",required=True)
    p.add_argument("--output",required=True,type=Path)
    a=p.parse_args()
    try:
        report=inspect(a.archive,source_commit=a.source_sha)
    except (OSError,ValueError,TypeError,KeyError,tarfile.TarError) as e:
        p.exit(2,"RELEASE-1 OCI verification refused: "+str(e)+"\n")
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,sort_keys=True))

if __name__=="__main__":
    main()
