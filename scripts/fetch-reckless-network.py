#!/usr/bin/env python3
"""Fetch and verify the pinned Reckless NNUE artifact portably.

This is the canonical Allfather path used by engine-local source builds when
EVALFILE is not supplied. It relies only on the Python standard library.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "vendor.lock.json"
ENGINE = "reckless"
ARTIFACT = "default_nnue"


class FetchError(RuntimeError):
    pass


def load_spec() -> dict[str, object]:
    try:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        spec = lock["engines"][ENGINE]["artifacts"][ARTIFACT]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise FetchError(f"cannot read pinned Reckless NNUE metadata from {LOCK}: {exc}") from exc

    required = ("filename", "url", "size", "sha256")
    missing = [key for key in required if key not in spec]
    if missing:
        raise FetchError(f"Reckless NNUE lock entry is missing fields: {missing}")

    if not isinstance(spec["filename"], str) or not spec["filename"]:
        raise FetchError("Reckless NNUE filename must be a non-empty string")
    if not isinstance(spec["url"], str) or not spec["url"]:
        raise FetchError("Reckless NNUE URL must be a non-empty string")
    if isinstance(spec["size"], bool) or not isinstance(spec["size"], int) or spec["size"] <= 0:
        raise FetchError("Reckless NNUE size must be a positive integer")
    if (
        not isinstance(spec["sha256"], str)
        or len(spec["sha256"]) != 64
        or any(ch not in "0123456789abcdefABCDEF" for ch in spec["sha256"])
    ):
        raise FetchError("Reckless NNUE SHA-256 must be a 64-character hex string")

    return spec


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: Path, *, expected_size: int, expected_sha256: str) -> tuple[bool, str]:
    if not path.is_file():
        return False, "file does not exist"
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        return False, f"size mismatch: expected {expected_size}, got {actual_size}"
    actual_sha = sha256_file(path)
    if actual_sha.lower() != expected_sha256.lower():
        return False, f"sha256 mismatch: expected {expected_sha256}, got {actual_sha}"
    return True, "ok"


def download(url: str, target: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "AllfatherChess-Reckless-NNUE/1"},
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as out:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            return
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last_error = exc
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            if attempt < 3:
                time.sleep(attempt)
    raise FetchError(f"failed to download pinned Reckless NNUE after 3 attempts: {last_error}")


def main() -> int:
    spec = load_spec()
    filename = str(spec["filename"])
    url = str(spec["url"])
    expected_size = int(spec["size"])
    expected_sha = str(spec["sha256"]).lower()

    artifact_dir = Path(
        os.environ.get(
            "ALLFATHER_ARTIFACT_DIR",
            str(ROOT / "build" / "artifacts" / ENGINE),
        )
    ).expanduser()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    target = artifact_dir / filename

    ok, reason = verify_file(
        target,
        expected_size=expected_size,
        expected_sha256=expected_sha,
    )
    if ok:
        print(f"Reckless NNUE cache verified: {filename}", file=sys.stderr)
        print(str(target.resolve()))
        return 0

    if target.exists():
        print(
            f"Cached Reckless NNUE failed verification ({reason}); replacing it.",
            file=sys.stderr,
        )
        target.unlink()

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{filename}.tmp.",
        dir=str(artifact_dir),
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        print(f"Downloading pinned Reckless NNUE: {filename}", file=sys.stderr)
        download(url, tmp)

        ok, reason = verify_file(
            tmp,
            expected_size=expected_size,
            expected_sha256=expected_sha,
        )
        if not ok:
            raise FetchError(f"Reckless NNUE verification failed: {reason}")

        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass

    print(f"Reckless NNUE verified and installed: {target}", file=sys.stderr)
    print(str(target.resolve()))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FetchError as exc:
        print(f"Reckless NNUE fetch failure: {exc}", file=sys.stderr)
        raise SystemExit(1)
