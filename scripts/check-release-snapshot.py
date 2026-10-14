#!/usr/bin/env python3
"""Early availability-only check; Docker APT still enforces signed archives."""
from __future__ import annotations
import argparse
import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

SUITES = ("noble", "noble-updates", "noble-backports", "noble-security")
RETRY_HTTP = (429, 502, 503, 504)


def require(cond, reason):
    if not cond:
        raise ValueError(reason)


def probe(snapshot: str, *, opener=urlopen, sleeper=time.sleep) -> dict:
    require(re.fullmatch(r"\d{8}T\d{6}Z", snapshot) is not None,
            "invalid pinned Ubuntu snapshot identity")
    results = {}
    for suite in SUITES:
        url = (f"https://snapshot.ubuntu.com/ubuntu/{snapshot}/dists/"
               f"{suite}/InRelease")
        for attempt in range(1, 4):
            try:
                with opener(url, timeout=10) as response:
                    response.read(512)
                results[suite] = {"reachable": True, "attempts": attempt}
                break
            except HTTPError as exc:
                if exc.code not in RETRY_HTTP or attempt == 3:
                    raise ValueError(
                        f"pinned snapshot {suite} unavailable HTTP {exc.code}") from exc
            except URLError as exc:
                if attempt == 3:
                    raise ValueError(
                        f"pinned snapshot {suite} unavailable network") from exc
            sleeper(attempt * 3)
    return {"schema_version": 1, "pinned_snapshot": snapshot,
            "availability_only": True, "signature_verified": False,
            "suites": results, "public_bot_release": False}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", type=Path, required=True)
    ap.add_argument("--sources", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    policy = json.loads(args.policy.read_text())
    snapshot = policy.get("apt_snapshot")
    sources = args.sources.read_text()
    require(isinstance(snapshot, str) and
            sources.count("Snapshot: " + snapshot) == 2 and
            "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg" in sources,
            "unpinned or unsigned Ubuntu snapshot policy")
    report = {"schema_version": 1, "availability_only": True,
              "signature_verified": False,
              "pinned_snapshot": snapshot, "public_bot_release": False}
    try:
        report.update(probe(snapshot))
        code = 0
    except ValueError as exc:
        # Fixed, bounded error classification; no user-controlled URLs/headers.
        report["error"] = str(exc)
        report["unavailable"] = True
        code = 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
    print(json.dumps(report,sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
