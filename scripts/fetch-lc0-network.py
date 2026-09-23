#!/usr/bin/env python3
"""Fetch and verify the pinned LC0 network candidate/frozen artifact."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.strength_profile import (
    StrengthProfileError,
    load_json,
    validate_lock,
)

LOCK = ROOT / "qualification" / "lc0-strength.lock.json"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "build" / "artifacts" / "lc0",
    )
    p.add_argument(
        "--allow-candidate-size",
        action="store_true",
        help="permit null expected_size_bytes and print the observed size",
    )
    return p


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(path: Path, network: dict, *, allow_candidate_size: bool) -> tuple[int, str]:
    size = path.stat().st_size
    digest = digest_file(path)
    expected_size = network.get("expected_size_bytes")
    if expected_size is None:
        if not allow_candidate_size:
            raise StrengthProfileError(
                "network size is not frozen; rerun with --allow-candidate-size "
                "to probe the artifact before updating the lock"
            )
    elif size != expected_size:
        raise StrengthProfileError(
            f"network size mismatch: expected {expected_size}, got {size}"
        )
    if digest != network["sha256"]:
        raise StrengthProfileError(
            f"network sha256 mismatch: expected {network['sha256']}, got {digest}; "
            f"observed_size_bytes={size}"
        )
    return size, digest


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    lock = load_json(LOCK)
    validate_lock(lock)
    network = lock["network"]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / network["filename"]

    if target.is_file():
        try:
            size, digest = verify(
                target,
                network,
                allow_candidate_size=args.allow_candidate_size,
            )
            print(f"LC0 network cache verified: {target}")
            print(f"observed_size_bytes={size}")
            print(f"observed_sha256={digest}")
            print(target)
            return 0
        except StrengthProfileError:
            target.unlink()

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{network['filename']}.",
        suffix=".tmp",
        dir=args.output_dir,
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        request = urllib.request.Request(
            network["url"],
            headers={"User-Agent": "AllfatherChess-LC0-qualification/1"},
        )
        with urllib.request.urlopen(request, timeout=120) as response, tmp.open("wb") as out:
            shutil.copyfileobj(response, out)
        size, digest = verify(
            tmp,
            network,
            allow_candidate_size=args.allow_candidate_size,
        )
        tmp.replace(target)
    finally:
        if tmp.exists():
            tmp.unlink()

    print(f"LC0 network verified and installed: {target}")
    print(f"observed_size_bytes={size}")
    print(f"observed_sha256={digest}")
    print(target)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (StrengthProfileError, OSError, urllib.error.URLError) as exc:
        print(f"fetch-lc0-network: {exc}", file=sys.stderr)
        raise SystemExit(1)
