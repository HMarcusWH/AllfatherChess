"""Independent META-1 schedule/game/replay validation."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from tools.local_game.common import (
    ROOT,
    load,
    require,
    sha,
    source_identity,
    verify_record,
)
from tools.local_game.integrity import verify_builds
from tools.local_game.validate import match_game, read_games, session_games, verify_runner_log
from .common import (
    ARMS,
    RUN_DISPOSITION,
    campaign_disposition,
    command,
    opening_blocks,
    policy,
    schedule,
)
from .report import empty_scores, record_result


def qualify(output: Path) -> dict:
    output = Path(output)
    m = load(output / "manifest.json")
    p = policy(ROOT / "qualification/meta-1-v1.json")
    disposition = m.get("qualification_disposition")
    if disposition != RUN_DISPOSITION:
        return {
            "schema_version": 1,
            "profile_id": "meta-1-v1",
            "passed": disposition in (
                "NOT_QUALIFIED_HOST_CAPACITY",
                "NOT_QUALIFIED_POSITIVE_WITNESS",
            ),
            "experiment_valid": False,
            "campaign_executed": False,
            "qualification_disposition": disposition,
            "games_observed": 0,
            "opening_blocks_observed": 0,
            "claim_boundary": p["claim_boundary"],
        }

    errors: list[str] = []
    games_out = []
    plies = []
    scores = empty_scores(ARMS)
    try:
        require(m.get("source") == source_identity(), "META-1 source checkout drift")
        require(m.get("status") == "completed" and not m.get("failures"),
                "META-1 campaign did not complete")

        policy_path = verify_record(ROOT, m.get("policy") or {})
        require(
            policy_path.resolve()
            == (ROOT / "qualification/meta-1-v1.json").resolve(),
            "META-1 manifest is not bound to the frozen policy",
        )
        opening_fixture = verify_record(ROOT, m.get("opening_fixture") or {})
        require(
            opening_fixture.resolve() == (ROOT / p["opening_file"]).resolve(),
            "META-1 manifest is not bound to the frozen opening fixture",
        )
        runtime_path = verify_record(ROOT, m.get("source_runtime") or {})
        require(
            runtime_path.resolve() == (ROOT / p["source_runtime"]).resolve(),
            "META-1 manifest is not bound to the frozen J12 runtime",
        )
        j12_path = verify_record(ROOT, m.get("j12_report") or {})
        j12 = load(j12_path)
        require(
            j12.get("source_commit") == m["source"]["commit"],
            "META-1 J12 prerequisite source differs from campaign source",
        )
        require(
            campaign_disposition(j12) == RUN_DISPOSITION,
            "META-1 campaign executed without real J12 authority qualification",
        )

        expected = schedule(p)
        require(m.get("planned_blocks") == expected, "META-1 schedule drift")
        require(len(m.get("jobs") or []) == 50, "META-1 did not execute 50 blocks")
        require(sha(ROOT / p["opening_file"]) == p["opening_sha256"],
                "META-1 opening fixture changed")
        source = load(ROOT / p["source_runtime"])
        fastchess = verify_builds(m["source"], p)
        frozen_blocks = opening_blocks(
            ROOT / p["opening_file"],
            p["opening_count"],
        )
        campaign_run_ids: set[str] = set()
        campaign_manifest_hashes: set[str] = set()

        for index, job in enumerate(m["jobs"]):
            plan = job["plan"]
            execution = job["execution"]
            require(plan == expected[index], f"{plan.get('id')}: executed plan drift")
            directory = output / plan["id"]
            opening_path = directory / plan["opening"]
            require(
                opening_path.is_file()
                and sha(opening_path) == plan["opening_sha256"]
                and opening_path.read_text(encoding="utf-8")
                == frozen_blocks[plan["opening_index"]],
                f"{plan['id']}: per-block opening evidence differs from frozen fixture",
            )
            require(
                execution["argv"] == command(
                    plan, directory, p, source, fastchess, opening_path,
                    write_specs=False,
                ),
                f"{plan['id']}: Fastchess argv differs from frozen META-1 contract",
            )
            require(
                execution["returncode"] == 0
                and not execution["timed_out"]
                and not execution.get("descendants_before_cleanup")
                and not execution.get("descendants_after_cleanup"),
                f"{plan['id']}: execution/process cleanup failed",
            )
            verify_runner_log(directory / "runner.log", plan)
            games = read_games(directory / "games.pgn", require_completed=True)
            opening = read_games(opening_path)[0]
            require(len(games) == 2, f"{plan['id']}: expected exactly two games")

            by_arm = {}
            for arm in plan["arms"]:
                groups, summaries = session_games(
                    directory, arm, source, p["source_runtime"], plan,
                    campaign_run_ids, campaign_manifest_hashes,
                )
                require(len(groups) == 2, f"{plan['id']}/{arm}: expected two game traces")
                require(len(summaries) == 1, f"{plan['id']}/{arm}: process reuse drift")
                by_arm[arm] = groups

            for game_index, game in enumerate(games):
                expected_colors = (
                    plan["arms"] if game_index == 0 else list(reversed(plan["arms"]))
                )
                require(
                    [game.headers["White"], game.headers["Black"]] == expected_colors,
                    f"{plan['id']}: colors were not reversed",
                )
                result = game.headers["Result"]
                record_result(scores, game.headers["White"], game.headers["Black"], result)
                game_plies = match_game(
                    game,
                    {arm: by_arm[arm][game_index] for arm in plan["arms"]},
                    False,
                    opening,
                    plan,
                )
                for row in game_plies:
                    if row["arm"] == "allfather-anchor-control":
                        require(
                            row.get("authority") == "ANCHOR_CONTROL",
                            "META-1 control ply was not marked ANCHOR_CONTROL",
                        )
                    else:
                        require(
                            row.get("authority") in ("HYBRID", "ANCHOR_FALLBACK"),
                            "META-1 live ply has unsupported authority",
                        )
                plies.extend({
                    "block": plan["id"], "game": game_index, **row,
                } for row in game_plies)
                games_out.append({
                    "block": plan["id"],
                    "opening_index": plan["opening_index"],
                    "game": game_index,
                    "white": game.headers["White"],
                    "black": game.headers["Black"],
                    "result": result,
                })

        require(len(games_out) == 100, "META-1 must retain exactly 100 games")
        require(len({row["opening_index"] for row in games_out}) == 50,
                "META-1 did not retain all 50 opening blocks")
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")

    authority_counts = Counter(row.get("authority") for row in plies)
    hybrid_interventions = sum(bool(row.get("override")) for row in plies)
    authorized_non_anchor = sum(bool(row.get("authorized_non_anchor")) for row in plies)
    suppressed = sum(bool(row.get("suppressed_authorized_non_anchor")) for row in plies)
    grants_authorized = sum(int(row.get("work_grants_authorized") or 0) for row in plies)
    grants_settled = sum(int(row.get("work_grants_settled") or 0) for row in plies)
    return {
        "schema_version": 1,
        "profile_id": "meta-1-v1",
        "passed": not errors,
        "experiment_valid": not errors and len(games_out) == 100,
        "campaign_executed": True,
        "qualification_disposition": (
            "QUALIFIED_META1_CAMPAIGN" if not errors else "INVALID_EVIDENCE"
        ),
        "errors": errors,
        "games_observed": len(games_out),
        "opening_blocks_observed": len({row["opening_index"] for row in games_out}),
        "scores": scores,
        "authority_counts": dict(authority_counts),
        "authorized_non_anchor_proposals": authorized_non_anchor,
        "hybrid_interventions": hybrid_interventions,
        "control_suppressed_authorized_non_anchor": suppressed,
        "work_grants_authorized": grants_authorized,
        "work_grants_settled": grants_settled,
        "physical_cpu_ms_by_arm": {
            arm: round(sum(
                float(row.get("physical_cpu_ms") or 0.0)
                for row in plies if row.get("arm") == arm
            ), 3)
            for arm in ARMS
        },
        "claim_boundary": p["claim_boundary"],
    }
