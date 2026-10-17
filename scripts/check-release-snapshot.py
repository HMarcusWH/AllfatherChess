#!/usr/bin/env python3
"""Bounded snapshot reachability preflight; signed APT remains authoritative.

The preflight rejects missing/truncated clear-signed indexes, but DOES NOT
cryptographically authenticate Ubuntu's InRelease metadata. Docker APT must.
"""
from __future__ import annotations
import argparse
import json
import re
import time
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

SUITES = ("noble", "noble-updates", "noble-backports", "noble-security")
RETRY_HTTP = (429, 502, 503, 504)
MAX_INRELEASE_BYTES = 4 * 1024 * 1024
MIN_INRELEASE_BYTES = 512
TOTAL_BUDGET_SECONDS = 145


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def validate_sources(content: str, snapshot: str) -> None:
    """No extra source, unsigned stanza or changed Ubuntu suite."""
    expected = (
        (("noble", "noble-updates", "noble-backports"),
         "https://archive.ubuntu.com/ubuntu"),
        (("noble-security",), "https://security.ubuntu.com/ubuntu"),
    )
    stanzas = [s for s in re.split(r"\n[ \t]*\n", content.strip())
               if s.strip() and not s.lstrip().startswith("#")]
    require(len(stanzas) == 2, "unexpected pinned Ubuntu sources count")
    for stanza, (suites, origin) in zip(stanzas, expected):
        fields = {}
        for raw in stanza.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            require(":" in line, "malformed Ubuntu source stanza")
            key, value = line.split(":", 1)
            require(key not in fields, "duplicate Ubuntu source key")
            fields[key] = value.strip()
        require(set(fields) == {"Types", "URIs", "Suites", "Components",
                                "Architectures", "Signed-By", "Snapshot"} and
                fields["Types"] == "deb" and fields["URIs"] == origin and
                tuple(fields["Suites"].split()) == suites and
                fields["Architectures"] == "amd64" and
                fields["Snapshot"] == snapshot and
                fields["Signed-By"] ==
                    "/usr/share/keyrings/ubuntu-archive-keyring.gpg" and
                set(fields["Components"].split()) ==
                    {"main", "universe", "restricted", "multiverse"},
                "unapproved pinned Ubuntu source identity")


def validate_inrelease(body: bytes, suite: str) -> None:
    """A bounded, complete, recognizable PGP clear-signed Release envelope."""
    require(MIN_INRELEASE_BYTES <= len(body) <= MAX_INRELEASE_BYTES and
            b"\x00" not in body, "empty or truncated Ubuntu InRelease")
    try:
        content = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("unreadable Ubuntu InRelease") from exc
    require(content.startswith("-----BEGIN PGP SIGNED MESSAGE-----\n") and
            "\nHash: SHA" in content[:150] and
            "\n\n" in content[:160], "missing signed Ubuntu InRelease header")
    begin = content.find("\n-----BEGIN PGP SIGNATURE-----\n")
    ending = content.find("\n-----END PGP SIGNATURE-----", begin)
    require(begin > 300 and ending > begin + 48 and
            content[ending + len("\n-----END PGP SIGNATURE-----"):].strip() == "",
            "incomplete Ubuntu InRelease signature envelope")
    signed_text = content[:begin]
    require("Origin: Ubuntu" in signed_text and
            re.search(r"(?m)^Suite: " + re.escape(suite) + r"\s*$",
                      signed_text) is not None and
            "Architectures:" in signed_text and
            "Components:" in signed_text,
            "Ubuntu InRelease suite or release metadata invalid")


def read_complete(response, expected_url: str, suite: str, *,
                  deadline: float | None = None,
                  clock=time.monotonic) -> None:
    observed = response.geturl() if hasattr(response, "geturl") else expected_url
    require(observed == expected_url,
            "Ubuntu snapshot redirected outside frozen suite URL")
    content = bytearray()
    while True:
        require(deadline is None or clock() < deadline,
                "snapshot global preflight budget expired during body read")
        part = response.read(min(65536, MAX_INRELEASE_BYTES + 1 - len(content)))
        if not part:
            break
        content.extend(part)
        require(len(content) <= MAX_INRELEASE_BYTES,
                "Ubuntu InRelease exceeds preflight size budget")
    declared = getattr(response, "headers", {}).get("Content-Length")
    if declared is not None:
        require(declared.isdecimal() and int(declared) == len(content),
                "Ubuntu InRelease HTTP body truncated")
    validate_inrelease(bytes(content), suite)


def probe(snapshot: str, *, opener=urlopen, sleeper=time.sleep,
          clock=time.monotonic) -> dict:
    require(isinstance(snapshot, str) and
            re.fullmatch(r"\d{8}T\d{6}Z", snapshot) is not None,
            "invalid pinned Ubuntu snapshot identity")
    deadline = clock() + TOTAL_BUDGET_SECONDS
    results = {}
    for suite in SUITES:
        url = (f"https://snapshot.ubuntu.com/ubuntu/{snapshot}/dists/"
               f"{suite}/InRelease")
        for attempt in range(1, 4):
            left = deadline - clock()
            require(left > 1.0, "pinned snapshot global preflight budget exhausted")
            try:
                with opener(url, timeout=min(10.0, left - 0.5)) as response:
                    read_complete(response, url, suite,
                                  deadline=deadline, clock=clock)
                results[suite] = {"reachable": True, "attempts": attempt}
                break
            except HTTPError as exc:
                require(exc.code in RETRY_HTTP and attempt != 3,
                        f"pinned snapshot {suite} HTTP {exc.code} unavailable")
            except (URLError, TimeoutError, ConnectionResetError,
                    BrokenPipeError, IncompleteRead, OSError) as exc:
                require(attempt != 3,
                        f"pinned snapshot {suite} network/read unavailable")
            left = deadline - clock()
            require(left > 1.0, "pinned snapshot global preflight budget exhausted")
            sleeper(min(attempt * 3, max(0.0, left - 1.0)))
    return {"schema_version": 1, "pinned_snapshot": snapshot,
            "availability_only": True, "signature_verified": False,
            "suites": results, "public_bot_release": False}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", type=Path, required=True)
    ap.add_argument("--sources", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    report = {"schema_version": 1, "availability_only": True,
              "signature_verified": False, "public_bot_release": False}
    try:
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        snapshot = policy.get("apt_snapshot")
        require(isinstance(snapshot, str), "invalid pinned snapshot in policy")
        validate_sources(args.sources.read_text(encoding="utf-8"), snapshot)
        report["pinned_snapshot"] = snapshot
        report.update(probe(snapshot))
        code = 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        # All expected filesystem, socket and HTTP acquisition failures
        # create bounded, structured evidence; no raw response is retained.
        report["error_class"] = type(exc).__name__
        report["failure_reason"] = str(exc)[:180]
        report["unavailable_or_invalid"] = True
        code = 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
