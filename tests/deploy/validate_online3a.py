#!/usr/bin/env python3
"""Independently reconstruct ONLINE-3A offline transcript and archive evidence."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
import tarfile
from pathlib import Path
import chess

MOVE = re.compile(r"^bestmove ([a-h][1-8][a-h][1-8][qrbn]?|0000)(?: ponder [a-h][1-8][a-h][1-8][qrbn]?)?$")

def digest(file):
    h = hashlib.sha256()
    with file.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()

def verify(*, report, stdout, stderr, manifest, archive):
    evidence = json.loads(report.read_text(encoding="utf-8"))
    seal = json.loads(manifest.read_text(encoding="utf-8"))
    if seal.get("release_kind") != "ONLINE-3A-OFFLINE-ONLY":
        raise ValueError("release kind is not offline-only")
    if seal.get("claim_boundary", {}).get("public_bot_release") is not False:
        raise ValueError("offline-only release boundary not retained")
    if seal.get("claim_boundary", {}).get("deployment_host_qualified") is not False:
        raise ValueError("host qualification manufactured")
    if evidence.get("manifest_sha256") != digest(manifest):
        raise ValueError("manifest digest mismatch")
    if evidence.get("passed") is not True or evidence.get("clean_shutdown") is not True:
        raise ValueError("smoke producer did not qualify")
    if evidence.get("regular_file_stdout_refused") is not True:
        raise ValueError("unsupported output transport not rejected")
    if not stderr.is_file():
        raise ValueError("stderr diagnostic file missing")
    diagnostics = stderr.read_text(encoding="utf-8", errors="replace")
    if "Allfather startup failure" in diagnostics or "Traceback" in diagnostics:
        raise ValueError("controller startup diagnostic indicates failure")
    lines = stdout.read_text(encoding="utf-8").splitlines()
    if lines.count("uciok") != 1 or lines.count("readyok") != 2:
        raise ValueError("UCI handshake/readiness count invalid")
    terminals = [line for line in lines if line.startswith("bestmove ")]
    if len(terminals) != 1:
        raise ValueError("missing/duplicate terminal bestmove")
    if any("startup failure" in line.lower() or "traceback" in line.lower() for line in lines):
        raise ValueError("UCI diagnostic failure")
    found = MOVE.fullmatch(terminals[0])
    if found is None or found.group(1) == "0000":
        raise ValueError("terminal bestmove is malformed/null")
    if found.group(1) not in [move.uci() for move in chess.Board().legal_moves]:
        raise ValueError("terminal move illegal from startpos")
    if evidence.get("bestmove") != found.group(1) or evidence.get("legal_startpos_move") is not True:
        raise ValueError("producer move does not match independently parsed transcript")
    expected_paths = {
        row["path"] for part in ("source_files", "bundle_artifacts")
        for row in seal[part]
    }
    expected_paths.add(seal["runtime_config"]["path"])
    expected_paths.add(seal["build_manifest"]["path"])
    expected_paths.add("build/online-release/release-manifest.json")
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        names = [item.name for item in members]
        if len(names) != len(set(names)) or set(names) != expected_paths:
            raise ValueError("archive path membership does not match sealed manifest")
        if any(not item.isfile() for item in members):
            raise ValueError("archive contains nonregular entries")
        if any(item.mode & 0o022 for item in members):
            raise ValueError("archive contains group/world writable entries")
        for item in members:
            if item.name.startswith("build/online-engine-opt-v2/bin/") and not (item.mode & 0o111):
                raise ValueError("engine binary lost executable permissions")
    return {
        "schema_version": 1, "passed": True,
        "independently_legal_bestmove": found.group(1),
        "source_commit": seal["source_commit"],
        "manifest_sha256": digest(manifest),
        "archive_sha256": digest(archive),
        "archive_file_count": len(expected_paths),
        "claim_boundary": seal["claim_boundary"],
    }

def main():
    p = argparse.ArgumentParser()
    for key in ("report", "stdout", "stderr", "manifest", "archive", "result"):
        p.add_argument("--" + key, required=True, type=Path)
    args = p.parse_args()
    try:
        result = verify(report=args.report, stdout=args.stdout,
                        stderr=args.stderr, manifest=args.manifest,
                        archive=args.archive)
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        print("ONLINE-3A independent validator: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
