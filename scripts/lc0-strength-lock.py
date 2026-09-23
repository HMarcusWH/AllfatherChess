#!/usr/bin/env python3
"""Validate and query the frozen/candidate LC0 qualification lock."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.strength_profile import StrengthProfileError, load_json, validate_lock

LOCK = ROOT / "qualification" / "lc0-strength.lock.json"


def main(argv: list[str]) -> int:
    data = load_json(LOCK)
    if argv == ["validate"]:
        validate_lock(data)
        print(f"LC0 strength lock OK: {data['qualification_status']}")
        return 0
    if argv == ["validate-frozen"]:
        validate_lock(data, require_frozen=True)
        print("LC0 strength lock is frozen")
        return 0
    if len(argv) == 3 and argv[0] in {"engine", "network"}:
        section = data.get(argv[0])
        if not isinstance(section, dict) or argv[1] not in section:
            raise StrengthProfileError(f"unknown {argv[0]} field: {argv[1]}")
        value = section[argv[1]]
        print(json.dumps(value) if isinstance(value, (dict, list)) else value)
        return 0
    raise StrengthProfileError(
        "usage: lc0-strength-lock.py validate|validate-frozen|"
        "engine <field>|network <field>"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except StrengthProfileError as exc:
        print(f"lc0-strength-lock: {exc}", file=sys.stderr)
        raise SystemExit(2)
