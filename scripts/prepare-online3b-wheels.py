#!/usr/bin/env python3
"""Reacquire 11 exact wheels and rebuild chess twice in a digest-pinned toolchain.

The source hash for chess is NOT its wheel hash. A mismatch refuses to stage
anything as qualified. No public, live or host authorization is issued.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "scripts/verify-release-python-lock.py"
IMAGE_TAG = "allfather/python-wheel-builder:release-2b-1-candidate"


def load_verifier():
    spec = importlib.util.spec_from_file_location("release_2b_python_validator", VERIFIER)
    if spec is None or spec.loader is None:
        raise ValueError("release source verifier not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(argv: list[str], **options) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=True, **options)


def ensure_hash(file: Path, expected: str) -> None:
    actual = hashlib.sha256(file.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"unapproved dependency bytes: {file.name}")


def build_once(*, sdist_dir: Path, output: Path, builder: dict, uid: int, gid: int) -> Path:
    output.mkdir()
    environment = builder["env"]
    args = [
        "docker", "run", "--rm", "--network", "none",
        "--security-opt", "no-new-privileges", "--read-only",
        "--user", f"{uid}:{gid}", "--tmpfs", "/tmp:rw,exec,size=384m,mode=1777",
        "--mount", f"type=bind,source={sdist_dir},target=/input,readonly",
        "--mount", f"type=bind,source={output},target=/output",
        "--workdir", "/tmp", "-e", "HOME=/tmp",
    ]
    for key, value in sorted(environment.items()):
        args.extend(["-e", f"{key}={value}"])
    args.append(IMAGE_TAG)
    args.extend(builder["build_command"])
    run(args, timeout=180)
    built = sorted(output.iterdir())
    if len(built) != 1 or built[0].name != "chess-1.11.2-py3-none-any.whl":
        raise ValueError("unexpected local chess wheel selection")
    if built[0].is_symlink() or not built[0].is_file():
        raise ValueError("invalid local wheel file")
    return built[0]


def prepare(source_lock: Path, wheelhouse: Path) -> dict:
    if sys.version_info[:2] != (3, 12):
        raise ValueError("dependency acquisition requires CPython 3.12")
    verifier = load_verifier()
    selected, builder, pins = verifier.policy(ROOT)
    expected_source = ROOT / selected["committed_resolution_path"]
    if source_lock.resolve() != expected_source.resolve():
        raise ValueError("runtime cannot supply a dynamically generated source lock")
    if wheelhouse.is_symlink():
        raise ValueError("symlinked output wheelhouse forbidden")
    wheelhouse.mkdir(parents=True, exist_ok=True)
    if any(wheelhouse.iterdir()):
        raise ValueError("output wheelhouse must be empty")
    expected_wheels = selected["wheels"]
    expected_downloads = set(expected_wheels) - {"chess-1.11.2-py3-none-any.whl"}
    expected_downloads.add("chess-1.11.2.tar.gz")
    with tempfile.TemporaryDirectory(prefix="release-2b1-") as temporary:
        base = Path(temporary)
        sources = base / "sources"
        sources.mkdir()
        # Hash checking occurs DURING download; selection then checks the exact
        # filename and bytes, so an alternative pip-allowed wheel is rejected.
        run([sys.executable, "-m", "pip", "download",
             "--disable-pip-version-check", "--no-cache-dir", "--no-deps",
             "--no-build-isolation", "--require-hashes", "-r", str(expected_source),
             "-d", str(sources)], cwd=ROOT, timeout=180)
        downloaded = list(sources.iterdir())
        if (len(downloaded) != 12 or {f.name for f in downloaded} != expected_downloads or
                any(not f.is_file() or f.is_symlink() for f in downloaded)):
            raise ValueError("selected source downloads include an alternate/missing file")
        sdist = sources / "chess-1.11.2.tar.gz"
        ensure_hash(sdist, selected["chess_source_archive_sha256"])
        for name in expected_downloads - {sdist.name}:
            ensure_hash(sources / name, expected_wheels[name])

        # The approved code supplies Dockerfile/tool hashes and a real OCI
        # digest. This image is a build tool, not a release/runtime container.
        run(["docker", "build", "--no-cache", "--platform", "linux/amd64",
             "-f", builder["dockerfile_path"], "--tag", IMAGE_TAG, "."],
            cwd=ROOT, timeout=240)
        image_id = run(["docker", "image", "inspect", "-f", "{{.Id}}", IMAGE_TAG],
                       capture_output=True, text=True, timeout=20).stdout.strip()
        if not image_id.startswith("sha256:") or len(image_id) != 71:
            raise ValueError("builder image identity missing")
        uid = os.getuid()
        gid = os.getgid()
        first = build_once(sdist_dir=sources, output=base / "build-a",
                           builder=builder, uid=uid, gid=gid)
        second = build_once(sdist_dir=sources, output=base / "build-b",
                            builder=builder, uid=uid, gid=gid)
        expected_chess = expected_wheels["chess-1.11.2-py3-none-any.whl"]
        ensure_hash(first, expected_chess)
        ensure_hash(second, expected_chess)
        if first.read_bytes() != second.read_bytes():
            raise ValueError("two clean local chess wheel builds diverged")
        for filename in sorted(expected_wheels):
            src = first if filename == first.name else sources / filename
            shutil.copyfile(src, wheelhouse / filename)
            ensure_hash(wheelhouse / filename, expected_wheels[filename])
        (wheelhouse / "runtime-wheels.lock").write_bytes(
            verifier.runtime_lock(ROOT, selected, pins))
        attestation = {
            "schema_version": 1,
            "source_lock_sha256": selected["committed_resolution_sha256"],
            "builder_policy_sha256": selected["wheel_builder_policy_sha256"],
            "builder_image_ref": builder["image_ref"],
            "local_builder_image_id": image_id,
            "trusted_chess_sdist_sha256": selected["chess_source_archive_sha256"],
            "built_chess_wheel_sha256": expected_chess,
            "runtime_lock_sha256": verifier.sha(wheelhouse / "runtime-wheels.lock"),
            "wheels": expected_wheels,
            "wheel_selection_reviewed": False, "public_bot_release": False,
        }
        (wheelhouse / "wheel-attestation.json").write_text(
            json.dumps(attestation, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    verdict = verifier.wheelhouse(ROOT, wheelhouse)
    return {**verdict, "builder_image_id": image_id, "two_local_builds_equal": True}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--source-lock", type=Path,
                   default=ROOT / "deploy/lichess/requirements.release.lock")
    p.add_argument("--wheelhouse", type=Path, required=True)
    args = p.parse_args()
    try:
        result = prepare(args.source_lock, args.wheelhouse)
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired) as error:
        p.exit(2, "RELEASE-2B-1 wheel preparation refused: " +
               type(error).__name__ + ": " + str(error) + "\n")


if __name__ == "__main__":
    main()
