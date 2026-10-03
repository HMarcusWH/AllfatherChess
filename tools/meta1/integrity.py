"""Independent META-1 evidence reconstruction and paired result validation."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from tools.engine_opt.domain import candidate_bundle_identity
from tools.local_game.common import (
    ROOT,
    load,
    require,
    sha,
    source_identity,
    verify_record,
)
from tools.local_game.integrity import verify_builds
from tools.local_game.validate import (
    match_game,
    read_games,
    session_games,
    verify_runner_log,
)
from .common import (
    ARMS,
    RUN_DISPOSITION,
    campaign_disposition,
    campaign_identity,
    command,
    opening_blocks,
    policy,
    schedule,
)
from .layout import ProducerLayout
from .paired import arm_telemetry, paired_blocks
from .preflight import (
    validate_postflight_payload,
    validate_preflight_payload,
)
from .report import empty_scores, record_result


def _verify_producer_summary(manifest: dict, path: Path) -> dict:
    producer = load(path)
    expected = {
        "schema_version": 1,
        "profile_id": "meta-1-v1",
        "source": manifest["source"],
        "qualification_disposition": manifest["qualification_disposition"],
        "status": manifest["status"],
        "planned_blocks": len(manifest["planned_blocks"]),
        "blocks_executed": len(manifest["jobs"]),
        "failures": list(manifest["failures"]),
    }
    require(producer == expected, "META-1 producer summary disagrees with manifest")
    return producer


def verify_common_evidence(output: Path) -> dict:
    output = Path(output)
    manifest = load(output / "manifest.json")
    require(
        manifest.get("campaign_id") == output.name,
        "META-1 campaign id differs from its directory",
    )
    source = source_identity()
    require(manifest.get("source") == source, "META-1 source checkout drift")
    producer_layout = ProducerLayout.from_dict(
        manifest.get("producer_layout"),
        campaign_id=output.name,
    )
    attempt = manifest.get("campaign_attempt")
    require(isinstance(attempt, dict), "META-1 campaign attempt identity missing")
    if __import__("os").environ.get("GITHUB_ACTIONS") == "true":
        env = __import__("os").environ
        require(env.get("GITHUB_REF") == "refs/heads/main",
                "META-1 independent qualification is not running on main")
        require(output.name == campaign_identity(source),
                "META-1 campaign directory does not bind source/workflow attempt")
        require(
            attempt.get("workflow_run_id") == env.get("GITHUB_RUN_ID")
            and attempt.get("workflow_run_attempt") == env.get("GITHUB_RUN_ATTEMPT"),
            "META-1 manifest workflow attempt identity drift",
        )

    policy_path = verify_record(ROOT, manifest.get("policy") or {})
    require(
        policy_path.resolve()
        == (ROOT / "qualification/meta-1-v1.json").resolve(),
        "META-1 manifest is not bound to the frozen policy",
    )
    p = policy(policy_path)

    opening_fixture = verify_record(ROOT, manifest.get("opening_fixture") or {})
    require(
        opening_fixture.resolve() == (ROOT / p["opening_file"]).resolve(),
        "META-1 manifest is not bound to the frozen opening fixture",
    )
    runtime_path = verify_record(ROOT, manifest.get("source_runtime") or {})
    require(
        runtime_path.resolve() == (ROOT / p["source_runtime"]).resolve(),
        "META-1 manifest is not bound to the frozen J12 runtime",
    )

    j12_path = verify_record(ROOT, manifest.get("j12_report") or {})
    j12 = load(j12_path)
    require(
        j12.get("source_commit") == source["commit"],
        "META-1 J12 prerequisite source differs from campaign source",
    )

    fastchess = verify_builds(source, p)
    bundle = candidate_bundle_identity(
        ROOT / p["bundle_root"],
        expected_source_commit=source["commit"],
    )
    require(
        manifest.get("candidate_bundle") == bundle,
        "META-1 candidate bundle identity drift",
    )

    preflight_path = verify_record(ROOT, manifest.get("preflight") or {})
    preflight = load(preflight_path)
    require(
        preflight.get("j12_report_sha256") == sha(j12_path),
        "META-1 preflight does not bind the exact J12 report bytes",
    )
    pre_host = validate_preflight_payload(
        preflight,
        source=source,
        p=p,
        j12=j12,
        candidate_bundle=bundle,
        expected_j12_report_sha256=sha(j12_path),
    )
    derived_disposition = campaign_disposition(j12)
    require(
        manifest.get("qualification_disposition") == derived_disposition,
        "META-1 producer disposition disagrees with independently reconstructed J12 evidence",
    )
    require(
        manifest.get("preflight_host_capabilities_digest") == pre_host.digest,
        "META-1 manifest preflight host digest drift",
    )
    require(
        manifest.get("preflight_qualification_domain_digest")
        == pre_host.qualification_domain_digest,
        "META-1 manifest preflight domain digest drift",
    )

    postflight_path = verify_record(ROOT, manifest.get("postflight") or {})
    postflight = load(postflight_path)
    require(
        postflight.get("preflight_sha256") == sha(preflight_path),
        "META-1 postflight does not bind the exact preflight bytes",
    )
    post_host = validate_postflight_payload(
        postflight,
        source=source,
        preflight=preflight,
        require_stable=derived_disposition == RUN_DISPOSITION,
        expected_preflight_sha256=sha(preflight_path),
    )

    producer_path = verify_record(ROOT, manifest.get("producer_summary") or {})
    producer = _verify_producer_summary(manifest, producer_path)

    expected = schedule(p)
    require(
        manifest.get("planned_blocks") == expected,
        "META-1 frozen schedule drift",
    )
    require(
        sha(ROOT / p["opening_file"]) == p["opening_sha256"],
        "META-1 opening fixture changed",
    )
    return {
        "manifest": manifest,
        "producer_layout": producer_layout,
        "policy": p,
        "source": source,
        "j12": j12,
        "preflight": preflight,
        "postflight": postflight,
        "pre_host": pre_host,
        "post_host": post_host,
        "fastchess": fastchess,
        "bundle": bundle,
        "expected": expected,
        "producer": producer,
        "disposition": derived_disposition,
    }


def qualify(output: Path) -> dict:
    output = Path(output)
    games_out: list[dict] = []
    plies: list[dict] = []
    scores = empty_scores(ARMS)
    errors: list[str] = []
    disposition = "INVALID_EVIDENCE"
    claim_boundary = {}
    campaign_executed = False
    try:
        common = verify_common_evidence(output)
        m = common["manifest"]
        p = common["policy"]
        disposition = common["disposition"]
        claim_boundary = p["claim_boundary"]

        if disposition != RUN_DISPOSITION:
            require(m.get("status") == "gated", "non-qualified META-1 attempt is not gated")
            require(not m.get("jobs"), "non-qualified META-1 attempt played games")
            require(not m.get("failures"), "gated META-1 attempt contains failures")
            return {
                "schema_version": 1,
                "profile_id": "meta-1-v1",
                "passed": True,
                "experiment_valid": False,
                "campaign_executed": False,
                "qualification_disposition": disposition,
                "games_observed": 0,
                "opening_blocks_observed": 0,
                "errors": [],
                "claim_boundary": claim_boundary,
                "source": common["source"],
                "candidate_bundle": common["bundle"],
                "preflight_host_capabilities_digest": common["pre_host"].digest,
                "postflight_host_capabilities_digest": common["post_host"].digest,
            }

        campaign_executed = True
        require(
            m.get("status") == "completed" and not m.get("failures"),
            "META-1 campaign did not complete cleanly",
        )
        require(len(m.get("jobs") or []) == 50, "META-1 did not execute 50 blocks")
        source_runtime = load(ROOT / p["source_runtime"])
        frozen_blocks = opening_blocks(
            ROOT / p["opening_file"],
            p["opening_count"],
        )
        campaign_run_ids: set[str] = set()
        campaign_manifest_hashes: set[str] = set()

        for index, job in enumerate(m["jobs"]):
            plan = job["plan"]
            execution = job["execution"]
            require(
                plan == common["expected"][index],
                f"{plan.get('id')}: executed plan drift",
            )
            directory = output / plan["id"]
            opening_path = directory / plan["opening"]
            producer_directory = common["producer_layout"].block_directory(plan["id"])
            producer_opening_path = producer_directory / plan["opening"]
            require(
                opening_path.is_file()
                and sha(opening_path) == plan["opening_sha256"]
                and opening_path.read_text(encoding="utf-8")
                == frozen_blocks[plan["opening_index"]],
                f"{plan['id']}: per-block opening evidence differs from frozen fixture",
            )
            require(
                execution["argv"]
                == command(
                    plan,
                    producer_directory,
                    p,
                    source_runtime,
                    Path(common["producer_layout"].fastchess),
                    producer_opening_path,
                    write_specs=False,
                    root=Path(common["producer_layout"].repo_root),
                    python_executable=common["producer_layout"].python_executable,
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
                    directory,
                    arm,
                    source_runtime,
                    p["source_runtime"],
                    plan,
                    campaign_run_ids,
                    campaign_manifest_hashes,
                    producer_root=Path(common["producer_layout"].repo_root),
                    producer_directory=producer_directory,
                    producer_python_executable=common["producer_layout"].python_executable,
                )
                require(
                    len(groups) == 2,
                    f"{plan['id']}/{arm}: expected two game traces",
                )
                require(
                    len(summaries) == 1,
                    f"{plan['id']}/{arm}: process reuse drift",
                )
                by_arm[arm] = groups

            for game_index, game in enumerate(games):
                expected_colors = (
                    plan["arms"]
                    if game_index == 0
                    else list(reversed(plan["arms"]))
                )
                require(
                    [game.headers["White"], game.headers["Black"]]
                    == expected_colors,
                    f"{plan['id']}: colors were not reversed",
                )
                result = game.headers["Result"]
                record_result(
                    scores,
                    game.headers["White"],
                    game.headers["Black"],
                    result,
                )
                game_plies = match_game(
                    game,
                    {
                        arm: by_arm[arm][game_index]
                        for arm in plan["arms"]
                    },
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
                            row.get("authority")
                            in ("HYBRID", "ANCHOR_FALLBACK"),
                            "META-1 live ply has unsupported authority",
                        )
                plies.extend(
                    {
                        "block": plan["id"],
                        "game": game_index,
                        **row,
                    }
                    for row in game_plies
                )
                games_out.append(
                    {
                        "block": plan["id"],
                        "opening_index": plan["opening_index"],
                        "game": game_index,
                        "white": game.headers["White"],
                        "black": game.headers["Black"],
                        "result": result,
                    }
                )

        require(len(games_out) == 100, "META-1 must retain exactly 100 games")
        require(
            len({row["opening_index"] for row in games_out}) == 50,
            "META-1 did not retain all 50 opening blocks",
        )
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")

    paired = []
    paired_summary = None
    telemetry = {}
    if not errors and campaign_executed:
        try:
            paired, paired_summary = paired_blocks(games_out, plies)
            telemetry = arm_telemetry(plies)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

    authority_counts = Counter(row.get("authority") for row in plies)
    return {
        "schema_version": 1,
        "profile_id": "meta-1-v1",
        "passed": not errors,
        "experiment_valid": not errors
        and campaign_executed
        and len(games_out) == 100,
        "campaign_executed": campaign_executed,
        "qualification_disposition": (
            "QUALIFIED_META1_CAMPAIGN"
            if not errors and campaign_executed
            else ("INVALID_EVIDENCE" if errors else disposition)
        ),
        "errors": errors,
        "games_observed": len(games_out),
        "opening_blocks_observed": len(
            {row["opening_index"] for row in games_out}
        ),
        "scores": scores,
        "paired_blocks": paired,
        "paired_summary": paired_summary,
        "authority_counts": dict(authority_counts),
        "arm_telemetry": telemetry,
        "authorized_non_anchor_proposals": sum(
            bool(row.get("authorized_non_anchor")) for row in plies
        ),
        "hybrid_interventions": sum(bool(row.get("override")) for row in plies),
        "control_suppressed_authorized_non_anchor": sum(
            bool(row.get("suppressed_authorized_non_anchor"))
            for row in plies
        ),
        "work_grants_authorized": sum(
            int(row.get("work_grants_authorized") or 0) for row in plies
        ),
        "work_grants_settled": sum(
            int(row.get("work_grants_settled") or 0) for row in plies
        ),
        "claim_boundary": claim_boundary,
    }
