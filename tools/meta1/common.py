"""Frozen META-1 campaign contract and deterministic schedule."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from tools.local_game.common import ROOT, load, require, save, sha
from tools.local_game.runner import engine_options


POLICY_PATH = ROOT / "qualification/meta-1-v1.json"
ARMS = ("allfather-orchestrated", "allfather-anchor-control")
RUN_DISPOSITION = "RUN_META1"


def policy(path: Path | str = POLICY_PATH) -> dict:
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    value = load(path)
    require(value.get("schema_version") == 1, "unsupported META-1 policy schema")
    require(value.get("profile_id") == "meta-1-v1", "unexpected META-1 profile")
    require(value.get("source_runtime") == "config/allfather.orchestrated-v1.validation.json",
            "META-1 must derive from the frozen J12 runtime")
    require(value.get("arms") == list(ARMS), "META-1 arm set/order drift")
    require(value.get("opening_count") == 50, "META-1 requires exactly 50 openings")
    require(value.get("games_per_opening") == 2 and value.get("games_total") == 100,
            "META-1 requires exactly 100 games")
    require(value.get("colors_reversed") is True, "META-1 requires reversed colors")
    require(value.get("concurrency") == 1, "META-1 must remain serial")
    require(value.get("same_compute_claim") is False, "META-1 is not an equal-compute claim")
    require(value.get("result_gate") is False and value.get("sprt") is False
            and value.get("elo_gate") is False, "META-1 results may not become a merge gate")
    opening = ROOT / value["opening_file"]
    require(opening.is_file(), "META-1 opening fixture is missing")
    require(sha(opening) == value.get("opening_sha256"), "META-1 opening fixture hash drift")
    return value


def opening_blocks(path: Path, expected: int = 50) -> tuple[str, ...]:
    text = Path(path).read_text(encoding="utf-8")
    starts = [m.start() for m in re.finditer(r"(?m)^\[Event ", text)]
    require(len(starts) == expected, f"META-1 opening count drift: {len(starts)}")
    starts.append(len(text))
    blocks = tuple(text[starts[i]:starts[i + 1]].strip() + "\n" for i in range(expected))
    events = []
    for block in blocks:
        match = re.search(r'^\[Event "([^"]+)"\]$', block, re.MULTILINE)
        require(match is not None, "META-1 opening lacks Event tag")
        events.append(match.group(1))
    require(len(set(events)) == expected, "META-1 opening identities are not unique")
    return blocks


def schedule(p: dict) -> list[dict]:
    blocks = opening_blocks(ROOT / p["opening_file"], p["opening_count"])
    jobs = []
    for index, _ in enumerate(blocks):
        arms = list(ARMS if index % 2 == 0 else tuple(reversed(ARMS)))
        jobs.append({
            "id": f"meta1-{index:02d}",
            "kind": "meta1",
            "opening_index": index,
            "opening": f"opening-{index:02d}.pgn",
            "arms": arms,
            "clock": p["clock"],
            "restart": False,
            "driver_nodes": None,
            "allow_resource_denial": False,
        })
    require(len(jobs) * p["games_per_opening"] == p["games_total"],
            "META-1 schedule/game count mismatch")
    return jobs


def campaign_disposition(j12: dict) -> str:
    require(j12.get("mechanism_valid") is True, "J12 mechanism evidence is invalid")
    disposition = j12.get("qualification_disposition")
    if j12.get("authority_qualified") is True:
        require(
            disposition == "QUALIFIED_ORCHESTRATED_AUTHORITY"
            and j12.get("synthetic_capacity_observation") is False,
            "J12 claims authority without a real qualified host",
        )
        return RUN_DISPOSITION
    require(
        disposition in ("NOT_QUALIFIED_HOST_CAPACITY", "NOT_QUALIFIED_POSITIVE_WITNESS"),
        "J12 has an unsupported non-authority disposition",
    )
    return str(disposition)


def command(plan: dict, directory: Path, p: dict, source: dict, fastchess: Path,
            opening_path: Path, *, write_specs: bool = True) -> list[str]:
    argv = [
        str(fastchess), "-concurrency", "1", "-rounds", "1", "-games", "2", "-repeat",
        "-variant", "standard", "-ratinginterval", "0", "-autosaveinterval", "0",
        "-startup-ms", str(p["startup_ms"]), "-ping-ms", str(p["ping_ms"]),
        "-ucinewgame-ms", str(p["ping_ms"]), "-event", plan["id"], "-site", "META-1",
        "-pgnout", f"file={directory / 'games.pgn'}", "notation=san", "append=false",
        "timeleft=true", "pv=false", "-log", f"file={directory / 'runner.log'}",
        "level=warn", "append=false", "-openings", f"file={opening_path}",
        "format=pgn", "order=sequential",
    ]
    for arm in plan["arms"]:
        options, environment = engine_options(arm, source)
        spec_path = directory / f"{arm}.json"
        spec = {"schema_version": 1, "arm": arm, "root": str(ROOT),
                "sessions": str(directory / "sessions" / arm), "environment": environment,
                "source_runtime": p["source_runtime"]}
        if write_specs:
            save(spec_path, spec)
        argv += ["-engine", f"name={arm}", f"cmd={sys.executable}",
                 f"args=-m tools.local_game.proxy --spec {spec_path}", f"dir={ROOT}",
                 "proto=uci", f"tc={plan['clock']}", f"timemargin={p['timemargin_ms']}",
                 "restart=off"]
        for name, value in options.items():
            rendered = str(value).lower() if isinstance(value, bool) else str(value)
            argv.append(f"option.{name}={rendered}")
    return argv
