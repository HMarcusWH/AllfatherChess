#!/usr/bin/env python3
"""Tiny deterministic process used by physical-resource contract tests."""

from __future__ import annotations

import sys
import time


def burn(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    value = 1
    while time.monotonic() < deadline:
        value = (value * 1664525 + 1013904223) & 0xFFFFFFFF
    if value < 0:  # pragma: no cover
        print(value, file=sys.stderr)


def main() -> int:
    mode = sys.argv[1]
    seconds = float(sys.argv[2])
    print("ready", flush=True)
    for raw in sys.stdin:
        command = raw.strip()
        if command == "run":
            if mode == "burn":
                burn(seconds)
            elif mode == "sleep":
                time.sleep(seconds)
            else:
                raise SystemExit(f"unknown mode: {mode}")
            print("done", flush=True)
        elif command == "quit":
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
