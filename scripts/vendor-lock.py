#!/usr/bin/env python3
"""Read and validate the AllfatherChess vendor lock without third-party dependencies."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT / "vendor.lock.json"
EXPECTED_ENGINES = {"stockfish", "reckless", "lc0"}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class LockError(ValueError):
    pass


def load_lock() -> dict:
    try:
        data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LockError(f"missing lockfile: {LOCK_PATH}") from exc
    except json.JSONDecodeError as exc:
        raise LockError(f"invalid JSON in {LOCK_PATH}: {exc}") from exc
    if not isinstance(data, dict):
        raise LockError("lockfile root must be an object")
    return data


def require(condition: bool, message: str) -> None:
    if not condition:
        raise LockError(message)


def validate(data: dict) -> None:
    require(data.get("schema_version") == 2, "schema_version must be 2")
    engines = data.get("engines")
    require(isinstance(engines, dict), "engines must be an object")
    require(set(engines) == EXPECTED_ENGINES, f"engines must be exactly {sorted(EXPECTED_ENGINES)}")

    destinations: set[str] = set()
    for name, entry in engines.items():
        require(isinstance(entry, dict), f"{name}: entry must be an object")
        for field in ("repository", "branch", "commit", "tree", "tracked_entries", "destination"):
            require(field in entry, f"{name}: missing {field}")

        repository = entry["repository"]
        require(isinstance(repository, str) and repository.startswith("https://github.com/") and repository.endswith(".git"),
                f"{name}: repository must be an https GitHub .git URL")
        require(isinstance(entry["branch"], str) and entry["branch"], f"{name}: branch must be non-empty")
        require(isinstance(entry["commit"], str) and HEX40.fullmatch(entry["commit"]) is not None,
                f"{name}: commit must be a lowercase 40-hex SHA")
        require(isinstance(entry["tree"], str) and HEX40.fullmatch(entry["tree"]) is not None,
                f"{name}: tree must be a lowercase 40-hex SHA")
        require(isinstance(entry["tracked_entries"], int) and entry["tracked_entries"] > 0,
                f"{name}: tracked_entries must be a positive integer")
        destination = entry["destination"]
        require(isinstance(destination, str) and destination == f"engines/{name}",
                f"{name}: destination must be engines/{name}")
        require(destination not in destinations, f"{name}: destination is duplicated")
        destinations.add(destination)

        artifacts = entry.get("artifacts", {})
        require(isinstance(artifacts, dict), f"{name}: artifacts must be an object")
        for artifact_name, artifact in artifacts.items():
            require(isinstance(artifact, dict), f"{name}/{artifact_name}: artifact must be an object")
            for field in ("filename", "url", "size", "sha256"):
                require(field in artifact, f"{name}/{artifact_name}: missing {field}")
            require(isinstance(artifact["filename"], str) and "/" not in artifact["filename"] and artifact["filename"],
                    f"{name}/{artifact_name}: filename must be a basename")
            parsed = urlparse(artifact["url"])
            require(parsed.scheme == "https" and parsed.netloc == "github.com",
                    f"{name}/{artifact_name}: url must be an https github.com URL")
            require(isinstance(artifact["size"], int) and artifact["size"] > 0,
                    f"{name}/{artifact_name}: size must be a positive integer")
            require(isinstance(artifact["sha256"], str) and HEX64.fullmatch(artifact["sha256"]) is not None,
                    f"{name}/{artifact_name}: sha256 must be lowercase 64-hex")


def get_engine(data: dict, name: str, field: str):
    engines = data["engines"]
    if name not in engines:
        raise LockError(f"unknown engine: {name}")
    if field not in engines[name]:
        raise LockError(f"{name}: unknown field: {field}")
    return engines[name][field]


def get_artifact(data: dict, engine: str, artifact: str, field: str):
    try:
        item = data["engines"][engine]["artifacts"][artifact]
    except KeyError as exc:
        raise LockError(f"unknown artifact: {engine}/{artifact}") from exc
    if field not in item:
        raise LockError(f"{engine}/{artifact}: unknown field: {field}")
    return item[field]


def emit(value) -> None:
    if isinstance(value, (dict, list)):
        print(json.dumps(value, sort_keys=True))
    else:
        print(value)


def main(argv: list[str]) -> int:
    try:
        data = load_lock()
        validate(data)
        if len(argv) == 1 and argv[0] == "validate":
            print(f"vendor lock OK: schema v{data['schema_version']}")
            return 0
        if len(argv) == 3 and argv[0] == "engine":
            emit(get_engine(data, argv[1], argv[2]))
            return 0
        if len(argv) == 4 and argv[0] == "artifact":
            emit(get_artifact(data, argv[1], argv[2], argv[3]))
            return 0
        raise LockError(
            "usage: vendor-lock.py validate | "
            "vendor-lock.py engine <name> <field> | "
            "vendor-lock.py artifact <engine> <artifact> <field>"
        )
    except LockError as exc:
        print(f"vendor-lock: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
