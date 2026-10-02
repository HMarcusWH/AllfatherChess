#!/usr/bin/env python3
"""Re-run independent META-1 campaign validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.local_game.common import save
from tools.meta1.integrity import qualify


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    args = parser.parse_args()
    report = qualify(args.campaign.resolve())
    save(args.campaign / "report.json", report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
