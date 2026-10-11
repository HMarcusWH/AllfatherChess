#!/usr/bin/env python3
"""ONLINE-3A: source-bound, artifact-verified offline release manifest.

This manifest does NOT qualify generic-host portability, online services, J12
capacity, playing strength, or any runtime authority beyond the sealed config.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = "build/online-engine-opt-v2"
BUILD_MANIFEST = BUNDLE + "/build-manifest.json"
RUNTIME = "config/allfather.online-hybrid-v2.validation.json"
DEFAULT_OUTPUT = "build/online-release/release-manifest.json"
SOURCE_DIRS = ("controller/", "common/", "adapters/", "config/", "qualification/")
EXTRA_SOURCES = ("deploy/bin/allfather-online", "scripts/release-manifest.py",
                 "scripts/online-host-preflight.py", "scripts/package-online.py",
                 "LICENSES.md", "vendor.lock.json")


class SealError(ValueError):
    pass


def require(value, message):
    if not value:
        raise SealError(message)


def safe(root: Path, name: str) -> Path:
    require(isinstance(name, str) and bool(name), "empty release path")
    part = PurePosixPath(name)
    require(not part.is_absolute() and ".." not in part.parts
            and str(part) == name and "\\" not in name,
            "noncanonical release path: " + name)
    path = root / name
    require(path.resolve().is_relative_to(root.resolve()),
            "release path escapes root: " + name)
    require(path.is_file() and not path.is_symlink(), "missing or linked release file: " + name)
    return path


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def info(root: Path, name: str) -> dict:
    p = safe(root, name)
    return {"path": name, "size": p.stat().st_size, "sha256": sha(p)}


def load(root: Path, name: str) -> dict:
    value = json.loads(safe(root, name).read_text(encoding="utf-8"))
    require(isinstance(value, dict), "expected JSON object: " + name)
    return value


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args],
                                   text=True, stderr=subprocess.PIPE).strip()


def tracked_sources(root: Path) -> list[str]:
    raw = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "-z", "--", *SOURCE_DIRS, *EXTRA_SOURCES]
    )
    names = sorted(set(x.decode("utf-8") for x in raw.split(b"\0") if x))
    for required in EXTRA_SOURCES:
        require(required in names, "source file not tracked: " + required)
    require(any(x.startswith("controller/") for x in names), "controller sources absent")
    return names


def sealed_bundle(root: Path) -> tuple[dict, list[dict]]:
    build = load(root, BUILD_MANIFEST)
    require(build.get("profile_id") == "online-engine-opt-v2", "unexpected engine bundle profile")
    require(build.get("source_commit") and build.get("source_tree"), "bundle missing source identity")
    artifacts = build.get("artifacts")
    require(isinstance(artifacts, dict), "bundle artifacts missing")
    entries = []
    for group in ("engines", "networks"):
        records = artifacts.get(group)
        require(isinstance(records, dict) and set(records) == {"stockfish", "reckless", "lc0"},
                "bundle must contain all three " + group)
        for owner, row in sorted(records.items()):
            require(isinstance(row, dict), "invalid bundle row")
            declared = row.get("path")
            require(isinstance(declared, str), "bundle artifact path missing")
            item = info(root, BUNDLE + "/" + declared)
            require(row.get("sha256") == item["sha256"] and
                    row.get("size") == item["size"], "bundle artifact mismatch: " + owner)
            entries.append(item)
    return build, entries


def check_runtime(root: Path, artifact_entries: list[dict]) -> None:
    runtime = load(root, RUNTIME)
    require(runtime.get("mode") == "active" and runtime.get("anchor") == "stockfish-anchor"
            and runtime.get("root") == "..", "HYBRID runtime identity drift")
    require((runtime.get("online_time") or {}).get("enabled") is True,
            "online deadline policy must be enabled")
    require((runtime.get("hybrid_authority") or {}).get("enabled") is True,
            "HYBRID authority not enabled")
    require((runtime.get("shadow") or {}).get("replay_root") ==
            "build/replays-online-hybrid-v2", "unexpected replay location")
    names = {x["path"] for x in artifact_entries}
    instances = runtime.get("instances") or {}
    require(set(instances) == {"stockfish-anchor", "stockfish-shadow",
                               "reckless-shadow", "lc0-shadow"},
            "unexpected engine topology")
    for instance, data in instances.items():
        binary = data.get("binary")
        require(binary in names, "engine binary outside sealed bundle: " + instance)
    lc0 = instances["lc0-shadow"]
    require((lc0.get("options") or {}).get("WeightsFile") in names,
            "LC0 weights not sealed")
    require((lc0.get("options") or {}).get("Backend") == "blas",
            "non-BLAS LC0 backend")


def create(root: Path, output: Path) -> dict:
    require((root / ".git").exists(), "release creation requires exact Git checkout")
    source_commit = git(root, "rev-parse", "HEAD")
    source_tree = git(root, "rev-parse", "HEAD^{tree}")
    require(not git(root, "status", "--porcelain", "--untracked-files=no"),
            "dirty tracked source: refuse release")
    build, artifacts = sealed_bundle(root)
    require(build["source_commit"] == source_commit and
            build["source_tree"] == source_tree,
            "engine bundle belongs to different source")
    check_runtime(root, artifacts)
    source_files = [info(root, path) for path in tracked_sources(root)]
    record = {
        "schema_version": 1,
        "release_kind": "ONLINE-3A-OFFLINE-ONLY",
        "source_commit": source_commit,
        "source_tree": source_tree,
        "runtime_config": info(root, RUNTIME),
        "build_manifest": info(root, BUILD_MANIFEST),
        "source_files": source_files,
        "bundle_artifacts": sorted(artifacts, key=lambda x: x["path"]),
        "claim_boundary": {
            "uci_launcher": True,
            "verified_bundle_bytes": True,
            "deployment_host_qualified": False,
            "network_recovery_qualified": False,
            "public_bot_release": False,
            "strength_or_elo": False,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    require(output.resolve().is_relative_to(root.resolve()), "manifest output outside root")
    tmp = output.with_name(output.name + ".tmp")
    tmp.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, output)
    verify(root, output)
    return record


def verify(root: Path, manifest: Path) -> dict:
    require(manifest.is_file() and not manifest.is_symlink(), "release manifest missing or linked")
    record = json.loads(manifest.read_text(encoding="utf-8"))
    require(isinstance(record, dict) and record.get("schema_version") == 1
            and record.get("release_kind") == "ONLINE-3A-OFFLINE-ONLY",
            "unsupported release manifest")
    boundary = record.get("claim_boundary") or {}
    require(boundary == {
        "uci_launcher": True, "verified_bundle_bytes": True,
        "deployment_host_qualified": False, "network_recovery_qualified": False,
        "public_bot_release": False, "strength_or_elo": False,
    }, "release claim boundary was altered")
    require(record.get("source_commit") and record.get("source_tree"),
            "release source identity missing")
    for category in ("source_files", "bundle_artifacts"):
        rows = record.get(category)
        require(isinstance(rows, list) and bool(rows), "release " + category + " empty")
        paths = [row.get("path") for row in rows]
        require(paths == sorted(set(paths)), "duplicate or unsorted release paths")
        for row in rows:
            require(row == info(root, row["path"]), "tampered release file: " + row["path"])
    runtime = record.get("runtime_config")
    build_entry = record.get("build_manifest")
    require(runtime == info(root, RUNTIME) and
            build_entry == info(root, BUILD_MANIFEST), "runtime/build identity mismatch")
    build, actual = sealed_bundle(root)
    require(build["source_commit"] == record["source_commit"] and
            build["source_tree"] == record["source_tree"],
            "bundle/release source mismatch")
    require(sorted(actual, key=lambda x: x["path"]) == record["bundle_artifacts"],
            "incomplete release engine bundle")
    check_runtime(root, actual)
    if (root / ".git").exists():
        require(git(root, "rev-parse", "HEAD") == record["source_commit"] and
                git(root, "rev-parse", "HEAD^{tree}") == record["source_tree"],
                "checkout changed since release seal")
    require("deploy/bin/allfather-online" in {x["path"] for x in record["source_files"]},
            "launcher missing from seal")
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["create", "verify"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path, default=Path(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    dest = args.manifest if args.manifest.is_absolute() else root / args.manifest
    try:
        record = create(root, dest) if args.command == "create" else verify(root, dest)
    except (SealError, OSError, subprocess.CalledProcessError, ValueError, KeyError) as exc:
        print("ONLINE-3A seal rejected: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"status": "SEALED" if args.command == "create" else "VERIFIED",
                      "source_commit": record["source_commit"],
                      "release_kind": record["release_kind"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
