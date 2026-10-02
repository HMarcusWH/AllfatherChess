"""Execute the frozen J13 META-1 campaign when J12 host authority is qualified."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from tools.local_game.common import ROOT, file_record, load, require, save, sha, source_identity
from tools.local_game.runner import bounded
from .common import RUN_DISPOSITION, campaign_disposition, command, opening_blocks, policy, schedule
from .integrity import qualify


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=ROOT / "qualification/meta-1-v1.json")
    parser.add_argument("--j12-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "build/test-results/meta1/campaign")
    parser.add_argument("--fastchess", type=Path, default=ROOT / "build/tools/fastchess/bin/fastchess")
    args = parser.parse_args()

    require(
        args.policy.resolve() == (ROOT / "qualification/meta-1-v1.json").resolve(),
        "META-1 runner accepts only the frozen source-controlled policy",
    )
    p = policy(args.policy)
    output = args.output.resolve()
    require(output.is_relative_to((ROOT / "build").resolve()), "META-1 output must live under build/")
    require(not output.is_symlink(), "META-1 output cannot be a symlink")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    source = source_identity()
    j12 = load(args.j12_report)
    require(j12.get("source_commit") == source["commit"], "META-1 J12 prerequisite is stale")
    disposition = campaign_disposition(j12)
    plans = schedule(p)
    manifest = {
        "schema_version": 1,
        "campaign_id": output.name,
        "profile_id": p["profile_id"],
        "source": source,
        "policy": file_record(args.policy.resolve()),
        "opening_fixture": file_record(ROOT / p["opening_file"]),
        "source_runtime": file_record(ROOT / p["source_runtime"]),
        "j12_report": file_record(args.j12_report.resolve()),
        "qualification_disposition": disposition,
        "planned_blocks": plans,
        "jobs": [],
        "failures": [],
        "status": "gated" if disposition != RUN_DISPOSITION else "running",
    }
    save(output / "manifest.json", manifest)

    if disposition != RUN_DISPOSITION:
        report = {
            "schema_version": 1,
            "profile_id": p["profile_id"],
            "passed": True,
            "experiment_valid": False,
            "campaign_executed": False,
            "qualification_disposition": disposition,
            "games": 0,
            "opening_blocks": 0,
            "claim_boundary": p["claim_boundary"],
        }
        save(output / "report.json", report)
        print(json.dumps(report, sort_keys=True))
        return 0

    require(args.fastchess.is_file(), "pinned Fastchess binary is missing")
    source_runtime = load(ROOT / p["source_runtime"])
    blocks = opening_blocks(ROOT / p["opening_file"], p["opening_count"])

    for plan in plans:
        directory = output / plan["id"]
        directory.mkdir()
        opening_path = directory / plan["opening"]
        opening_path.write_text(blocks[plan["opening_index"]], encoding="utf-8")
        require(
            sha(opening_path) == plan["opening_sha256"],
            f"{plan['id']}: generated opening block differs from frozen fixture",
        )
        argv = command(plan, directory, p, source_runtime, args.fastchess, opening_path)
        execution = bounded(
            argv,
            ROOT,
            directory / "runner-stdout.log",
            int(p["job_timeout_s"]),
        )
        manifest["jobs"].append({"plan": plan, "execution": execution})
        if (
            execution["returncode"] != 0
            or execution["timed_out"]
            or execution.get("descendants_before_cleanup")
            or execution.get("descendants_after_cleanup")
        ):
            manifest["failures"].append(
                f"{plan['id']}: Fastchess or process cleanup failed"
            )
            break
        save(output / "manifest.json", manifest)

    manifest["status"] = (
        "completed"
        if len(manifest["jobs"]) == len(plans) and not manifest["failures"]
        else "failed"
    )
    save(output / "manifest.json", manifest)
    report = qualify(output)
    save(output / "report.json", report)
    print(json.dumps({
        "passed": report["passed"],
        "experiment_valid": report["experiment_valid"],
        "qualification_disposition": report["qualification_disposition"],
        "games": report.get("games_observed", 0),
        "interventions": report.get("hybrid_interventions", 0),
        "suppressed": report.get("control_suppressed_authorized_non_anchor", 0),
    }, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
