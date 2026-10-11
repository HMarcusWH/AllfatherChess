#!/usr/bin/env python3
"""Build exactly one chess wheel from pinned sdist, and seal its runtime wheel hash.

chess==1.11.2 is sdist-only. The trusted download hash refers to that sdist,
not to a locally built wheel. Pip's --require-hashes must use the produced
wheel's actual SHA256 inside the networkless container.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

CHESS_SDIST_SHA256 = "a8b43e5678fdb3000695bdaa573117ad683761e5ca38e591c4826eba6d25bb39"

def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source-lock", required=True, type=Path)
    p.add_argument("--wheelhouse", required=True, type=Path)
    a = p.parse_args()
    lock = a.source_lock.read_text()
    if "chess==1.11.2" not in lock or CHESS_SDIST_SHA256 not in lock:
        raise SystemExit("untrusted chess source archive hash in dependency lock")
    out = a.wheelhouse.resolve()
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "download", "--no-deps",
                    "--require-hashes", "-r", str(a.source_lock.resolve()),
                    "-d", str(out)], check=True)
    source = list(out.glob("chess-1.11.2.tar.gz"))
    if len(source) != 1 or digest(source[0]) != CHESS_SDIST_SHA256:
        raise SystemExit("chess source distribution mismatch")
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps",
                    "--no-build-isolation", "--wheel-dir", str(out),
                    str(source[0])], check=True)
    builds = list(out.glob("chess-1.11.2-*.whl"))
    if len(builds) != 1:
        raise SystemExit("chess wheel absent or ambiguous")
    wheelhash = digest(builds[0])
    source[0].unlink()
    unexpected = [str(f.name) for f in out.iterdir() if not f.name.endswith(".whl")]
    if unexpected:
        raise SystemExit("unexpected non-wheel dependency: " + repr(unexpected))
    lines = lock.splitlines()
    ix = next((i for i, line in enumerate(lines) if line.startswith("chess==1.11.2")), None)
    if ix is None:
        raise SystemExit("chess requirement absent")
    end = ix + 1
    while end < len(lines) and lines[end].lstrip().startswith("--hash=sha256:"):
        end += 1
    lines[ix:end] = ["chess==1.11.2 \\", "    --hash=sha256:" + wheelhash]
    generated = "\n".join(lines) + "\n"
    (out / "runtime-wheels.lock").write_text(generated)
    output = {
        "schema_version": 1, "trusted_chess_sdist_sha256": CHESS_SDIST_SHA256,
        "built_chess_wheel_sha256": wheelhash,
        "source_lock_sha256": digest(a.source_lock),
        "runtime_lock_sha256": digest(out / "runtime-wheels.lock"),
        "wheels": {f.name: digest(f) for f in sorted(out.glob("*.whl"))}
    }
    if len(output["wheels"]) != 12:
        raise SystemExit("resolved bridge dependencies must have exactly 12 wheels")
    (out / "wheel-attestation.json").write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"wheel_count": 12, "chess_wheel_sha256": wheelhash}, sort_keys=True))

if __name__ == "__main__":
    main()
