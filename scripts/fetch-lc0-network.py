#!/usr/bin/env python3
"""Fetch and verify the pinned LC0 network candidate/frozen artifact."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.strength_profile import (
    StrengthProfileError,
    load_json,
    validate_lock,
)

LOCK = ROOT / "qualification" / "lc0-strength.lock.json"

# These bounds apply to one pinned-network acquisition, not arbitrary URLs.
# Exhausted retries are failures, never permission to substitute a network.
TRANSIENT_HTTP_STATUSES = frozenset((408, 429, 500, 502, 503, 504))
MAX_DOWNLOAD_ATTEMPTS = 7
BACKOFF_SECONDS = (5, 10, 20, 40, 80, 120)
MAX_RETRY_AFTER_SECONDS = 180
MAX_TOTAL_SLEEP_SECONDS = 420


def _retry_after_seconds(headers, *, now=None) -> float | None:
    """Parse both RFC Retry-After forms without trusting unlimited delays."""
    value = None if headers is None else headers.get("Retry-After")
    if value is None:
        return None
    value = str(value).strip()
    if value.isascii() and value.isdecimal():
        return float(int(value))
    try:
        expected = parsedate_to_datetime(value)
        if expected.tzinfo is None:
            expected = expected.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc) if now is None else now
        return max(0.0, (expected - now).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _download_with_retry(
    request, tmp: Path, network: dict, *,
    allow_candidate_size: bool,
    opener=None, sleeper=None,
) -> tuple[int, str]:
    """Download exact locked bytes with bounded retry of transient HTTP errors.

    Each attempt opens the same private temporary path in wb mode: a short
    HTTP body from an earlier attempt can never be appended to a later one.
    Non-transient HTTP, invalid pinned hashes/sizes, and exhaustion fail closed.
    """
    opener = urllib.request.urlopen if opener is None else opener
    sleeper = time.sleep if sleeper is None else sleeper
    waited = 0.0
    for index in range(MAX_DOWNLOAD_ATTEMPTS):
        try:
            with opener(request, timeout=120) as response, tmp.open("wb") as out:
                shutil.copyfileobj(response, out, length=1 << 20)
            # The hash check is deliberately outside the HTTP exception
            # handler. Bad bytes are a provenance error, not retryable noise.
            return verify(tmp, network, allow_candidate_size=allow_candidate_size)
        except urllib.error.HTTPError as exc:
            code = int(exc.code)
            if code not in TRANSIENT_HTTP_STATUSES or index >= MAX_DOWNLOAD_ATTEMPTS - 1:
                raise
            requested = _retry_after_seconds(exc.headers)
            if requested is not None and requested > MAX_RETRY_AFTER_SECONDS:
                raise StrengthProfileError(
                    "LC0 pinned network source asked for an excessive Retry-After; "
                    "retry budget exhausted without changing upstream URL"
                ) from exc
            wait = float(BACKOFF_SECONDS[index])
            if requested is not None:
                wait = max(wait, requested)
            if waited + wait > MAX_TOTAL_SLEEP_SECONDS:
                raise StrengthProfileError(
                    "LC0 pinned network download exceeded bounded retry budget"
                ) from exc
            waited += wait
            print(
                f"LC0 upstream HTTP {code}; retry {index + 2}/"
                f"{MAX_DOWNLOAD_ATTEMPTS} after {wait:.0f}s",
                file=sys.stderr, flush=True,
            )
            exc.close()
            sleeper(wait)
    raise StrengthProfileError("LC0 download exhausted all bounded attempts")



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
    if target.is_symlink():
        raise StrengthProfileError("LC0 network cache must not be a symlink")

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
        size, digest = _download_with_retry(
            request, tmp, network,
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
