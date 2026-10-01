#!/usr/bin/env python3
"""Freeze/check the J10 independent calibration worklist.

This script does not start engines and does not label outcomes.  It validates
that the pre-outcome seed corpus has stable source-group identities and emits a
machine-readable collection plan for later staged n16->n32 evidence gathering.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CORPUS_META = ROOT / "qualification/j10-calibration-corpus-v1.json"
OUTPUT = ROOT / "build/j10-calibration/collection-plan.json"


class CollectionError(ValueError):
    pass


def parse_epd(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    seen_groups: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 4:
            raise CollectionError(f"malformed J10 corpus line: {raw!r}")
        case_id, group, fen, tags_raw = parts
        if not case_id or case_id in seen_ids:
            raise CollectionError(f"duplicate/empty case id: {case_id!r}")
        if not group or group in seen_groups:
            raise CollectionError(
                f"duplicate/empty independent source group: {group!r}"
            )
        if len(fen.split()) != 6:
            raise CollectionError(f"{case_id}: FEN must contain six fields")
        tags = [tag.strip() for tag in tags_raw.split(",") if tag.strip()]
        seen_ids.add(case_id)
        seen_groups.add(group)
        rows.append(
            {
                "case_id": case_id,
                "source_group": group,
                "fen": fen,
                "tags": tags,
                "position_command": f"position fen {fen}",
                "staged_intervention": {
                    "base_verify_nodes": 16,
                    "extension_verify_nodes": 32,
                    "owners": ["stockfish", "reckless", "lc0"],
                },
            }
        )
    if not rows:
        raise CollectionError("J10 corpus is empty")
    return rows


def main() -> int:
    meta = json.loads(CORPUS_META.read_text(encoding="utf-8"))
    path = ROOT / str(meta["path"])
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != meta.get("content_sha256"):
        raise CollectionError("J10 corpus content SHA-256 drift")
    rows = parse_epd(path)
    groups = {str(row["source_group"]) for row in rows}
    if len(rows) != int(meta.get("rows", -1)):
        raise CollectionError("J10 corpus row-count drift")
    if len(groups) != int(meta.get("independent_groups", -1)):
        raise CollectionError("J10 corpus independent-group count drift")

    output = {
        "schema_version": 1,
        "corpus_id": meta["corpus_id"],
        "corpus_sha256": digest,
        "rows": len(rows),
        "independent_groups": len(groups),
        "promotion_eligible": bool(meta.get("promotion_eligible")),
        "minimum_independent_groups_for_stop_promotion": int(
            meta["minimum_independent_groups_for_stop_promotion"]
        ),
        "cases": rows,
        "claim_boundary": {
            "outcomes_collected": False,
            "stop_promotion": False,
            "move_quality": False,
            "elo": False,
            "strength": False,
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CollectionError, OSError, KeyError, ValueError) as exc:
        print(f"J10 calibration collection plan failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
