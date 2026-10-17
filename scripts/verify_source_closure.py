#!/usr/bin/env python3
"""Offline source dependency closure and Git committed-byte verification.

This is independent of producer manifests. AST imports are discovered from
each executable entrypoint; dynamic imports and non-Python build inputs have
explicit roots. Neither a correct SHA string nor a producer-generated hash
establishes equality with Git's committed source bytes.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    "online-recovery-v1": {
        "policy": "qualification/online-recovery-v1.json",
        "field": "attested_source_paths",
        "roots": ("deploy/lichess/run_recovery.py",
                  "scripts/qualify-online-recovery.py",
                  "tests/lichess/fake_lichess_api.py",
                  "scripts/verify_source_closure.py"),
        "inputs": ("deploy/lichess/Dockerfile.offline",
                   ".github/workflows/online-recovery.yml",
                   "qualification/assurance-contract-v1.json"),
    },
    "online-recovery-v2": {
        "policy": "qualification/online-recovery-v2.json",
        "field": "attested_paths",
        "roots": ("deploy/lichess/run_recovery_v2.py",
                  "scripts/qualify-online-recovery-v2.py",
                  # v2 dynamically loads the independent v1 verifier
                  "scripts/qualify-online-recovery.py",
                  "tests/lichess/fake_lichess_api.py",
                  "scripts/verify_source_closure.py"),
        "inputs": ("deploy/lichess/Dockerfile.offline",
                   ".github/workflows/online-recovery-v2.yml",
                   "qualification/assurance-contract-v1.json",
                   "qualification/online-recovery-v1.json"),
    },
}


def require(condition: bool, why: str) -> None:
    if not condition:
        raise ValueError(why)


def imported_sources(path: Path, root: Path) -> set[str]:
    """Visit local imports; no module imports or application code executed."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        candidates = []
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            module = node.module or ""
            candidates.append(module)
            # from deploy.lichess import foo imports executable foo.py
            candidates.extend(module+"."+alias.name for alias in node.names
                              if alias.name != "*")
        for name in candidates:
            if name.startswith(("tests.lichess.", "deploy.lichess.",
                                "scripts.")):
                candidate = name.replace(".", "/") + ".py"
                if (root / candidate).is_file():
                    names.add(candidate)
    return names


def expected_sources(profile: str, root: Path = ROOT) -> set[str]:
    require(profile in PROFILES, "unknown source closure profile")
    setup = PROFILES[profile]
    required = set(setup["roots"]) | set(setup["inputs"])
    queue = list(setup["roots"])
    while queue:
        path = queue.pop()
        require((root / path).is_file(), "source closure import missing: "+path)
        for child in imported_sources(root / path, root):
            if child not in required:
                required.add(child)
                queue.append(child)
    return required


def verify_profile(profile: str, root: Path = ROOT) -> dict:
    require(profile in PROFILES, "unknown source closure profile")
    setup = PROFILES[profile]
    doc = json.loads((root / setup["policy"]).read_text(encoding="utf-8"))
    listed = doc.get(setup["field"])
    require(type(listed) is list and len(listed) == len(set(listed)) and
            all(type(name) is str for name in listed),
            "source closure attestation set is malformed")
    required = expected_sources(profile, root)
    missing = sorted(required - set(listed))
    require(not missing, "unattested executable/source input: " + ",".join(missing))
    return {"profile":profile,"source_closure_verified":True,
            "source_paths_checked":len(listed),
            "reachable_executable_paths":len(required),
            "deployment_authorized":False}


def verify_committed_bytes(source_sha: str, paths: list[str],
                           root: Path = ROOT) -> None:
    """Compare actual loaded files against independently retrieved Git bytes."""
    require(type(source_sha) is str and
            re.fullmatch(r"[0-9a-f]{40}", source_sha) is not None,
            "source Git commit SHA malformed")
    require(type(paths) is list and len(paths) == len(set(paths)),
            "source Git byte input list invalid")
    for name in paths:
        require(type(name) is str and name and
                (root/name).is_file() and not (root/name).is_symlink() and
                (root/name).resolve().is_relative_to(root.resolve()),
                "source Git path invalid/missing/linked")
        git = subprocess.run(
            ["git", "-C", str(root), "show", source_sha+":"+name],
            capture_output=True, timeout=8, check=False)
        require(git.returncode == 0, "committed source absent: "+name)
        require(hashlib.sha256((root/name).read_bytes()).digest() ==
                hashlib.sha256(git.stdout).digest(),
                "executed source differs from Git commit: "+name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=sorted(PROFILES), required=True)
    ap.add_argument("--source-sha", required=False)
    args = ap.parse_args()
    report = verify_profile(args.profile)
    if args.source_sha:
        cfg = PROFILES[args.profile]
        paths = json.loads((ROOT/cfg["policy"]).read_text())[cfg["field"]]
        verify_committed_bytes(args.source_sha, paths)
        report["committed_bytes_verified"] = True
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
