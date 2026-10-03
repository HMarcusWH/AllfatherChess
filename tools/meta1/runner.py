"""Execute one immutable J13 META-1 attempt after J12 host qualification."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.engine_opt.domain import candidate_bundle_identity
from tools.local_game.common import (
    ROOT,
    file_record,
    load,
    require,
    save,
    sha,
    source_identity,
)
from tools.local_game.runner import bounded
from .common import (
    RUN_DISPOSITION,
    campaign_identity,
    command,
    opening_blocks,
    policy,
    schedule,
)
from .layout import ProducerLayout
from .preflight import (
    capture_postflight,
    validate_preflight_payload,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _producer_summary(manifest: dict) -> dict:
    return {
        "schema_version": 1,
        "profile_id": "meta-1-v1",
        "source": manifest["source"],
        "qualification_disposition": manifest["qualification_disposition"],
        "status": manifest["status"],
        "planned_blocks": len(manifest["planned_blocks"]),
        "blocks_executed": len(manifest["jobs"]),
        "failures": list(manifest["failures"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "qualification/meta-1-v1.json",
    )
    parser.add_argument("--j12-report", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--fastchess",
        type=Path,
        default=ROOT / "build/tools/fastchess/bin/fastchess",
    )
    args = parser.parse_args()

    require(
        args.policy.resolve()
        == (ROOT / "qualification/meta-1-v1.json").resolve(),
        "META-1 runner accepts only the frozen source-controlled policy",
    )
    p = policy(args.policy)
    source = source_identity()
    expected_id = campaign_identity(source)
    output = (
        (ROOT / "build/test-results/meta1" / expected_id)
        if args.output is None
        else args.output.resolve()
    )
    root = (ROOT / "build/test-results/meta1").resolve()
    require(
        output.parent.resolve() == root,
        "META-1 output must be one immutable attempt under build/test-results/meta1/",
    )
    if os.environ.get("GITHUB_ACTIONS") == "true":
        require(
            os.environ.get("GITHUB_REF") == "refs/heads/main",
            "confirmatory META-1 workflow_dispatch must execute merged main",
        )
        require(
            output.name == expected_id,
            "META-1 campaign id does not bind source/workflow attempt",
        )
    require(
        not output.exists() and not output.is_symlink(),
        "META-1 campaign attempt already exists; retries require a new workflow attempt",
    )

    j12_path = args.j12_report.resolve()
    preflight_path = args.preflight.resolve()
    j12 = load(j12_path)
    preflight = load(preflight_path)
    require(
        j12.get("source_commit") == source["commit"],
        "META-1 J12 prerequisite is stale",
    )
    bundle = candidate_bundle_identity(
        ROOT / p["bundle_root"],
        expected_source_commit=source["commit"],
    )
    fastchess_path = args.fastchess.resolve()
    layout = ProducerLayout.create(
        repo_root=ROOT.resolve(),
        campaign_root=output,
        fastchess=fastchess_path,
        python_executable=sys.executable,
        campaign_id=output.name,
    )
    live_host = validate_preflight_payload(
        preflight,
        source=source,
        p=p,
        j12=j12,
        candidate_bundle=bundle,
        expected_j12_report_sha256=sha(j12_path),
    )
    disposition = preflight["qualification_disposition"]
    output.mkdir(parents=True)
    plans = schedule(p)
    manifest = {
        "schema_version": 1,
        "campaign_id": output.name,
        "profile_id": p["profile_id"],
        "source": source,
        "producer_layout": layout.as_dict(),
        "campaign_attempt": {
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
            "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "started_at_utc": _utc_now(),
        },
        "policy": file_record(args.policy.resolve()),
        "opening_fixture": file_record(ROOT / p["opening_file"]),
        "source_runtime": file_record(ROOT / p["source_runtime"]),
        "j12_report": file_record(j12_path),
        "preflight": file_record(preflight_path),
        "preflight_host_capabilities_digest": live_host.digest,
        "preflight_qualification_domain_digest": live_host.qualification_domain_digest,
        "candidate_bundle": bundle,
        "qualification_disposition": disposition,
        "planned_blocks": plans,
        "jobs": [],
        "failures": [],
        "status": "gated" if disposition != RUN_DISPOSITION else "running",
    }
    save(output / "manifest.json", manifest)

    exit_code = 0
    try:
        if disposition == RUN_DISPOSITION:
            require(fastchess_path.is_file(), "pinned Fastchess binary is missing")
            source_runtime = load(ROOT / p["source_runtime"])
            blocks = opening_blocks(ROOT / p["opening_file"], p["opening_count"])
            for plan in plans:
                directory = output / plan["id"]
                directory.mkdir()
                opening_path = directory / plan["opening"]
                opening_path.write_text(
                    blocks[plan["opening_index"]],
                    encoding="utf-8",
                )
                require(
                    sha(opening_path) == plan["opening_sha256"],
                    f"{plan['id']}: generated opening block differs from frozen fixture",
                )
                argv = command(
                    plan,
                    directory,
                    p,
                    source_runtime,
                    Path(layout.fastchess),
                    opening_path,
                    root=Path(layout.repo_root),
                    python_executable=layout.python_executable,
                )
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
                if len(manifest["jobs"]) == len(plans)
                and not manifest["failures"]
                else "failed"
            )
            exit_code = 0 if manifest["status"] == "completed" else 1
    except Exception as exc:
        manifest["failures"].append(f"{type(exc).__name__}: {exc}")
        manifest["status"] = "failed"
        exit_code = 1
    finally:
        post_path = output / "host-postflight.json"
        try:
            capture_postflight(preflight_path, post_path)
            manifest["postflight"] = file_record(post_path)
        except Exception as exc:
            if post_path.is_file():
                manifest["postflight"] = file_record(post_path)
            manifest["failures"].append(
                f"postflight:{type(exc).__name__}: {exc}"
            )
            if disposition == RUN_DISPOSITION:
                manifest["status"] = "failed"
                exit_code = 1

        producer_path = output / "producer-summary.json"
        save(producer_path, _producer_summary(manifest))
        manifest["producer_summary"] = file_record(producer_path)
        save(output / "manifest.json", manifest)

    print(
        json.dumps(
            {
                "campaign_id": output.name,
                "status": manifest["status"],
                "qualification_disposition": disposition,
                "blocks_executed": len(manifest["jobs"]),
                "failures": manifest["failures"],
            },
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
