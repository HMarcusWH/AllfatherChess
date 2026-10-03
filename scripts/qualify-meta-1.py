#!/usr/bin/env python3
"""Independently reconstruct one immutable META-1 campaign artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.local_game.common import save
from tools.meta1.integrity import qualify


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output-report", type=Path)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    report = qualify(campaign)
    output = (
        campaign / "report-independent.json"
        if args.output_report is None
        else args.output_report.resolve()
    )
    save(output, report)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
