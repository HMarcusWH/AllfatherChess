"""S1 E01 source-locked 16-game matched G3 output-control experiment.

Runs only when cgroup v2 is delegated. Same opening/clock/source policy is not
equal total CPU, and a chess-game difference is not an Elo/superiority proof.
No LIVE API, publication, release or change to the playing-engine controller.
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
from tools.g3_pilot import campaign_identity
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
    read_games, match_game, session_games, verify_runner_log,
)
from tools.meta1.common import (
    command as pinned_match_command, opening_blocks, policy as meta1_policy,
)
from tools.strength_lab.diagnostic import contract as s1_source_contract
from tools.strength_lab.participant_evidence import verify_resource_receipts


POLICY = ROOT / "qualification/strength/s1-e01-v1.json"
ARMS = ("allfather-g3", "allfather-g3-anchor-control")
INDICES = (0, 6, 12, 18, 24, 30, 36, 42)
ROOT_OUTPUT = ROOT / "build/test-results/strength-s1-e01"
CLAIMS = {
    "strict_participant_cpu_observations_required": True,
    "source_locked_outward_only_ablation": True,
    "game_results_descriptive_only": True,
    "equal_compute": False,
    "elo": False,
    "superiority": False,
    "live_lichess": False,
    "release_authorized": False,
}


def policy(candidate: dict | None = None) -> dict:
    p = load(POLICY) if candidate is None else candidate
    meta = meta1_policy()
    control = s1_source_contract()
    require(
        type(p) is dict and p.get("schema_version") == 1
        and p.get("profile_id") == "strength-s1-e01-g3-output-only-matched-control-v1"
        and p.get("status") == "IMPLEMENTED_NOT_EXECUTED_OR_QUALIFIED"
        and p.get("source_contract") ==
            "qualification/strength/g3-output-control-v1.json"
        and p.get("source_runtime") == control["source_runtime"]
        and p.get("bundle_root") == "build/online-engine-opt-v2",
        "E01 source profile or experiment status changed",
    )
    require(
        p.get("opening_file") == meta["opening_file"]
        and p.get("frozen_meta1_opening_source") == "qualification/meta-1-v1.json"
        and sha(ROOT / p["opening_file"]) == meta["opening_sha256"]
        and p.get("opening_indices") == list(INDICES)
        and max(INDICES) < meta["opening_count"],
        "E01 openings differ from frozen META-1 corpus",
    )
    require(
        p.get("arms") == list(ARMS)
        and p.get("games_per_opening") == 2
        and p.get("total_games") == 2 * len(INDICES)
        and p.get("clock") == "0:30+1"
        and p.get("concurrency") == 1
        and p.get("resource_accounting") ==
            "strict_cgroup_v2_child_tree_plus_launcher"
        and p.get("require_real_network") is True
        and p.get("require_independent_replay_verification") is True
        and p.get("require_strict_resource_receipts") is True
        and p.get("require_scope_no_leak") is True,
        "E01 campaign/resource coverage drift",
    )
    for key in ("startup_ms", "ping_ms", "job_timeout_s"):
        require(type(p.get(key)) is int and 0 < p[key] <= 900_000,
                f"E01 invalid bounded runtime: {key}")
    require(p.get("timemargin_ms") == 0, "E01 changed pinned timing")
    for key in (
        "same_compute_claim", "equal_compute", "strength_qualified",
        "elo_gate", "superiority_claim", "live_api_permitted",
        "artifact_distribution_authorized", "public_bot_release",
    ):
        require(p.get(key) is False, f"E01 forbidden promotion: {key}")
    return p


def planned_games(p: dict) -> tuple[list[dict], tuple[str, ...]]:
    blocks = opening_blocks(ROOT / p["opening_file"], 50)
    plans = []
    for ordinal, index in enumerate(INDICES):
        arms = list(ARMS if ordinal % 2 == 0 else reversed(ARMS))
        block = blocks[index]
        plans.append({
            "id": f"s1-e01-{ordinal:02d}",
            "kind": "strength-s1-output-only-diagnostic",
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


def command(plan: dict, directory: Path, p: dict, runtime: dict,
            fastchess: Path, opening: Path, *, write_specs: bool,
            root: Path = ROOT, python_executable: str = sys.executable) -> list[str]:
    """Change ONLY the transparent proxy executable to strict resource wrapper."""
    argv = pinned_match_command(
        plan, directory, p, runtime, fastchess, opening,
        write_specs=write_specs, root=root, python_executable=python_executable,
    )
    require(argv.count("-site") == 1, "ambiguous Fastchess site field")
    site = argv.index("-site")
    require(argv[site + 1] == "META-1", "unexpected Fastchess site source")
    argv[site + 1] = "S1-E01"
    for arm in plan["arms"]:
        spec_path = directory / f"{arm}.json"
        original = f"args=-m tools.local_game.proxy --spec {spec_path}"
        require(argv.count(original) == 1,
                f"{arm}: missing or duplicated canonical UCI passthrough")
        argv[argv.index(original)] = (
            f"args=-m tools.strength_lab.participant_scope --spec {spec_path}"
        )
    require(sum("tools.strength_lab.participant_scope" in x for x in argv) == 2,
            "S1 experiment did not scope both chess participants")
    return argv


def _points(result: str, arm: str, white: str, black: str) -> float:
    require(result in ("1-0", "0-1", "1/2-1/2"), "nonterminal E01 game")
    if result == "1/2-1/2":
        return .5
    return float((white if result == "1-0" else black) == arm)


def validate_campaign(output: Path) -> dict:
    output = output.resolve()
    p = policy()
    source = source_identity()
    plans, blocks = planned_games(p)
    manifest = load(output / "manifest.json")
    require(
        manifest.get("schema_version") == 1
        and manifest.get("profile_id") == p["profile_id"]
        and manifest.get("status") == "completed"
        and not manifest.get("failures")
        and manifest.get("source") == source
        and manifest.get("campaign_id") == output.name
        and output.parent == ROOT_OUTPUT.resolve()
        and output.name == campaign_identity(source),
        "E01 campaign not finished or exact-source-bound",
    )
    require(
        manifest.get("plans") == plans
        and [job.get("plan") for job in manifest["jobs"]] == plans,
        "E01 job schedule was edited or incomplete",
    )
    require(
        verify_record(ROOT, manifest["policy"]) == POLICY
        and verify_record(ROOT, manifest["fixture"]) ==
            ROOT / p["opening_file"]
        and verify_record(ROOT, manifest["source_runtime"]) ==
            ROOT / p["source_runtime"],
        "E01 policy/fixture/runtime artifact changed",
    )
    require(
        manifest.get("candidate_bundle") ==
            candidate_bundle_identity(
                ROOT / p["bundle_root"], expected_source_commit=source["commit"]),
        "E01 executable/model bundle differs from exact-head source",
    )
    fastchess = verify_builds(source, p)
    require(
        manifest.get("fastchess") == str(fastchess)
        and manifest.get("producer_root") == str(ROOT)
        and manifest.get("resource_accounting") == p["resource_accounting"],
        "E01 game host or strict resource contract differs",
    )
    from tools.g3_pilot_preflight import verify_preflight
    preflight = verify_record(ROOT, manifest["backend_preflight"])
    require(
        preflight == ROOT / "build/test-results/g3-pilot/preflight.json"
        and verify_preflight(load(preflight), source)["passed"] is True,
        "E01 was run without source-verified real LC0 b4 game-host readiness",
    )
    runtime = load(ROOT / p["source_runtime"])
    games, plies, resource_rows, observations = [], [], [], []
    run_ids, manifest_hashes = set(), set()
    for plan, job in zip(plans, manifest["jobs"], strict=True):
        folder = output / plan["id"]
        opening = folder / plan["opening"]
        require(opening.read_text(encoding="utf-8") ==
                blocks[plan["opening_index"]]
                and sha(opening) == plan["opening_sha256"],
                "E01 individual opening input changed")
        execution = job["execution"]
        require_clean_hygiene(job["source_hygiene_before"])
        require_clean_hygiene(job["source_hygiene_after"])
        require(
            execution["argv"] == command(
                plan, folder, p, runtime, fastchess, opening,
                write_specs=False,
                python_executable=manifest["python_executable"],
            )
            and execution["cwd"] == str(folder)
            and execution["returncode"] == 0
            and execution["timed_out"] is False
            and not execution.get("descendants_before_cleanup")
            and not execution.get("descendants_after_cleanup"),
            "E01 Fastchess invocation, timeout, or leak",
        )
        verify_runner_log(folder / "runner.log", plan)
        resource = verify_resource_receipts(folder, tuple(plan["arms"]))
        resource_rows.append({"block": plan["id"], **resource})
        played = read_games(folder / "games.pgn", require_completed=True)
        opened = read_games(opening)
        require(len(played) == 2 and len(opened) == 1,
                "E01 opening pair missing completed games")
        by_arm = {}
        for arm in plan["arms"]:
            grouped, summaries = session_games(
                folder, arm, runtime, p["source_runtime"], plan,
                run_ids, manifest_hashes,
            )
            require(len(grouped) == 2 and len(summaries) == 1,
                    f"E01 {arm}: incomplete persistent game session")
            by_arm[arm] = grouped
            for searches in grouped:
                for item in searches:
                    require(item["run"] is not None,
                            "E01 lacked authority replay per move")
                    observations.append(observe_replay_backend(item["run"]))
        for game_number, played_game in enumerate(played):
            expected_colors = (plan["arms"] if game_number == 0
                               else list(reversed(plan["arms"])))
            require(
                [played_game.headers["White"], played_game.headers["Black"]]
                    == expected_colors,
                "E01 opening colors did not reverse",
            )
            moves = match_game(
                played_game,
                {arm: by_arm[arm][game_number] for arm in plan["arms"]},
                False, opened[0], plan,
            )
            for move in moves:
                if move["arm"] == ARMS[1]:
                    require(move.get("authority") == "ANCHOR_CONTROL"
                            and move.get("override") is False,
                            "E01 control changed outward incumbent")
                else:
                    require(move.get("authority") in
                            ("HYBRID", "ANCHOR_FALLBACK"),
                            "E01 G3 arm unexpectedly suppressed authority")
            games.append({
                "block": plan["id"], "opening_index": plan["opening_index"],
                "game": game_number, "white": played_game.headers["White"],
                "black": played_game.headers["Black"],
                "result": played_game.headers["Result"],
            })
            plies.extend({"block": plan["id"], "game": game_number, **move}
                         for move in moves)
    backend = aggregate_backend_coverage(observations)
    require(
        backend["backend_failure_count"] == 0
        and backend["lc0_explore_stages_observed"] > 0
        and backend["three_family_explore_completions"] > 0,
        "E01 lacks verified real three-family G3 evidence",
    )
    require(len(games) == p["total_games"] and plies,
            "E01 missing whole-game records")
    totals = {
        arm: {"W": 0, "D": 0, "L": 0, "points": 0.0, "games": 0}
        for arm in ARMS
    }
    for game in games:
        for arm in ARMS:
            points = _points(
                game["result"], arm, game["white"], game["black"])
            outcome = "D" if points == .5 else "W" if points == 1 else "L"
            totals[arm][outcome] += 1
            totals[arm]["points"] += points
            totals[arm]["games"] += 1
    require(source_identity() == source, "E01 source mutated after games")
    return {
        "status": "DESCRIPTIVE_S1_E01_MEASURED",
        "real_chess_games_verified": len(games),
        "results": totals,
        "actual_g3_overrides": sum(
            move.get("override") is True and move["arm"] == ARMS[0]
            for move in plies),
        "would_authorize_control_changes": sum(
            move.get("suppressed_authorized_non_anchor") is True
            and move["arm"] == ARMS[1] for move in plies),
        "authority_counts": dict(Counter(
            move.get("authority") for move in plies)),
        "whole_participant_resources": resource_rows,
        "backend_coverage": backend,
        "same_compute_claim": False,
        "equal_compute": False,
        "elo": False,
        "superiority": False,
        "strength_qualified": False,
    }


def execute(output: Path) -> int:
    output = output.resolve()
    p = policy()
    source = source_identity()
    require(
        output.parent == ROOT_OUTPUT.resolve()
        and output.name == campaign_identity(source)
        and not output.exists() and not output.is_symlink(),
        "E01 requires one fresh, exact-attempt-bound output",
    )
    fastchess = verify_builds(source, p)
    candidate = candidate_bundle_identity(
        ROOT / p["bundle_root"], expected_source_commit=source["commit"])
    plans, blocks = planned_games(p)
    from tools.g3_pilot_preflight import verify_preflight
    preflight = ROOT / "build/test-results/g3-pilot/preflight.json"
    require(preflight.is_file() and not preflight.is_symlink()
            and verify_preflight(load(preflight), source)["passed"] is True,
            "E01 lacks real LC0 game-host readiness")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    manifest = {
        "schema_version": 1, "profile_id": p["profile_id"],
        "source": source, "campaign_id": output.name,
        "producer_root": str(ROOT), "python_executable": sys.executable,
        "fastchess": str(fastchess), "policy": file_record(POLICY),
        "fixture": file_record(ROOT / p["opening_file"]),
        "source_runtime": file_record(ROOT / p["source_runtime"]),
        "candidate_bundle": candidate,
        "backend_preflight": file_record(preflight),
        "resource_accounting": p["resource_accounting"],
        "plans": plans, "jobs": [], "failures": [], "status": "running",
        "claim_boundary": CLAIMS,
    }
    save(output / "manifest.json", manifest)
    runtime = load(ROOT / p["source_runtime"])
    try:
        for plan in plans:
            before = source_hygiene()
            require_clean_hygiene(before)
            require(source_identity() == source, "E01 repository changed")
            folder = output / plan["id"]
            folder.mkdir()
            opening = folder / plan["opening"]
            opening.write_text(blocks[plan["opening_index"]], encoding="utf-8")
            require(sha(opening) == plan["opening_sha256"],
                    "E01 opening bytes changed")
            argv = command(
                plan, folder, p, runtime, fastchess, opening, write_specs=True)
            result = bounded(
                argv, folder, folder / "runner.log", p["job_timeout_s"])
            result["cwd"] = str(folder)
            after = source_hygiene()
            manifest["jobs"].append({
                "plan": plan, "execution": result,
                "source_hygiene_before": before,
                "source_hygiene_after": after,
            })
            save(output / "manifest.json", manifest)
            require_clean_hygiene(after)
            require(source_identity() == source, "E01 checkout changed")
            if (
                result["returncode"] != 0 or result["timed_out"]
                or result.get("descendants_before_cleanup")
                or result.get("descendants_after_cleanup")
            ):
                manifest["failures"].append(
                    f"{plan['id']}: failed game, incomplete resource scope or leaked process")
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
    result = {
        "schema_version": 1,
        "profile_id": "strength-s1-e01-g3-output-only-matched-control-v1",
        "passed": False, "errors": [], "claim_boundary": CLAIMS,
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
        "real_games": result.get("real_chess_games_verified", 0),
        "actual_g3_overrides": result.get("actual_g3_overrides"),
        "results": result.get("results"),
        "errors": result["errors"],
        "equal_compute": False, "elo": False, "superiority": False,
    }, sort_keys=True), flush=True)
    return 0 if result["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("contracts", "run", "verify"),
                        default="contracts")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    p = policy()
    if args.phase == "contracts":
        plans, _ = planned_games(p)
        print(json.dumps({
            "profile_id": p["profile_id"], "games": p["total_games"],
            "pairs": len(plans), "arms": list(ARMS),
            "strict_cgroup_v2": True, "claim_boundary": CLAIMS,
        }, sort_keys=True))
        return 0
    path = args.output or (
        ROOT_OUTPUT / campaign_identity(source_identity()))
    if args.phase == "run":
        return execute(path)
    return verify(path)


if __name__ == "__main__":
    raise SystemExit(main())
