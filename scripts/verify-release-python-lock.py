#!/usr/bin/env python3
"""RELEASE-2B-1 source/selection verifier: zero network, no policy promotion.

Do not use a producer's PASS field as independent proof. This verifier checks
the source-controlled input closure, selected bytes and bounded wheel metadata.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import zipfile
from email import policy as email_policy
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SELECTED = "qualification/release-python-wheels-v1.json"
BUILDER = "qualification/release-python-builder-v1.json"
HEX = re.compile(r"[0-9a-f]{64}\Z")
PIN = re.compile(r"([a-z0-9][a-z0-9-]*)==([A-Za-z0-9.]+) \\\Z")
HASH_LINE = re.compile(r"    --hash=sha256:([0-9a-f]{64})\Z")
REQ = re.compile(r"([A-Za-z0-9_.-]+)(==|~=)([0-9][0-9.]*)\Z")
MAX_JSON = 128 * 1024
MAX_LOCK = 256 * 1024
MAX_METADATA = 128 * 1024
MAX_WHEEL = 40 * 1024 * 1024


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def sha(path: Path) -> str:
    require(path.is_file() and not path.is_symlink(), "missing/linked input: " + str(path))
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def bounded(path: Path, limit: int) -> bytes:
    require(path.is_file() and not path.is_symlink(), "missing/linked input: " + str(path))
    require(0 < path.stat().st_size <= limit, "oversized/empty input: " + str(path))
    return path.read_bytes()


def load(path: Path) -> dict:
    obj = json.loads(bounded(path, MAX_JSON))
    require(type(obj) is dict, "expected JSON object")
    return obj


def parse_lock(path: Path) -> dict[str, tuple[str, str]]:
    raw = bounded(path, MAX_LOCK).decode("ascii")
    lines = [s for s in raw.splitlines() if s and not s.startswith("#")]
    require(len(lines) == 24, "source lock must contain twelve exact pins and hashes")
    pins = {}
    order = []
    for i in range(0, len(lines), 2):
        head = PIN.fullmatch(lines[i])
        digest = HASH_LINE.fullmatch(lines[i + 1])
        require(head is not None and digest is not None, "unrecognized hash-lock syntax")
        name, version = head.groups()
        require(name == norm(name) and name not in pins, "noncanonical/duplicate source package")
        require(bool(HEX.fullmatch(digest.group(1))), "invalid source archive hash")
        pins[name] = (version, digest.group(1))
        order.append(name)
    require(order == sorted(order), "source pins must be sorted")
    return pins


def compatible(pinned: str, op: str, declared: str) -> bool:
    if op == "==":
        return pinned == declared
    # PEP 440 compatible releases: ~=X.Y => >=X.Y,<X+1
    try:
        v = tuple(int(v) for v in pinned.split("."))
        lo = tuple(int(v) for v in declared.split("."))
    except ValueError:
        return False
    if len(lo) < 2:
        return False
    hi = lo[:-1]
    upper = hi[:-1] + (hi[-1] + 1,)
    width = max(len(v), len(lo), len(upper))
    v, lo, upper = (x + (0,) * (width - len(x)) for x in (v, lo, upper))
    return lo <= v < upper


def parse_direct(path: Path) -> dict[str, tuple[str, str]]:
    raw = bounded(path, MAX_LOCK).decode("utf-8")
    result = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = REQ.fullmatch(line)
        require(match is not None, "unsupported top-level requirement")
        name, op, version = match.groups()
        name = norm(name)
        require(name not in result, "duplicate top-level requirement")
        result[name] = (op, version)
    require(result and "chess" in result and "requests" in result,
            "essential top-level requirements absent")
    return result


def wheel_name_parts(filename: str) -> tuple[str, str]:
    require(filename.endswith(".whl"), "non-wheel in selection")
    bits = filename[:-4].split("-")
    require(len(bits) == 5, "invalid selected wheel filename")
    require(bits[-3] in ("cp312", "py3") and bits[-2] in ("cp312", "none"),
            "unexpected Python ABI")
    require(bits[-1] == "any" or
            ("manylinux2014_x86_64" in bits[-1] and "manylinux_2_17_x86_64" in bits[-1]),
            "unexpected wheel platform")
    return norm(bits[0]), bits[1]


def policy(root: Path = ROOT) -> tuple[dict, dict, dict]:
    root = root.resolve()
    selected = load(root / SELECTED)
    builder = load(root / BUILDER)
    require(selected.get("schema_version") == 1 and
            selected.get("scope") == "release-2b-python-wheel-candidate" and
            selected.get("platform") == "linux/amd64" and
            selected.get("python_abi") == "cp312", "wrong selected-wheel scope")
    require(selected.get("public_bot_release") is False and
            selected.get("artifact_distribution_authorized") is False and
            selected.get("wheel_selection_reviewed") is False,
            "candidate policy illegally claims release approval")
    require(selected.get("source_requirements_path") == "deploy/lichess/requirements.in" and
            selected.get("committed_resolution_path") == "deploy/lichess/requirements.release.lock" and
            selected.get("wheel_builder_policy") == BUILDER, "policy input path mismatch")
    for path_key, hash_key in (
        ("source_requirements_path", "source_requirements_sha256"),
        ("committed_resolution_path", "committed_resolution_sha256"),
        ("wheel_builder_policy", "wheel_builder_policy_sha256"),
    ):
        digest = selected.get(hash_key)
        require(type(digest) is str and bool(HEX.fullmatch(digest)) and
                digest == sha(root / selected[path_key]),
                "approved input-closure hash mismatch: " + path_key)
    require(builder.get("schema_version") == 1 and
            builder.get("scope") == "release-2b-1-candidate-not-approved" and
            builder.get("platform") == "linux/amd64" and
            builder.get("python_abi") == "cp312" and
            builder.get("expected_python_version") == "3.12.14" and
            builder.get("builder_reviewed") is False and
            builder.get("external_attestation") is False and
            builder.get("public_bot_release") is False,
            "builder schema/approval invalid")
    image = builder.get("image_ref")
    require(isinstance(image, str) and
            re.fullmatch(r"docker\.io/library/python@sha256:[0-9a-f]{64}", image) is not None,
            "builder image must have an actual pinned OCI digest")
    dockerpath = builder.get("dockerfile_path")
    tools = builder.get("build_tools_path")
    require(dockerpath == "deploy/release/Dockerfile.wheel-builder" and
            tools == "deploy/release/python-wheel-build-tools.lock", "unexpected builder inputs")
    require(builder.get("dockerfile_sha256") == sha(root / dockerpath) and
            builder.get("build_tools_sha256") == sha(root / tools),
            "builder toolchain file drift")
    docker = bounded(root / dockerpath, MAX_LOCK).decode("utf-8")
    require("FROM --platform=linux/amd64 " + image in docker and
            "SOURCE_DATE_EPOCH=" not in docker and
            " --require-hashes " in docker and " --no-deps " in docker,
            "builder is not pinned/hash-installed")
    tools_raw = bounded(root / tools, MAX_LOCK).decode("utf-8")
    for tool, version in builder.get("tool_versions", {}).items():
        require(tool + "==" + version + " \\" in tools_raw,
                "builder tool version missing from download lock")
    require(builder.get("tool_versions") == {"pip": "24.0", "setuptools": "68.1.2", "wheel": "0.42.0"},
            "unreviewed build-tool version")
    require(builder.get("build_command") ==
            ["python", "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
             "--wheel-dir", "/output", "/input/chess-1.11.2.tar.gz"],
            "unapproved local wheel construction")
    require(builder.get("env") == {
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
        "PYTHONHASHSEED": "0",
    }, "builder environment drift")

    source_pins = parse_lock(root / selected["committed_resolution_path"])
    direct = parse_direct(root / selected["source_requirements_path"])
    for name, (op, requested) in direct.items():
        require(name in source_pins and compatible(source_pins[name][0], op, requested),
                "top-level requirements not reconciled with frozen source resolution: " + name)
    wheel_map = selected.get("wheels")
    require(type(wheel_map) is dict and len(wheel_map) == 12 and
            selected.get("expected_wheel_count") == 12,
            "selected policy must include exactly twelve wheels")
    chosen = {}
    for filename, hexhash in wheel_map.items():
        require(type(hexhash) is str and HEX.fullmatch(hexhash) is not None,
                "invalid selected wheel SHA-256")
        name, version = wheel_name_parts(filename)
        require(name not in chosen and name in source_pins and
                source_pins[name][0] == version, "wheel selection version/name drift")
        if name != "chess":
            require(source_pins[name][1] == hexhash,
                    "selected wheel does not match committed download hash: " + name)
        chosen[name] = (version, filename, hexhash)
    require(set(chosen) == set(source_pins), "source resolution/wheel package closure differs")
    require(selected.get("chess_source_archive_sha256") == source_pins["chess"][1] ==
            "a8b43e5678fdb3000695bdaa573117ad683761e5ca38e591c4826eba6d25bb39",
            "chess source hash is not source-locked")
    require(selected.get("installer_exception") == {
        "distribution": "pip", "exact_version": "24.0",
        "origin": "Ubuntu24.04 venv bootstrap; OS/package provenance pending RELEASE-2B-2",
    }, "unapproved installer exception")
    return selected, builder, source_pins


def runtime_lock(root: Path, selected: dict, pins: dict) -> bytes:
    raw = bounded(root / selected["committed_resolution_path"], MAX_LOCK)
    source = ("chess==" + pins["chess"][0] + " \\\n    --hash=sha256:" +
              selected["chess_source_archive_sha256"]).encode()
    built = ("chess==" + pins["chess"][0] + " \\\n    --hash=sha256:" +
             selected["wheels"]["chess-1.11.2-py3-none-any.whl"]).encode()
    require(raw.count(source) == 1, "chess source lock has missing/ambiguous entry")
    return raw.replace(source, built)


def check_zip(path: Path, filename: str, name: str, version: str) -> None:
    raw_dist = filename.split("-")[0]
    member = raw_dist + "-" + version + ".dist-info/METADATA"
    require(path.stat().st_size <= MAX_WHEEL, "wheel exceeds declared size bound")
    try:
        with zipfile.ZipFile(path) as z:
            members = z.infolist()
            require(0 < len(members) <= 4096 and
                    len({i.filename for i in members}) == len(members),
                    "duplicate/excessive wheel members")
            for entry in members:
                p = PurePosixPath(entry.filename)
                require(not p.is_absolute() and ".." not in p.parts and
                        "\\" not in entry.filename and not stat.S_ISLNK(entry.external_attr >> 16),
                        "unsafe wheel member")
            hits = [x for x in members if x.filename.endswith(".dist-info/METADATA")]
            require(len(hits) == 1 and hits[0].filename == member and
                    hits[0].file_size <= MAX_METADATA,
                    "wheel METADATA location invalid")
            header = BytesParser(policy=email_policy.default).parsebytes(z.read(hits[0]))
            require(norm(header.get("Name", "")) == name and
                    header.get("Version") == version, "wheel filename/METADATA mismatch")
            wheelmeta = raw_dist + "-" + version + ".dist-info/WHEEL"
            require(wheelmeta in z.namelist(), "wheel WHEEL metadata missing")
            tags = z.read(wheelmeta).decode("utf-8", "strict")
            py_tag, abi_tag, platform_tags = filename[:-4].split("-")[-3:]
            expanded = {
                "Tag: " + py_tag + "-" + abi_tag + "-" + platform_tag
                for platform_tag in platform_tags.split(".")
            }
            require(any(tag in tags for tag in expanded),
                    "wheel ABI/platform tag disagrees with filename")
    except (zipfile.BadZipFile, RuntimeError, EOFError, UnicodeDecodeError) as exc:
        raise ValueError("invalid selected wheel archive") from exc


def wheelhouse(root: Path, directory: Path, inventory: Path | None = None) -> dict:
    selected, builder, pins = policy(root)
    require(directory.is_dir() and not directory.is_symlink(), "wheelhouse path invalid")
    expected = set(selected["wheels"]) | {"runtime-wheels.lock", "wheel-attestation.json"}
    actual = list(directory.iterdir())
    require({p.name for p in actual} == expected and len(actual) == len(expected) and
            all(p.is_file() and not p.is_symlink() for p in actual),
            "wheelhouse unexpected file, directory or link")
    for filename, expected_digest in sorted(selected["wheels"].items()):
        path = directory / filename
        require(sha(path) == expected_digest, "wheel selected bytes differ: " + filename)
        name, version = wheel_name_parts(filename)
        check_zip(path, filename, name, version)
    require(bounded(directory / "runtime-wheels.lock", MAX_LOCK) ==
            runtime_lock(root, selected, pins), "installed runtime lock does not select exact wheel bytes")
    att = load(directory / "wheel-attestation.json")
    require(att.get("schema_version") == 1 and
            att.get("source_lock_sha256") == selected["committed_resolution_sha256"] and
            att.get("wheels") == selected["wheels"] and
            att.get("runtime_lock_sha256") == sha(directory / "runtime-wheels.lock") and
            att.get("trusted_chess_sdist_sha256") == selected["chess_source_archive_sha256"] and
            att.get("built_chess_wheel_sha256") ==
            selected["wheels"]["chess-1.11.2-py3-none-any.whl"] and
            att.get("builder_policy_sha256") == selected["wheel_builder_policy_sha256"],
            "wheel attestation/input-closure mismatch")
    if inventory is not None:
        rows = bounded(inventory, MAX_LOCK).decode("utf-8").splitlines()
        order = [norm(row.partition("==")[0]) for row in rows]
        require(order == sorted(set(order)) and len(rows) == 13,
                "installed Python distribution closure invalid")
        recorded = {}
        for row in rows:
            m = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9.]+)", row)
            require(m is not None, "malformed installed dist")
            key = norm(m.group(1))
            require(key not in recorded, "duplicate installed dist")
            recorded[key] = m.group(2)
        for filename in selected["wheels"]:
            name, version = wheel_name_parts(filename)
            require(recorded.get(name) == version, "installed wheel version drift")
        require(recorded.get("pip") == "24.0" and len(recorded) == 13,
                "unapproved installer distribution/version")
    return {
        "schema_version": 1, "verdict": "PASS",
        "scope": "candidate-integrity-only", "selected_wheel_count": 12,
        "source_lock_sha256": selected["committed_resolution_sha256"],
        "wheel_selection_reviewed": False, "public_bot_release": False,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--wheelhouse", type=Path)
    p.add_argument("--python-inventory", type=Path)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    try:
        if args.wheelhouse is None:
            selected, _builder, pins = policy(args.root)
            result = {"verdict": "PASS", "scope": "source-policy-only",
                      "source_packages": len(pins), "selected_wheel_count": 12,
                      "wheel_selection_reviewed": False, "public_bot_release": False}
        else:
            result = wheelhouse(args.root, args.wheelhouse, args.python_inventory)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        p.exit(2, "RELEASE-2B-1 Python lock verification refused: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
