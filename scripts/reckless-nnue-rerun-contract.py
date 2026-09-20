#!/usr/bin/env python3
"""Prove Cargo re-runs the verified Reckless NNUE resolution path on model changes."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "vendor.lock.json"
HELPER = ROOT / "scripts" / "fetch-reckless-network.py"
RECKLESS = ROOT / "engines" / "reckless"


class ContractError(RuntimeError):
    pass


def load_spec() -> dict[str, object]:
    try:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        return lock["engines"]["reckless"]["artifacts"]["default_nnue"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ContractError(f"cannot read Reckless NNUE lock metadata: {exc}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_helper(env: dict[str, str]) -> Path:
    completed = subprocess.run(
        [sys.executable, str(HELPER)],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise ContractError(
            "verified Reckless NNUE helper failed:\n" + completed.stderr.strip()
        )
    value = completed.stdout.strip()
    if not value:
        raise ContractError("verified Reckless NNUE helper returned no path")
    path = Path(value)
    if not path.is_file():
        raise ContractError(f"verified Reckless NNUE path does not exist: {path}")
    return path


def run_cargo(env: dict[str, str]) -> None:
    completed = subprocess.run(
        ["cargo", "check", "--release", "--no-default-features"],
        cwd=RECKLESS,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        raise ContractError(
            "Reckless cargo check failed during NNUE rerun contract:\n"
            + completed.stdout[-12000:]
        )


def corrupt_same_size(path: Path) -> None:
    size = path.stat().st_size
    if size < 2:
        raise ContractError(f"NNUE file is unexpectedly small: {size}")
    offset = size // 2
    with path.open("r+b") as handle:
        handle.seek(offset)
        original = handle.read(1)
        if len(original) != 1:
            raise ContractError("failed to read corruption byte from NNUE")
        handle.seek(offset)
        handle.write(bytes([original[0] ^ 0x01]))
        handle.flush()
        os.fsync(handle.fileno())
    if path.stat().st_size != size:
        raise ContractError("same-size corruption unexpectedly changed NNUE length")
    os.utime(path, None)


def main() -> int:
    spec = load_spec()
    expected_size = int(spec["size"])
    expected_sha = str(spec["sha256"]).lower()

    env = os.environ.copy()
    env.pop("EVALFILE", None)
    env["PYTHON"] = sys.executable

    path = run_helper(env)
    if path.stat().st_size != expected_size or sha256_file(path).lower() != expected_sha:
        raise ContractError("precondition failed: pinned Reckless NNUE is not verified")

    # Establish Cargo's build-script dependency graph with the resolved model path.
    run_cargo(env)

    # Avoid coarse filesystem timestamp granularity hiding the modification.
    time.sleep(1.1)
    corrupt_same_size(path)

    corrupted_sha = sha256_file(path).lower()
    if corrupted_sha == expected_sha:
        raise ContractError("failed to corrupt Reckless NNUE for rerun test")

    try:
        # This succeeds only if cargo:rerun-if-changed=<resolved model> causes
        # build.rs to run again and the verified helper repairs the cache.
        run_cargo(env)

        actual_size = path.stat().st_size
        actual_sha = sha256_file(path).lower()
        if actual_size != expected_size:
            raise ContractError(
                f"Cargo rerun did not restore pinned NNUE size: "
                f"expected {expected_size}, got {actual_size}"
            )
        if actual_sha != expected_sha:
            raise ContractError(
                "Cargo rerun did not re-verify/restore the pinned Reckless NNUE: "
                f"expected {expected_sha}, got {actual_sha}"
            )
    finally:
        # Never poison later CI stages. Cleanup may repair the cache, but it is
        # deliberately after the assertion above so it cannot mask a rerun bug.
        if not path.is_file() or sha256_file(path).lower() != expected_sha:
            try:
                run_helper(env)
            except ContractError as exc:
                print(f"warning: NNUE cleanup failed: {exc}", file=sys.stderr)

    print(
        "Reckless NNUE Cargo rerun contract passed: same-size corruption "
        "triggered verified restoration"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError, ValueError) as exc:
        print(f"Reckless NNUE rerun contract failure: {exc}", file=sys.stderr)
        raise SystemExit(1)
