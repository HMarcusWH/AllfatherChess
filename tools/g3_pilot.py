"""Exploratory real G3-v2 vs Stockfish-anchor pilot using pinned Fastchess.

This is deliberately NOT J13 META-1. Unlike matched J12 ANCHOR_CONTROL, the
single-process anchor arm has different compute topology. A same-clock result
is descriptive only: it cannot establish equal-compute superiority or Elo.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys

from tools.engine_opt.domain import candidate_bundle_identity
from tools.g3_pilot_health import (
    source_hygiene, require_clean_hygiene, observe_replay_backend,
    aggregate_backend_coverage,
)
from tools.local_game.common import (
    ROOT, file_record, load, require, save, sha, source_identity, verify_record,
)
from tools.local_game.integrity import verify_builds
from tools.local_game.runner import bounded
from tools.local_game.validate import (
    match_game, read_games, session_games, verify_runner_log,
)
from tools.meta1.common import (
    command as pinned_match_command, opening_blocks, policy as meta1_policy,
)

POLICY = ROOT / "qualification/g3-pilot-v1.json"
ARMS = ("allfather-g3", "allfather-anchor")
INDICES = (0, 6, 12, 18, 24, 30, 36, 42)
ROOT_OUTPUT = ROOT / "build/test-results/g3-pilot"
SITE = "G3-EXPLORATORY"
CLAIM_BOUNDARY = {
    "offline_chess_game_exploration": True,
    "same_clock_descriptive_only": True,
    "equal_compute": False,
    "elo": False,
    "superiority": False,
    "j12_meta1_campaign": False,
    "j12_authority_qualified": False,
    "deployment": False,
    "live_lichess": False,
}


def pilot_policy(candidate: dict | None = None) -> dict:
    p = load(POLICY) if candidate is None else candidate
    meta = meta1_policy()
    require(type(p) is dict and p.get("schema_version") == 1
            and p.get("profile_id") == "g3-v2-anchor-exploratory-pilot"
            and p.get("scope") == "offline-same-clock-descriptive-only",
            "exploratory pilot policy drift")
    require(
        p.get("source_runtime") == "config/allfather.online-hybrid-v2.validation.json"
        and p.get("bundle_root") == "build/online-engine-opt-v2"
        and p.get("opening_file") == meta["opening_file"]
        and p.get("frozen_meta1_opening_source") == "qualification/meta-1-v1.json",
        "pilot does not use frozen real-backend G3 and source openings",
    )
    require(p.get("opening_indices") == list(INDICES)
            and len(set(INDICES)) == len(INDICES)
            and max(INDICES) < meta["opening_count"],
            "pilot opening selection was changed after pre-registration")
    require(p.get("arms") == list(ARMS)
            and p.get("games_per_opening") == 2
            and p.get("total_games") == len(INDICES) * 2
            and p.get("clock") == "0:30+1"
            and p.get("concurrency") == 1,
            "pilot arm/clock/game schedule drift")
    for name in ("startup_ms", "ping_ms", "job_timeout_s"):
        require(type(p.get(name)) is int and 0 < p[name] <= 900_000,
                "invalid pilot bounded runtime: " + name)
    require(type(p.get("timemargin_ms")) is int
            and p["timemargin_ms"] == 0,
            "pilot time margin differs from qualified Fastchess policy")
    for key in (
        "live_api_permitted", "host_j12_authority_qualified",
        "meta1_campaign_qualified", "same_compute_claim",
        "elo_gate", "superiority_claim", "artifact_distribution_authorized",
        "public_bot_release",
    ):
        require(p.get(key) is False, "pilot policy promoted forbidden claim: " + key)
    require(sha(ROOT / p["opening_file"]) == meta["opening_sha256"],
            "frozen META-1 opening source changed")
    return p


def planned_games(p: dict) -> tuple[list[dict], tuple[str, ...]]:
    blocks = opening_blocks(ROOT / p["opening_file"], 50)
    plans = []
    for ordinal, index in enumerate(INDICES):
        arms = list(ARMS if ordinal % 2 == 0 else reversed(ARMS))
        block = blocks[index]
        plans.append({
            "id": f"g3-pilot-{ordinal:02d}",
            "kind": "g3-exploratory-pair",
            "opening_index": index,
            "opening": f"opening-{ordinal:02d}.pgn",
            "opening_sha256": hashlib.sha256(block.encode("utf-8")).hexdigest(),
            "arms": arms,
            "clock": p["clock"],
            "restart": False,
            "driver_nodes": None,
            "allow_resource_denial": False,
        })
    return plans, blocks


def frozen_command(plan: dict, directory: Path, p: dict, runtime: dict,
                   fastchess: Path, opening: Path, *, write_specs: bool,
                   root: Path = ROOT, python_executable: str = sys.executable) -> list[str]:
    argv = pinned_match_command(
        plan, directory, p, runtime, fastchess, opening,
        write_specs=write_specs, root=root, python_executable=python_executable,
    )
    require(argv.count("-site") == 1, "Fastchess site flag is ambiguous")
    site_index = argv.index("-site")
    require(argv[site_index + 1] == "META-1", "unexpected Fastchess site default")
    argv[site_index + 1] = SITE
    return argv


def points(result: str, arm: str, white: str, black: str) -> float:
    require({white, black} == set(ARMS) and arm in ARMS,
            "score requested outside frozen G3/anchor arms")
    require(result in ("1-0", "0-1", "1/2-1/2"),
            "non-terminal or unsupported exploratory game result")
    if result == "1/2-1/2":
        return 0.5
    winner = white if result == "1-0" else black
    return 1.0 if arm == winner else 0.0


def summarize(p: dict, games: list[dict], plies: list[dict],
              backend_coverage: dict | None = None) -> dict:
    require(len(games) == p["total_games"], "pilot has incomplete game coverage")
    require(plies and {row.get("arm") for row in plies} == set(ARMS),
            "pilot lacks independent move evidence from both arms")
    scores = {arm: {"W": 0, "D": 0, "L": 0, "games": 0, "points": 0.0} for arm in ARMS}
    by_index: dict[int, list[dict]] = {}
    for game in games:
        index = game["opening_index"]
        require(index in INDICES, "pilot contains undeclared opening")
        by_index.setdefault(index, []).append(game)
        for arm in ARMS:
            score = points(game["result"], arm, game["white"], game["black"])
            outcome = "D" if score == 0.5 else "W" if score == 1.0 else "L"
            scores[arm][outcome] += 1
            scores[arm]["games"] += 1
            scores[arm]["points"] += score
    require(set(by_index) == set(INDICES),
            "pilot omitted a declared opening block")
    pairs = []
    for index in INDICES:
        pair = sorted(by_index[index], key=lambda x: x["game"])
        require(len(pair) == 2 and [g["game"] for g in pair] == [0, 1],
                "each opening needs exactly two completed games")
        require(pair[0]["white"] == pair[1]["black"]
                and pair[0]["black"] == pair[1]["white"],
                "opening colors did not reverse")
        g3_points = sum(points(g["result"], ARMS[0], g["white"], g["black"])
                        for g in pair)
        pairs.append({
            "opening_index": index,
            "g3_points": g3_points,
            "anchor_points": 2.0 - g3_points,
            "g3_minus_anchor_points": 2.0 * g3_points - 2.0,
            "results": [g["result"] for g in pair],
        })
    arm_telemetry = {}
    for arm in ARMS:
        rows = [r for r in plies if r["arm"] == arm]
        physical = [float(r["physical_cpu_ms"]) for r in rows
                    if type(r.get("physical_cpu_ms")) in (int, float)]
        proxy_cpu = [float(r["observed_cpu_ms"]) for r in rows
                     if type(r.get("observed_cpu_ms")) in (int, float)]
        arm_telemetry[arm] = {
            "plies": len(rows),
            "physical_cpu_ms_sum": round(sum(physical), 3),
            "physical_cpu_ms_missing": len(rows) - len(physical),
            "proxy_cpu_ms_sum": round(sum(proxy_cpu), 3),
            "proxy_cpu_ms_missing": len(rows) - len(proxy_cpu),
            "mean_response_ms": round(
                sum(float(r["response_ms"]) for r in rows) / len(rows), 3),
            "hybrid_authority_count": sum(
                r.get("authority") == "HYBRID" for r in rows),
            "anchor_fallback_count": sum(
                r.get("authority") == "ANCHOR_FALLBACK" for r in rows),
            "actual_anchor_changing_overrides": sum(
                r.get("override") is True for r in rows),
        }
    return {
        "scores": scores,
        "opening_pairs": pairs,
        "g3_minus_anchor_points": scores[ARMS[0]]["points"] - scores[ARMS[1]]["points"],
        "real_chess_games_verified": len(games),
        "authority_counts": dict(Counter(r.get("authority") for r in plies
                                         if r.get("authority") is not None)),
        "arm_telemetry": arm_telemetry,
        "backend_coverage": backend_coverage if backend_coverage is not None
                            else {"scope": "synthetic-summary-no-backend-verification"},
    }


def campaign_identity(source: dict) -> str:
    run = os.environ.get("GITHUB_RUN_ID", "local")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        require(run.isdigit() and attempt.isdigit(),
                "GitHub run/attempt identity missing")
    return f"{source['commit'][:12]}-{run}-{attempt}"


def validate_campaign(output: Path) -> dict:
    output = output.resolve()
    p = pilot_policy()
    source_id = source_identity()
    plans, blocks = planned_games(p)
    manifest = load(output / "manifest.json")
    require(manifest.get("schema_version") == 1
            and manifest.get("profile_id") == p["profile_id"]
            and manifest.get("status") == "completed"
            and not manifest.get("failures"),
            "exploratory pilot did not complete cleanly")
    require(manifest.get("source") == source_id
            and manifest.get("campaign_id") == output.name
            and output.parent.resolve() == ROOT_OUTPUT.resolve()
            and output.name == campaign_identity(source_id),
            "pilot output/source/attempt binding drift")
    require(manifest.get("plans") == plans
            and [j.get("plan") for j in manifest["jobs"]] == plans,
            "pilot output differs from frozen schedule")
    require(verify_record(ROOT, manifest["policy"]) == POLICY
            and verify_record(ROOT, manifest["fixture"]) == ROOT / p["opening_file"]
            and verify_record(ROOT, manifest["source_runtime"]) == ROOT / p["source_runtime"],
            "pilot source-input record differs from frozen policy")
    require(manifest.get("candidate_bundle") ==
            candidate_bundle_identity(ROOT / p["bundle_root"],
                                      expected_source_commit=source_id["commit"]),
            "pilot candidate engine/network bundle drift")
    fastchess = verify_builds(source_id, p)
    require(manifest.get("producer_root") == str(ROOT)
            and manifest.get("fastchess") == str(fastchess),
            "pilot producer host/source layout drift")
    from tools.g3_pilot_preflight import verify_preflight
    preflight = verify_record(ROOT, manifest.get("backend_preflight") or {})
    require(preflight == ROOT_OUTPUT / "preflight.json",
            "source-bound LC0 preflight is not at canonical game-host path")
    verified_preflight = verify_preflight(load(preflight), source_id)
    require(manifest.get("lc0_uci_preflight_passed") is True
            and verified_preflight["passed"] is True,
            "pilot did not prove game-host LC0 UCI/BLAS readiness")
    runtime = load(ROOT / p["source_runtime"])
    backend_observations: list[dict] = []
    game_rows: list[dict] = []
    ply_rows: list[dict] = []
    unique_run_ids: set[str] = set()
    unique_replays: set[str] = set()
    for plan, job in zip(plans, manifest["jobs"], strict=True):
        folder = output / plan["id"]
        opening_path = folder / plan["opening"]
        require(opening_path.is_file()
                and opening_path.read_text(encoding="utf-8")
                == blocks[plan["opening_index"]]
                and sha(opening_path) == plan["opening_sha256"],
                "pilot opening bytes differ from predeclared source")
        expected = frozen_command(
            plan, folder, p, runtime, fastchess, opening_path,
            write_specs=False,
            python_executable=manifest["python_executable"],
        )
        execution = job["execution"]
        require_clean_hygiene(job.get("source_hygiene_before"))
        require_clean_hygiene(job.get("source_hygiene_after"))
        require(execution.get("cwd") == str(folder),
                "Fastchess did not execute inside isolated match folder")
        require(execution["argv"] == expected
                and execution["returncode"] == 0
                and execution["timed_out"] is False
                and not execution.get("descendants_before_cleanup")
                and not execution.get("descendants_after_cleanup"),
                "pilot Fastchess invocation, completion or process cleanup invalid")
        verify_runner_log(folder / "runner.log", plan)
        games = read_games(folder / "games.pgn", require_completed=True)
        openings = read_games(opening_path)
        require(len(games) == 2 and len(openings) == 1,
                "pilot must retain two complete games and exactly one opening")
        streams = {}
        for arm in plan["arms"]:
            groups, summaries = session_games(
                folder, arm, runtime, p["source_runtime"], plan,
                unique_run_ids, unique_replays,
            )
            require(len(groups) == 2 and len(summaries) == 1,
                    "pilot expected exactly two games on one persistent engine process")
            streams[arm] = groups
        for searches in streams[ARMS[0]]:
            for item in searches:
                require(item.get("run") is not None,
                        "G3 move has no source-bound replay")
                backend_observations.append(observe_replay_backend(item["run"]))
        for game_id, game in enumerate(games):
            expected_colors = (plan["arms"] if game_id == 0
                               else list(reversed(plan["arms"])))
            require([game.headers["White"], game.headers["Black"]] == expected_colors,
                    "pilot game colors/reverse schedule drift")
            moves = match_game(
                game, {arm: streams[arm][game_id] for arm in plan["arms"]},
                False, openings[0], plan,
            )
            for move in moves:
                require(
                    move["arm"] != ARMS[0]
                    or move.get("authority") in ("HYBRID", "ANCHOR_FALLBACK"),
                    "G3 pilot emitted unsupported outward chess authority",
                )
            game_rows.append({
                "block": plan["id"],
                "opening_index": plan["opening_index"],
                "game": game_id,
                "white": game.headers["White"],
                "black": game.headers["Black"],
                "result": game.headers["Result"],
            })
            ply_rows.extend({"block": plan["id"], "game": game_id, **x} for x in moves)
    coverage = aggregate_backend_coverage(backend_observations)
    require(coverage["backend_failure_count"] == 0,
            "G3 pilot shadow backend failed; retained details in raw replay")
    require(coverage["lc0_explore_stages_observed"] > 0
            and coverage["three_family_explore_completions"] > 0,
            "G3 pilot lacks real completed LC0/three-family EXPLORE evidence")
    require(source_identity() == source_id,
            "pilot source identity drifted after Fastchess games")
    return summarize(p, game_rows, ply_rows, coverage)


def execute(output: Path) -> int:
    output = output.resolve()
    p = pilot_policy()
    source_id = source_identity()
    require(output.resolve().parent == ROOT_OUTPUT.resolve()
            and output.name == campaign_identity(source_id),
            "pilot output directory is not exact-attempt-bound")
    require(not output.exists() and not output.is_symlink(),
            "pilot attempts are immutable; retries need a new run attempt")
    fastchess = verify_builds(source_id, p)
    candidate = candidate_bundle_identity(
        ROOT / p["bundle_root"], expected_source_commit=source_id["commit"])
    plans, blocks = planned_games(p)
    from tools.g3_pilot_preflight import verify_preflight
    preflight = ROOT_OUTPUT / "preflight.json"
    require(preflight.is_file() and not preflight.is_symlink(),
            "real game-host LC0 preflight was not executed")
    require(verify_preflight(load(preflight), source_id).get("passed") is True,
            "LC0 game-host preflight is not verified")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    manifest = {
        "schema_version": 1,
        "profile_id": p["profile_id"],
        "source": source_id,
        "campaign_id": output.name,
        "producer_root": str(ROOT),
        "python_executable": sys.executable,
        "fastchess": str(fastchess),
        "policy": file_record(POLICY),
        "fixture": file_record(ROOT / p["opening_file"]),
        "source_runtime": file_record(ROOT / p["source_runtime"]),
        "candidate_bundle": candidate,
        "backend_preflight": file_record(preflight),
        "lc0_uci_preflight_passed": True,
        "plans": plans,
        "jobs": [],
        "failures": [],
        "status": "running",
        "claim_boundary": CLAIM_BOUNDARY,
    }
    save(output / "manifest.json", manifest)
    runtime = load(ROOT / p["source_runtime"])
    try:
        for plan in plans:
            before = source_hygiene()
            require_clean_hygiene(before)
            require(source_identity() == source_id,
                    "pilot repository modified before starting game pair")
            print(f"G3 pilot playing {plan['id']} {plan['arms']}", flush=True)
            folder = output / plan["id"]
            folder.mkdir()
            opening = folder / plan["opening"]
            opening.write_text(blocks[plan["opening_index"]], encoding="utf-8")
            require(sha(opening) == plan["opening_sha256"],
                    "pilot opening fixture was changed during generation")
            argv = frozen_command(plan, folder, p, runtime, fastchess,
                                  opening, write_specs=True)
            result = bounded(argv, folder, folder / "runner.log", p["job_timeout_s"])
            result["cwd"] = str(folder)
            after = source_hygiene()
            manifest["jobs"].append({
                "plan": plan, "execution": result,
                "source_hygiene_before": before,
                "source_hygiene_after": after,
            })
            save(output / "manifest.json", manifest)
            require_clean_hygiene(after)
            require(source_identity() == source_id,
                    "Fastchess wrote or changed source outside isolated match folder")
            if (result["returncode"] != 0 or result["timed_out"]
                    or result.get("descendants_before_cleanup")
                    or result.get("descendants_after_cleanup")):
                manifest["failures"].append(
                    f"{plan['id']}: failed game/worker or retained leaked process")
                break
        manifest["status"] = (
            "completed" if len(manifest["jobs"]) == len(plans)
            and not manifest["failures"] else "failed"
        )
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failures"].append(f"{type(exc).__name__}: {exc}")
    finally:
        save(output / "manifest.json", manifest)
    return verify(output)


def verify(output: Path) -> int:
    output = output.resolve()
    result: dict = {
        "schema_version": 1,
        "profile_id": "g3-v2-anchor-exploratory-pilot",
        "passed": False,
        "evidence_qualified_for": "same-clock-exploratory-pilot-only",
        "errors": [],
        "claim_boundary": dict(CLAIM_BOUNDARY),
    }
    try:
        result.update(validate_campaign(output))
        result["passed"] = True
    except Exception as exc:
        result["errors"] = [f"{type(exc).__name__}: {exc}"]
    output.mkdir(parents=True, exist_ok=True)
    save(output / "report.json", result)
    print(json.dumps({
        "passed": result["passed"],
        "games": result.get("real_chess_games_verified", 0),
        "scores": result.get("scores"),
        "actual_overrides": (result.get("arm_telemetry") or {})
            .get("allfather-g3", {}).get("actual_anchor_changing_overrides"),
        "errors": result["errors"],
        "backend_coverage": result.get("backend_coverage"),
        "same_compute_claim": False,
        "meta1_campaign_qualified": False,
    }, sort_keys=True), flush=True)
    return 0 if result["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("contracts", "run", "verify"),
                        default="contracts")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    p = pilot_policy()
    plans, blocks = planned_games(p)
    if args.phase == "contracts":
        print(json.dumps({
            "profile_id": p["profile_id"],
            "opening_indices": p["opening_indices"],
            "pairs": len(plans),
            "games": p["total_games"],
            "source_opening_count": len(blocks),
            "claim_boundary": CLAIM_BOUNDARY,
        }, sort_keys=True))
        return 0
    path = args.output or (
        ROOT_OUTPUT / campaign_identity(source_identity()))
    if args.phase == "run":
        return execute(path)
    return verify(path)


if __name__ == "__main__":
    raise SystemExit(main())
