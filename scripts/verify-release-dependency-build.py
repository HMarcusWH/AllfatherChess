#!/usr/bin/env python3
"""RELEASE-2B-2 independent native dependency-stage observation.

This source-controlled checker is intentionally not a release approval. It
reconstructs three exact native package sets and matches downloaded .deb
hashes to real APT candidate records after gpgv-checked snapshot selection.
Two independent jobs/review of acquisition provenance remain future gates.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DIGEST=re.compile(r"[a-f0-9]{64}\Z")
PACKAGE=re.compile(r"[a-z0-9][a-z0-9+.-]*\Z")
VERSION=re.compile(r"[0-9][0-9A-Za-z.+:~_-]*\Z")
MAX_FILE=1024*1024


def require(ok: bool, why: str):
    if not ok:
        raise ValueError(why)


def bounded(path: Path, maximum: int=MAX_FILE) -> bytes:
    require(path.is_file() and not path.is_symlink() and
            0 < path.stat().st_size <= maximum, "absent/linked/oversized native evidence: "+str(path))
    return path.read_bytes()


def hashbytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def jsonfile(path: Path) -> dict:
    obj=json.loads(bounded(path))
    require(type(obj) is dict, "invalid native policy document")
    return obj


def parse_inventory(raw: bytes) -> dict[str,str]:
    require(raw.endswith(b"\n") and b"\r" not in raw and b"\x00" not in raw,
            "noncanonical native inventory file")
    rows=raw.decode("ascii").splitlines()
    require(rows==sorted(set(rows)) and 25<=len(rows)<=300,
            "native inventory not sorted/unique")
    out={}
    for row in rows:
        require(row.count("=")==1, "malformed native inventory record")
        package,version=row.split("=",1)
        require(PACKAGE.fullmatch(package) and VERSION.fullmatch(version),
                "invalid native package/version")
        out[package]=version
    return out


def exact_inventory(artifact: Path, expected: list[str], label: str) -> dict[str,str]:
    raw=bounded(artifact)
    require(raw==("\n".join(expected)+"\n").encode("ascii"),
            label+" diverges from source-controlled exact native package list")
    return parse_inventory(raw)


def rows_from_delta(before: dict[str,str], after: dict[str,str]) -> dict[str,str]:
    require(set(before).issubset(after), "native build removed pinned-base packages")
    return {k:v for k,v in after.items() if before.get(k)!=v}


def ledger(directory: Path, filename: str, evidence_subdir: str,
           delta: dict[str,str], *, expected_hashes: dict[str,str]|None=None) -> dict:
    raw=bounded(directory/filename).decode("ascii")
    entries=raw.splitlines()
    require(entries==sorted(set(entries)) and len(entries)==len(delta),
            "downloaded native package set differs from installed delta")
    indexed=directory/evidence_subdir
    require(indexed.is_dir() and not indexed.is_symlink(), "package metadata evidence missing")
    require({x.name for x in indexed.iterdir()}=={n+".txt" for n in delta} and
            all(x.is_file() and not x.is_symlink() for x in indexed.iterdir()),
            "native package metadata witnesses incomplete")
    result={}
    for entry in entries:
        cols=entry.split("\t")
        require(len(cols)==5, "downloaded native .deb ledger requires 5 fields")
        name,version,architecture,sha,filename=cols
        require(name not in result and delta.get(name)==version and
                architecture in ("amd64","all") and DIGEST.fullmatch(sha) and
                re.fullmatch(r"[A-Za-z0-9_.+%:~-]+\.deb",filename),
                "downloaded native package version/hash format mismatch")
        record=bounded(indexed/(name+".txt")).decode("utf-8")
        for needle in ("Package: "+name+"\n","Version: "+version+"\n",
                       "Architecture: "+architecture+"\n","SHA256: "+sha+"\n"):
            require(needle in record, "downloaded package hash not in signed APT snapshot cache")
        if expected_hashes is not None and name in expected_hashes:
            require(sha==expected_hashes[name], "native acquired .deb SHA differs from source candidate")
        result[name]={"version":version,"architecture":architecture,
                      "sha256":sha,"filename":filename}
    return result


def verify(*,root:Path=ROOT,evidence:Path,os_inventory:Path,
           py_inventory:Path|None=None,wheelhouse:Path|None=None) -> dict:
    root=root.resolve()
    require(evidence.is_dir() and not evidence.is_symlink(), "missing native evidence dir")
    native=jsonfile(root/"qualification/release-os-packages.lock")
    profile=jsonfile(root/"qualification/release-native-build-v1.json")
    bootstrap=jsonfile(root/"qualification/release-bootstrap-v1.json")
    release=jsonfile(root/"qualification/release-image-v1.json")
    require(profile.get("schema_version")==native.get("schema_version")==1 and
            profile.get("lock_approved") is False and
            native.get("approved") is False and
            release.get("os_package_lock_complete") is False and
            release.get("wheel_lock_complete") is False and
            release.get("public_bot_release") is False and
            release.get("allow_real_credentials") is False,
            "native policy attempted forbidden dependency/public approval")
    require(native.get("base_image")==release["base_image"]==
            profile.get("base_image") and
            native.get("apt_snapshot")==profile.get("apt_snapshot")==
            release.get("apt_snapshot")=="20261001T000000Z", "native base/snapshot identity drift")
    b=exact_inventory(evidence/"base-packages.txt",native["base_inventory"],"base")
    x=exact_inventory(evidence/"bootstrap-packages.txt",
                      native["observed_bootstrap_inventory"],"bootstrap")
    f=exact_inventory(os_inventory,native["expected_final_inventory"],"final")
    require(len(b)==92 and len(x)==94 and len(f)==120 and
            hashbytes(bounded(os_inventory))==native["expected_final_inventory_sha256"],
            "native exact final inventory/hash differs")
    ca_delta=rows_from_delta(b,x)
    runtime_delta=rows_from_delta(x,f)
    require(ca_delta=={"ca-certificates":"20260601~24.04.1",
                       "openssl":"3.0.13-0ubuntu3.16",
                       "libssl3t64":"3.0.13-0ubuntu3.16"},
            "observed CA bootstrap acquired/updated unexpected native packages")
    # Repeated signature/transport inputs are compared against source-recorded
    # candidates; the checker does not independently validate raw InRelease
    # signatures (that is done by gpgv inside the image).
    require(bounded(evidence/"keyring.sha256").decode("ascii").strip()==
            bootstrap["candidate_ca"]["trust_keyring_sha256"] and
            bounded(evidence/"transport-ca.sha256").decode("ascii").strip()==
            bootstrap["experimental_tls_transport_ca"]["expected_source_file_sha256"] and
            bounded(evidence/"ca-deb.sha256").decode("ascii").strip()==
            bootstrap["candidate_ca"]["deb_sha256"],
            "bootstrap keyring, transport certificate or CA bytes differ")
    bootstrap_candidates=native.get("bootstrap_acquired_debs")
    require(type(bootstrap_candidates) is list and len(bootstrap_candidates)==3,
            "measured bootstrap .deb hashes absent from source candidate")
    actual_boot=ledger(evidence,"bootstrap-debs.tsv","bootstrap-index-records",
                       ca_delta,expected_hashes={
                           r["name"]:r["sha256"] for r in bootstrap_candidates})
    require(actual_boot=={
        r["name"]: {k:r[k] for k in ("version","architecture","sha256","filename")}
        for r in bootstrap_candidates},
        "bootstrap selected .deb identity differs from exact candidate lock")
    # Runtime-dependency manifest may start as a candidate observation: even
    # then it MUST bind each downloaded .deb to signed-Apt-selected metadata.
    candidates=native.get("acquired_package_debs")
    must_equal=None
    if candidates is not None:
        require(type(candidates) is list, "native acquired package policy not a list")
        must_equal={r["name"]:r["sha256"] for r in candidates}
    actual_runtime=ledger(evidence,"runtime-debs.tsv","runtime-index-records",
                          runtime_delta,expected_hashes=must_equal)
    require(candidates is not None and actual_runtime=={
        r["name"]: {k:r[k] for k in ("version","architecture","sha256","filename")}
        for r in candidates},
        "runtime acquired package/version/architecture SHA differs from source candidate")
    declared=bounded(evidence/"source-ubuntu-declared.sources").decode("ascii")
    require(declared==bounded(root/"deploy/release/ubuntu.sources").decode("ascii"),
            "runtime signed source declaration drift")
    effective=bounded(evidence/"source-ubuntu-effective.sources").decode("ascii")
    fixed="https://snapshot.ubuntu.com/ubuntu/20261001T000000Z"
    require(effective.count("URIs: "+fixed)==2 and
            "Snapshot:" not in effective and
            "URIs: https://archive.ubuntu.com" not in effective and
            "URIs: https://security.ubuntu.com" not in effective and
            effective.count("Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg")==2,
            "effective APT source not exclusively the dated Ubuntu snapshot")
    source_rows=bounded(evidence/"runtime-direct-selections.txt").decode("ascii")
    require(source_rows==bounded(root/"qualification/release-os-apt-selections-v1.txt").decode("ascii"),
            "runtime explicitly pinned native selections differ")
    index_raw=bounded(evidence/"runtime-inrelease-sha256.txt").decode("ascii")
    index_rows=index_raw.splitlines()
    require(len(index_rows)==4 and index_rows==sorted(set(index_rows)) and
            all(re.fullmatch(r"[a-f0-9]{64}  snapshot\.ubuntu\.com_ubuntu_20261001T000000Z_[A-Za-z0-9_.-]+_InRelease",x)
                for x in index_rows),
            "runtime signed snapshot indexes incomplete or floating")
    # The signed snapshot source identity will be reviewed separately.
    # Candidate index hashes must be measured and then matched against a
    # source-controlled lock before 2B-2's native hash gate is complete.
    signed=native.get("signed_index_evidence")
    if signed is not None:
        require(type(signed) is dict and
                signed.get("runtime_inrelease_sha256_lines")==index_rows and
                signed.get("bootstrap_inrelease_sha256_lines")==
                bounded(evidence/"inrelease-sha256.txt").decode("ascii").splitlines(),
                "signed snapshot index hash identities differ from reviewed candidate")
    # Positive candidate wheel checks are delegated to the independent v2B-1
    # checker rather than trusting a producer's manifest.
    wheel_status="NOT_RUN"
    if wheelhouse is not None or py_inventory is not None:
        require(wheelhouse is not None and py_inventory is not None, "partial wheel qualification")
        spec=importlib.util.spec_from_file_location("release2b1_python",
               root/"scripts/verify-release-python-lock.py")
        require(spec is not None and spec.loader is not None, "missing v2B-1 verifier")
        m=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.wheelhouse(root,wheelhouse,py_inventory)
        wheel_status="PASS"
    return {
        "schema_version":1,"verdict":"PASS","scope":"release-2b-2-native-candidate-only",
        "base_package_count":len(b),"bootstrap_package_count":len(x),
        "final_package_count":len(f),
        "native_inventory_sha256":hashbytes(bounded(os_inventory)),
        "bootstrap_acquired_debs":actual_boot,"runtime_acquired_debs":actual_runtime,
        "signed_snapshot_gpgv_executed_in_build":True,
        "independent_signature_attestation":False,
        "source_native_hash_lock_complete":candidates is not None and signed is not None,
        "runtime_signed_index_hashes":index_rows,
        "bootstrap_signed_index_hashes":bounded(
            evidence/"inrelease-sha256.txt").decode("ascii").splitlines(),
        "python_selected_wheel_verdict":wheel_status,
        "dependency_lock_approved":False,
        "artifact_distribution_authorized":False,
        "public_bot_release":False,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--root",type=Path,default=ROOT)
    p.add_argument("--evidence-dir",type=Path,required=True)
    p.add_argument("--os-inventory",type=Path,required=True)
    p.add_argument("--python-inventory",type=Path)
    p.add_argument("--wheelhouse",type=Path)
    p.add_argument("--output",type=Path)
    args=p.parse_args()
    try:
        result=verify(root=args.root,evidence=args.evidence_dir,
                      os_inventory=args.os_inventory,
                      py_inventory=args.python_inventory,
                      wheelhouse=args.wheelhouse)
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
        print(json.dumps(result,sort_keys=True))
    except (OSError,ValueError,KeyError,TypeError,UnicodeError) as exc:
        p.exit(2,"RELEASE-2B-2 native dependency stage rejected: "+
               type(exc).__name__+": "+str(exc)+"\n")


if __name__=="__main__":
    main()
