"""S0: remeasure historical G3 evidence, never modify the original campaign.

Requires an independently source-bound retained Actions archive or extracted
campaign; all nested stream/checkpoint integrity is checked by existing replay
verifiers. This is development evidence and cannot promote chess strength.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile

from tools.g3_pilot_health import observe_replay_backend, aggregate_backend_coverage
from tools.local_game.common import ROOT, load, require, save, sha
from tools.local_game.validate import read_games

POLICY = ROOT / "qualification/strength/dev-g3-20261025-v1.json"
MAX_FILES = 22000
MAX_BYTES = 900 * 1024 * 1024
MAX_FILE = 80 * 1024 * 1024


def frozen_policy():
    p = load(POLICY)
    require(p.get("schema_version") == 1
            and p.get("id") == "dev-g3-20261025"
            and p.get("role") == "development_negative_baseline_only",
            "unapproved S0 source policy")
    for key in ("public_bot_release", "playing_strength_qualified",
                "live_game_authorized"):
        require(p.get(key) is False, "S0 cannot promote external authority")
    return p


def check_regular_tree(directory: Path):
    require(directory.is_dir() and not directory.is_symlink(),
            "invalid historical input directory")
    count = total = 0
    for here, dirs, files in os.walk(directory, followlinks=False):
        for name in dirs + files:
            p = Path(here) / name
            st = p.lstat()
            require(not stat.S_ISLNK(st.st_mode), "artifact symlink forbidden")
            if name in files:
                require(stat.S_ISREG(st.st_mode) and st.st_size <= MAX_FILE,
                        "historical artifact has oversized/special file")
                count += 1
                total += st.st_size
                require(count <= MAX_FILES and total <= MAX_BYTES,
                        "historical artifact exceeded bounded limits")
    return count, total


def unpack_source_archive(archive: Path, temporary: Path, policy: dict):
    require(archive.is_file() and not archive.is_symlink()
            and archive.name == policy["archive_name"]
            and sha(archive) == policy["archive_sha256"],
            "ZIP name or independently pinned SHA256 differs")
    with zipfile.ZipFile(archive) as z:
        members = z.infolist()
        require(0 < len(members) <= MAX_FILES, "invalid archive file count")
        total = 0
        seen = set()
        for item in members:
            name = item.filename
            path = PurePosixPath(name)
            require(name not in seen and name and not name.startswith("/")
                    and "\\" not in name and ".." not in path.parts
                    and path.parts[0] == policy["campaign_directory"]
                    and not item.flag_bits & 1
                    and not stat.S_ISLNK(item.external_attr >> 16),
                    "unsafe ZIP member or root")
            seen.add(name)
            require(item.file_size <= MAX_FILE,
                    "oversized historical ZIP member")
            total += item.file_size
            require(total <= MAX_BYTES, "ZIP expansion exceeds input limit")
        for item in members:
            dest = temporary / item.filename
            require(dest.resolve().is_relative_to(temporary.resolve()),
                    "ZIP extraction escaped bounded root")
            if item.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(item) as source, dest.open("xb") as target:
                    shutil.copyfileobj(source, target, length=1 << 20)
    directory = temporary / policy["campaign_directory"]
    check_regular_tree(directory)
    return directory


def score_games(campaign: Path):
    scores = {arm: {"W": 0, "D": 0, "L": 0, "points": 0.0}
              for arm in ("allfather-g3", "allfather-anchor")}
    n = 0
    for index in range(8):
        pair = read_games(campaign / f"g3-pilot-{index:02d}" / "games.pgn",
                          require_completed=True)
        require(len(pair) == 2
                and pair[0].headers["White"] == pair[1].headers["Black"]
                and pair[0].headers["Black"] == pair[1].headers["White"],
                "historical game pair incomplete or colors not reversed")
        for game in pair:
            white, black = game.headers["White"], game.headers["Black"]
            result = game.headers["Result"]
            require({white, black} == set(scores)
                    and result in ("1-0", "0-1", "1/2-1/2"),
                    "historical game has invalid player or result")
            n += 1
            winner = white if result == "1-0" else black
            for arm in scores:
                points = 0.5 if result == "1/2-1/2" else float(arm == winner)
                field = "D" if points == 0.5 else "W" if points == 1 else "L"
                scores[arm][field] += 1
                scores[arm]["points"] += points
    return n, scores


def audit(campaign: Path, source_zip_verified: bool):
    policy = frozen_policy()
    campaign = campaign.resolve()
    require(campaign.name == policy["campaign_directory"],
            "historical campaign identity drift")
    file_count, total_size = check_regular_tree(campaign)
    manifest = load(campaign / "manifest.json")
    old = load(campaign / "report.json")
    require(manifest.get("source") == {
        "commit": policy["source_commit"], "tree": policy["source_tree"]}
        and manifest.get("campaign_id") == policy["campaign_directory"]
        and manifest.get("status") == "completed"
        and len(manifest.get("jobs", [])) == 8
        and manifest.get("failures") == [],
        "historical source/attempt/job record not complete")
    require(old.get("passed") is True and old.get("errors") == [],
            "historical producer report is not a valid baseline")
    games, scores = score_games(campaign)
    require(games == old["real_chess_games_verified"],
            "PGN games differ from original report")
    for arm in scores:
        for name, value in scores[arm].items():
            require(old["scores"][arm][name] == value,
                    "PGN scores differ from source report")
    replays = sorted(x.parent.parent for x in
                     campaign.rglob("decision/final.json"))
    require(replays and len(replays) == len(set(replays)),
            "source-joined G3 decision replays missing")
    seen = set()
    observations = []
    for run in replays:
        parts = run.relative_to(campaign).parts
        require(len(parts) == 6 and parts[0].startswith("g3-pilot-")
                and parts[1:3] == ("sessions", "allfather-g3")
                and parts[4] == "replays" and run.name not in seen,
                "unexpected or duplicate G3 replay occurrence")
        seen.add(run.name)
        parent = load(run / "manifest.json")
        require(parent.get("run_id") == run.name,
                "nested replay run_id differs from occurrence")
        observations.append(observe_replay_backend(run))
    coverage = aggregate_backend_coverage(observations)
    measured = {
        "games": games,
        "g3_wins": scores["allfather-g3"]["W"],
        "g3_draws": scores["allfather-g3"]["D"],
        "g3_losses": scores["allfather-g3"]["L"],
    }
    for key, expected in policy["expected"].items():
        if key not in measured:
            measured[key] = coverage.get(key)
        require(measured[key] == expected,
                f"historical {key}: expected {expected!r}, observed {measured[key]!r}")
    require(coverage["backend_failure_count"] == 0,
            "reclassified historical backend failure")
    return {
        "schema_version": 1,
        "status": "HISTORICAL_DEVELOPMENT_RECONCILED",
        "audit_id": policy["id"],
        "source_commit": policy["source_commit"],
        "source_tree": policy["source_tree"],
        "historical_artifact_sha256": policy["archive_sha256"],
        "archive_sha256_independently_verified": source_zip_verified,
        "source_file_count": file_count,
        "source_size_bytes": total_size,
        "measured": measured,
        "backend_coverage": coverage,
        "original_producer_verify_count": (
            old.get("backend_coverage") or {}).get("verify_stages_completed"),
        "development_only": True,
        "old_report_modified": False,
        "playing_strength_qualified": False,
        "public_bot_release": False,
        "live_game_authorized": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--campaign", type=Path)
    source.add_argument("--archive", type=Path)
    parser.add_argument("--audit-id", default="dev-g3-20261025")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    policy = frozen_policy()
    require(args.audit_id == policy["id"], "unfrozen audit ID")
    output = args.output.resolve()
    require(output.is_relative_to(ROOT / "build")
            and output.suffix == ".json"
            and not output.exists(),
            "S0 output must be a new immutable JSON inside build/")
    if args.archive is None:
        result = audit(args.campaign, source_zip_verified=False)
    else:
        with tempfile.TemporaryDirectory(prefix="allfather-s0-") as temp:
            campaign = unpack_source_archive(args.archive, Path(temp), policy)
            result = audit(campaign, source_zip_verified=True)
    save(output, result)
    print(json.dumps({"status": result["status"],
                      "measured": result["measured"],
                      "original_verify_count": result["original_producer_verify_count"],
                      "source_zip_verified": result["archive_sha256_independently_verified"],
                      "strength_qualified": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
