#!/usr/bin/env python3
"""Prepare a source-locked offline scanner through a disposable Docker registry.

This transports exact scanner bytes between the host Docker daemon and a
docker-container BuildKit daemon. No image is pushed to a public registry.
A candidate manifest is NEVER its own release approval.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen

_source_path=Path(__file__).with_name("prepare-release-scanner.py")
_spec=importlib.util.spec_from_file_location("release1_scanner_source",_source_path)
if _spec is None or _spec.loader is None:
    raise ValueError("scanner source verifier unavailable")
_source=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_source)
ROOT, LOCK, prepare, require=(_source.ROOT,_source.LOCK,
                              _source.prepare,_source.require)

REGISTRY_HOST="127.0.0.1:5000"
REGISTRY_BUILDKIT="scanner-registry:5000"
NAME="allfather/release-scanner"
MEDIA_TYPES=("application/vnd.oci.image.manifest.v1+json",
             "application/vnd.docker.distribution.manifest.v2+json")
OUT=ROOT/"build/test-results/release1-scanner"


def call(args: list[str], *, timeout: int=900) -> str:
    result=subprocess.run(args,check=False,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,timeout=timeout)
    require(result.returncode==0,
            "scanner Docker/registry operation refused: "+args[0])
    return result.stdout.decode("utf-8","replace").strip()


def docker_names() -> tuple[str,str]:
    run=os.environ.get("GITHUB_RUN_ID","local")
    attempt=os.environ.get("GITHUB_RUN_ATTEMPT","1")
    require(run.isdigit() or run=="local","scanner GitHub run identity malformed")
    require(attempt.isdigit(),"scanner GitHub attempt identity malformed")
    return "release1-scanner-"+run+"-"+attempt, "release1-scanner-registry-"+run


def builder_name() -> str:
    mode=os.environ.get("RELEASE1_SCANNER_BUILDER","release1-scanner-probe")
    require(mode in ("release1-scanner-probe","release1-builder"),
            "unapproved scanner builder name")
    return mode


def cleanup() -> None:
    network,registry=docker_names()
    for args in (["docker","buildx","rm","--force",builder_name()],
                 ["docker","rm","-f",registry],
                 ["docker","network","rm",network]):
        try:
            subprocess.run(args,stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,timeout=35,check=False)
        except (OSError,subprocess.TimeoutExpired):
            pass


def response_digest(tag: str) -> str:
    url="http://"+REGISTRY_HOST+"/v2/"+NAME+"/manifests/"+tag
    req=Request(url,headers={"Accept":", ".join(MEDIA_TYPES)})
    with urlopen(req,timeout=12) as response:
        media=response.headers.get("Content-Type","").split(";",1)[0]
        raw=response.read(8*1024*1024+1)
        header=response.headers.get("Docker-Content-Digest")
    require(media in MEDIA_TYPES and len(raw)<=8*1024*1024,
            "local scanner manifest content type or size invalid")
    digest="sha256:"+hashlib.sha256(raw).hexdigest()
    require(header==digest and
            json.loads(raw).get("schemaVersion")==2,
            "local scanner manifest hash, header or schema mismatch")
    return digest


def setup() -> dict:
    require(os.environ.get("SOURCE_SHA") and
            os.environ.get("GITHUB_ENV"),
            "scanner setup is restricted to exact-head GitHub CI")
    require(call(["git","rev-parse","HEAD"])==
            os.environ["SOURCE_SHA"],
            "scanner setup checkout differs from exact requested SHA")
    policy=json.loads(LOCK.read_text(encoding="utf-8"))
    require(policy.get("registry_transport_image")==
            "docker.io/library/registry@sha256:"
            "a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373",
            "scanner registry transport image is not pinned")
    OUT.mkdir(parents=True,exist_ok=True)
    source=prepare(output=OUT)
    network,registry=docker_names()
    try:
        call(["docker","network","create","--driver","bridge",network],timeout=90)
        call(["docker","run","-d","--name",registry,"--network",network,
              "--network-alias","scanner-registry",
              "-p","127.0.0.1:5000:5000",
              policy["registry_transport_image"]],timeout=90)
        for attempt in range(25):
            try:
                req=Request("http://127.0.0.1:5000/v2/")
                with urlopen(req,timeout=3) as r:
                    if r.status==200:
                        break
            except (OSError,TimeoutError):
                time.sleep(1)
        else:
            raise ValueError("isolated scanner registry readiness failed")
        hashes=[]
        for index in (1,2):
            image=REGISTRY_HOST+"/"+NAME+":candidate-"+str(index)
            call(["docker","buildx","build","--builder","default",
                  "--platform","linux/amd64","--no-cache","--pull=false",
                  "--provenance=false","--sbom=false","--load",
                  "--build-arg","SOURCE_DATE_EPOCH="+str(source["source_date_epoch"]),
                  "-f",str(ROOT/"deploy/release/scanner/Dockerfile"),
                  "-t",image,str(OUT)],timeout=360)
            call(["docker","push",image],timeout=240)
            hashes.append(response_digest("candidate-"+str(index)))
        require(hashes[0]==hashes[1],
                "scanner clean image rebuild changed OCI manifest bytes")
        digest=hashes[0]
        config=OUT/"buildkitd.scanner.toml"
        config.write_text(
            '[registry."scanner-registry:5000"]\n  http = true\n',
            encoding="utf-8")
        call(["docker","buildx","create","--driver","docker-container",
              "--driver-opt","network="+network,"--name",builder_name(),
              "--buildkitd-config",str(config),"--use"],timeout=90)
        call(["docker","buildx","inspect","--builder",builder_name(),
              "--bootstrap"],timeout=180)
        ref=REGISTRY_BUILDKIT+"/"+NAME+"@"+digest
        # The verifier always addresses the exact exported manifest, never
        # a tag that a later producer could repoint.
        env=Path(os.environ["GITHUB_ENV"])
        with env.open("a",encoding="utf-8") as fd:
            fd.write("RELEASE_SCANNER_REF="+ref+"\n")
            fd.write("RELEASE_SCANNER_DIGEST="+digest+"\n")
        record={
            "schema_version":1,
            "upstream_commit":source["upstream_commit"],
            "binary_sha256":source["binary_sha256"],
            "manifest_digest":digest,
            "image_reproducible":True,
            "binary_reproducible":True,
            "scanner_reference_kind":"ephemeral-local-registry-manifest",
            "public_bot_release":False,
            "artifact_distribution_authorized":False,
            "approved_identity_matched":(
                policy.get("approved_binary_sha256")==source["binary_sha256"]
                and policy.get("approved_manifest_digest")==digest),
        }
        (OUT/"scanner-candidate.json").write_text(
            json.dumps(record,indent=2,sort_keys=True)+"\n",encoding="utf-8")
        print(json.dumps(record,sort_keys=True))
        return record
    except BaseException:
        cleanup()
        raise


def verify_approval() -> None:
    policy=json.loads(LOCK.read_text(encoding="utf-8"))
    candidate=json.loads((OUT/"scanner-candidate.json").read_text("utf-8"))
    require(candidate.get("image_reproducible") is True and
            candidate.get("binary_reproducible") is True,
            "scanner candidate reproducibility not proven")
    require(candidate.get("upstream_commit")==policy.get("upstream_commit"),
            "scanner candidate source identity mismatch")
    # Only a separate committed reviewed lock can grant scanner identity.
    require(type(policy.get("approved_binary_sha256")) is str and
            type(policy.get("approved_manifest_digest")) is str and
            policy["approved_binary_sha256"]==candidate.get("binary_sha256") and
            policy["approved_manifest_digest"]==candidate.get("manifest_digest"),
            "scanner candidate digest not yet frozen/approved")
    print(json.dumps({
        "scanner_identity_approved":True,
        "scanner_manifest_digest":candidate["manifest_digest"],
        "source_locked":True,
        "public_bot_release":False,
        "artifact_distribution_authorized":False},sort_keys=True))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("action",choices=("setup","verify-approval","cleanup"))
    args=ap.parse_args()
    if args.action=="cleanup":
        cleanup()
        return
    try:
        if args.action=="setup":
            setup()
        else:
            verify_approval()
    except (OSError,ValueError,subprocess.SubprocessError,TimeoutError,
            KeyError,TypeError) as error:
        ap.exit(2,"scanner CI candidate refused: "+type(error).__name__+
                " ("+str(error)[:160]+")\n")


if __name__=="__main__":
    main()
