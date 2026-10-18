#!/usr/bin/env python3
"""Observe genuine RELEASE-1 OS/wheel dependency bytes without approving locks.

Run from an independently checked-out, exact-commit source worktree after the
installed-image, staged-wheel and full OCI qualifications. This report is
candidate measurement only, never distribution, host or live-play authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT="scripts/observe-release-dependencies.py"
POLICY="qualification/release-image-v1.json"
SHA256=re.compile(r"[0-9a-f]{64}\Z")
COMMIT=re.compile(r"[0-9a-f]{40}\Z")
OS_NAME=re.compile(r"[a-z0-9][a-z0-9+.-]*(?::amd64)?\Z")
DEBIAN_VERSION=re.compile(r"[0-9][A-Za-z0-9.+:~_-]*\Z")
PY_NAME=re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
PY_VERSION=re.compile(r"[0-9][A-Za-z0-9.!+_-]*\Z")
WHEEL_NAME=re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]{2,199}\.whl\Z")
MAX_INVENTORY=1024*1024
MAX_WHEEL=128*1024*1024
MAX_WHEEL_TOTAL=512*1024*1024


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path, *, max_bytes: int=MAX_WHEEL) -> str:
    require(path.is_file() and not path.is_symlink() and
            0 <= path.stat().st_size <= max_bytes,
            "dependency evidence missing, linked, or too large")
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def read_bounded(path: Path, *, max_bytes: int=MAX_INVENTORY) -> bytes:
    require(path.is_file() and not path.is_symlink() and
            0 < path.stat().st_size <= max_bytes,
            "dependency evidence not a bounded regular file")
    data=path.read_bytes()
    require(len(data)<=max_bytes and len(data)>0,
            "dependency evidence size changed during read")
    return data


def inventory_lines(raw: bytes, *, minimum: int, maximum: int) -> list[str]:
    require(raw.endswith(b"\n") and b"\r" not in raw and b"\x00" not in raw,
            "dependency inventory must be complete canonical LF text")
    try:
        rows=raw.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("non-ASCII dependency inventory") from exc
    require(minimum<=len(rows)<=maximum and
            all(row and len(row)<=260 and
                all(32<=ord(ch)<=126 for ch in row) for row in rows),
            "dependency inventory count or record malformed")
    return rows


def parse_os_inventory(raw: bytes, required: list[str], *,
                       minimum: int=40) -> int:
    rows=inventory_lines(raw,minimum=minimum,maximum=4000)
    require(rows==sorted(rows), "OS package inventory is not byte-sorted")
    packages=set()
    for row in rows:
        require(row.count("=")==1, "OS package identity is not name=version")
        name,version=row.split("=",1)
        require(OS_NAME.fullmatch(name) is not None and
                DEBIAN_VERSION.fullmatch(version) is not None and
                name not in packages,
                "OS package duplicate, nonnative, or invalid version")
        packages.add(name)
    require(type(required) is list and bool(required) and
            all(type(name) is str for name in required),
            "required OS dependency policy malformed")
    base_names={name.split(":",1)[0] for name in packages}
    require(set(required)<=base_names, "required native OS dependencies absent")
    return len(rows)


def parse_python_inventory(raw: bytes, *, minimum: int=12) -> int:
    rows=inventory_lines(raw,minimum=minimum,maximum=256)
    normalized=[]
    for row in rows:
        require(row.count("==")==1, "Python inventory not a pinned distribution")
        name,version=row.split("==",1)
        require(PY_NAME.fullmatch(name) is not None and
                PY_VERSION.fullmatch(version) is not None,
                "Python inventory has unpinned/unsafe requirement")
        normalized.append(re.sub(r"[-_.]+","-",name).casefold())
    require(normalized==sorted(normalized) and
            len(normalized)==len(set(normalized)),
            "Python inventory is unsorted or has duplicate packages")
    require({"chess","requests","pyyaml"} <= set(normalized),
            "essential installed bridge distributions missing")
    return len(rows)


def inspect_wheelhouse(stage: Path, source_lock: Path,
                      manifest: dict) -> dict:
    folder=stage/"wheels"
    require(folder.is_dir() and not folder.is_symlink(),
            "staged wheelhouse directory invalid")
    attestation=folder/"wheel-attestation.json"
    lock=folder/"runtime-wheels.lock"
    witness=json.loads(read_bounded(attestation))
    require(type(witness) is dict and witness.get("schema_version")==1,
            "staged wheel attestation schema invalid")
    require(digest_file(attestation,max_bytes=MAX_INVENTORY)==
            manifest.get("wheel_attestation_sha256"),
            "wheel attestation does not match release manifest")
    runtime_hash=digest_file(lock,max_bytes=MAX_INVENTORY)
    require(runtime_hash==witness.get("runtime_lock_sha256")==
            manifest.get("runtime_wheel_lock_sha256"),
            "runtime wheel lock identity mismatch")
    source_hash=digest_file(source_lock,max_bytes=MAX_INVENTORY)
    require(source_hash==witness.get("source_lock_sha256"),
            "resolved source requirement lock drift")
    listed=witness.get("wheels")
    require(type(listed) is dict and len(listed)==12,
            "wheel inventory must contain exactly 12 entries")
    disk={p.name:p for p in folder.glob("*.whl")}
    require(set(disk)==set(listed), "staged wheel files differ from attestation")
    total=0
    for name,hexdigest in sorted(listed.items()):
        require(type(name) is str and WHEEL_NAME.fullmatch(name) is not None
                and "/" not in name and "\\" not in name and
                type(hexdigest) is str and SHA256.fullmatch(hexdigest),
                "unsafe or unpinned wheel inventory entry")
        item=disk[name]
        require(item.is_file() and not item.is_symlink() and
                0<item.stat().st_size<=MAX_WHEEL and
                item.resolve().parent==folder.resolve(),
                "wheel missing, linked, or outside stage")
        total+=item.stat().st_size
        require(total<=MAX_WHEEL_TOTAL and
                digest_file(item)==hexdigest,
                "wheel bytes mismatch attested inventory")
    return {"wheel_count":len(listed),
            "wheel_total_bytes":total,
            "wheel_attestation_sha256":digest_file(attestation),
            "runtime_lock_sha256":runtime_hash,
            "source_lock_sha256":source_hash}


def validate_release_policy(policy: dict) -> None:
    require(type(policy) is dict and
            policy.get("schema_version")==1 and
            policy.get("scope")=="offline-image-candidate-only" and
            policy.get("platform")=="linux/amd64" and
            type(policy.get("base_image")) is str and
            re.fullmatch(r"docker.io/library/ubuntu@sha256:[0-9a-f]{64}",
                         policy["base_image"]) is not None and
            type(policy.get("apt_snapshot")) is str and
            re.fullmatch(r"[0-9]{8}T[0-9]{6}Z",policy["apt_snapshot"]) is not None,
            "release dependency identity policy invalid")
    for key in ("allow_real_credentials","public_bot_release",
                "deployment_host_qualified","network_recovery_qualified",
                "os_package_lock_complete","wheel_lock_complete"):
        require(policy.get(key) is False,
                "observation cannot execute against elevated release policy")
    for key in ("exact_os_package_inventory_sha256",
                "exact_runtime_wheel_lock_sha256",
                "exact_runtime_wheel_inventory_sha256"):
        require(policy.get(key) is None,
                "observation must not treat preapproved dependency pins as candidates")


def git_identity(root: Path) -> tuple[str,str]:
    commit=subprocess.check_output(
        ["git","-C",str(root),"rev-parse","HEAD"],text=True).strip()
    tree=subprocess.check_output(
        ["git","-C",str(root),"rev-parse","HEAD^{tree}"],text=True).strip()
    require(COMMIT.fullmatch(commit) and COMMIT.fullmatch(tree),
            "invalid exact-source Git identity")
    return commit,tree


def observe(*, stage: Path, os_inventory: Path, python_inventory: Path,
            source_lock: Path, source_sha: str, root: Path=ROOT) -> dict:
    commit,tree=git_identity(root)
    require(commit==source_sha and COMMIT.fullmatch(source_sha),
            "dependency observer did not execute from exact Git head")
    policy_file=root/POLICY
    policy=json.loads(read_bounded(policy_file))
    validate_release_policy(policy)
    require(stage.is_dir() and not stage.is_symlink(),
            "invalid candidate stage directory")
    manifest=json.loads(read_bounded(stage/"manifest.json",max_bytes=4*1024*1024))
    require(type(manifest) is dict and
            manifest.get("source_commit")==commit and
            manifest.get("source_tree")==tree and
            manifest.get("release_image_policy_sha256")==digest_file(policy_file),
            "release candidate differs from source/policy identity")
    attested=manifest.get("source_attestation")
    require(type(attested) is dict and
            attested.get(SCRIPT)==digest_file(root/SCRIPT),
            "dependency observer executable not staged and source attested")
    wheels=inspect_wheelhouse(stage,source_lock,manifest)
    os_raw=read_bounded(os_inventory)
    py_raw=read_bounded(python_inventory)
    os_count=parse_os_inventory(os_raw,policy["required_native_packages"])
    py_count=parse_python_inventory(py_raw)
    return {
        "schema_version":1,
        "scope":"offline-dependency-candidate-measurement-only",
        "source_commit":commit,
        "source_tree":tree,
        "base_image":policy["base_image"],
        "apt_snapshot":policy["apt_snapshot"],
        "release_policy_sha256":digest_file(policy_file),
        "stage_manifest_sha256":digest_file(stage/"manifest.json",max_bytes=4*1024*1024),
        "os_inventory_sha256":digest_bytes(os_raw),
        "os_package_count":os_count,
        "python_inventory_sha256":digest_bytes(py_raw),
        "python_distribution_count":py_count,
        **wheels,
        "candidate_integrity_observed":True,
        "dependency_lock_approved":False,
        "cross_run_reproducibility_qualified":False,
        "offline_candidate_qualified":False,
        "artifact_distribution_authorized":False,
        "public_bot_release":False,
        "release_blocked":True,
    }


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument("--stage",type=Path,required=True)
    p.add_argument("--os-inventory",type=Path,required=True)
    p.add_argument("--python-inventory",type=Path,required=True)
    p.add_argument("--source-lock",type=Path,required=True)
    p.add_argument("--source-sha",required=True)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    try:
        report=observe(stage=args.stage,os_inventory=args.os_inventory,
                       python_inventory=args.python_inventory,
                       source_lock=args.source_lock,source_sha=args.source_sha)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
        print(json.dumps(report,sort_keys=True))
    except (OSError,ValueError,KeyError,TypeError,
            UnicodeDecodeError,subprocess.SubprocessError) as exc:
        p.exit(2,"RELEASE-2A dependency observation refused: "+
               type(exc).__name__+"\n")


if __name__=="__main__":
    main()
