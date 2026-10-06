#!/usr/bin/env python3
"""Extract deterministic candidate G3 witnesses from one cryptographically bound LOCAL-1 artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]

import sys
sys.path.insert(0, str(ROOT))

from controller.counterfactual import verify_counterfactual_integrity
from controller.final_decision import verify_final_decision_integrity
from controller.replay import verify_bundle_integrity
from controller.replay_history import archive_replay_scope


class ExtractionError(RuntimeError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ExtractionError(message)


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtractionError(f"{path}: cannot load JSON: {exc}") from exc
    require(isinstance(value, dict), f"{path}: JSON root must be an object")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_source_metadata(path: Path) -> dict:
    source = load(path)
    required = (
        "repository",
        "workflow_run",
        "artifact_id",
        "artifact_name",
        "artifact_sha256",
        "source_commit",
        "campaign_path",
        "campaign_policy_path",
        "campaign_policy_sha256",
    )
    for key in required:
        require(source.get(key) not in (None, ""), f"source metadata missing {key}")
    require(source.get("schema_version") == 1, "unsupported source-metadata schema")
    require(
        isinstance(source["artifact_sha256"], str)
        and len(source["artifact_sha256"]) == 64
        and all(ch in "0123456789abcdef" for ch in source["artifact_sha256"]),
        "artifact_sha256 must be lowercase SHA-256 hex",
    )
    require(
        isinstance(source["source_commit"], str) and len(source["source_commit"]) == 40,
        "source_commit must be a 40-character commit id",
    )
    return source


def safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    root = destination.resolve()
    seen: set[str] = set()
    total_size = 0
    try:
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                name = info.filename
                require(name and "\x00" not in name, "archive contains an invalid member name")
                require("\\" not in name, f"archive member uses backslash path separators: {name!r}")
                member = PurePosixPath(name)
                require(not member.is_absolute(), f"archive contains absolute path: {name!r}")
                require(".." not in member.parts, f"archive contains path traversal: {name!r}")
                normalized = str(member)
                require(normalized not in seen, f"archive contains duplicate member: {normalized!r}")
                seen.add(normalized)

                mode = (info.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(mode) if mode else 0
                require(
                    file_type in (0, stat.S_IFREG, stat.S_IFDIR),
                    f"archive contains symlink or special file: {name!r}",
                )
                total_size += int(info.file_size)
                require(total_size <= 2 * 1024**3, "archive exceeds extraction size limit")

                target = (destination / Path(*member.parts)).resolve()
                require(target.is_relative_to(root), f"archive member escapes extraction root: {name!r}")
                if info.is_dir() or file_type == stat.S_IFDIR:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(info, "r") as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
    except zipfile.BadZipFile as exc:
        raise ExtractionError(f"invalid artifact ZIP: {exc}") from exc


def verify_campaign(campaign: Path, source: dict) -> tuple[dict, dict, dict[str, list[dict]]]:
    require(campaign.is_dir(), f"campaign path is missing: {campaign}")
    manifest = load(campaign / "manifest.json")
    report = load(campaign / "report.json")

    require(manifest.get("status") == "completed", "retained campaign is not completed")
    require(report.get("passed") is True, "retained campaign did not pass")
    require(report.get("validated_games") == 28, "retained campaign did not validate 28 games")
    require(report.get("errors") == [], "retained campaign contains validation errors")
    require(report.get("execution_scope") == "required_local1", "retained campaign scope is not required_local1")
    require(
        manifest.get("campaign_id") == report.get("campaign_id"),
        "campaign manifest/report identity mismatch",
    )
    require(
        (manifest.get("source") or {}).get("commit") == source["source_commit"],
        "campaign manifest source commit does not match frozen source metadata",
    )
    for label, doc in (("manifest", manifest), ("report", report)):
        require(
            (doc.get("execution_domain") or {}).get("source_commit") == source["source_commit"],
            f"{label} execution-domain source commit mismatch",
        )
        require(
            (doc.get("candidate_bundle") or {}).get("source_commit") == source["source_commit"],
            f"{label} candidate-bundle source commit mismatch",
        )
    policy = manifest.get("policy") or {}
    require(policy.get("path") == source["campaign_policy_path"], "campaign policy path mismatch")
    require(policy.get("sha256") == source["campaign_policy_sha256"], "campaign policy digest mismatch")

    plies = report.get("plies")
    require(isinstance(plies, list) and plies, "campaign report has no validated plies")
    by_replay: dict[str, list[dict]] = defaultdict(list)
    for row in plies:
        replay_id = row.get("replay_id")
        if isinstance(replay_id, str) and replay_id:
            by_replay[replay_id].append(row)
    return manifest, report, by_replay


def verify_replay(run: Path) -> None:
    for label, verifier in (
        ("bundle", verify_bundle_integrity),
        ("final decision", verify_final_decision_integrity),
        ("counterfactual", verify_counterfactual_integrity),
    ):
        problems = verifier(run)
        require(not problems, f"{run.name}: {label} integrity failure: {problems}")


def collect_candidates(campaign: Path, report_rows: dict[str, list[dict]]) -> list[dict]:
    by: dict[str, list[dict]] = defaultdict(list)
    pattern = "base-*/sessions/allfather-g3/*/replays/*/decision/final.json"
    for final_path in sorted(campaign.glob(pattern)):
        run = final_path.parents[1]
        final_doc = load(final_path)
        decision = final_doc.get("decision") or {}
        if (
            decision.get("authority") != "HYBRID"
            or not decision.get("proposal_move")
            or decision.get("proposal_move") == decision.get("anchor_move")
        ):
            continue

        verify_replay(run)
        manifest = load(run / "manifest.json")
        route = load(run / "route.json")
        resource = load(run / "resource.json")
        decision = (load(run / "decision/final.json").get("decision") or {})

        run_id = manifest.get("run_id")
        require(isinstance(run_id, str) and run_id == run.name, f"{run}: run identity mismatch")
        rows = report_rows.get(run_id) or []
        require(len(rows) == 1, f"{run_id}: expected exactly one validated campaign ply")
        row = rows[0]

        job = run.relative_to(campaign).parts[0]
        position = manifest.get("position") or {}
        time_plan = manifest.get("time_plan") or {}
        snap = decision.get("authorization_snapshot") or {}
        manifest_sha = sha256_file(run / "manifest.json")
        require(
            row.get("job") == job
            and row.get("arm") == "allfather-g3"
            and row.get("authority") == "HYBRID"
            and row.get("authorization_granted") is True
            and row.get("authorized_non_anchor") is True
            and row.get("override") is True
            and row.get("resource_qualified") is True
            and row.get("anchor_move") == decision.get("anchor_move")
            and row.get("proposal_move") == decision.get("proposal_move")
            and row.get("move") == decision.get("emitted_move")
            and row.get("replay_manifest_sha256") == manifest_sha,
            f"{run_id}: retained replay disagrees with validated campaign ply",
        )

        qualified = (
            (decision.get("authorization") or {}).get("authorized") is True
            and snap.get("route_action") == "BUY_STAGED_VERIFY"
            and snap.get("route_buy_extension") is True
            and snap.get("staged_complete") is True
            and snap.get("terminal_source") == "staged_verification"
            and (manifest.get("clock_outcome") or {}).get("output_within_deadline") is True
            and resource.get("qualified") is True
            and (route.get("resource_measurement") or {}).get("qualified") is True
            and (route.get("envelope_claim") or {}).get("claimed") is True
            and str(position.get("command") or "").startswith("position startpos")
            and float(time_plan.get("available_clock_ms") or 0) >= 10000
        )
        if not qualified:
            continue

        by[job].append(
            {
                "id": f"discovery-{job}-g{int(manifest['generation']):06d}",
                "moves": " ".join(position.get("moves") or []),
                "command": (manifest.get("external_request") or {}).get("command"),
                "source_set": "discovery_local1_v1",
                "discovery": {
                    "job_id": job,
                    "generation": manifest["generation"],
                    "run_id": run_id,
                    "position_id": position["position_id"],
                    "anchor_move": decision["anchor_move"],
                    "proposal_move": decision["proposal_move"],
                    "available_clock_ms": time_plan["available_clock_ms"],
                    "hard_budget_ms": time_plan["hard_budget_ms"],
                    "output_within_deadline": True,
                },
            }
        )

    require(len(by) == 4, f"expected four override-bearing base jobs, found {sorted(by)}")
    selected: list[dict] = []
    seen: set[str] = set()
    for job in sorted(by):
        picked: list[dict] = []
        for row in sorted(
            by[job],
            key=lambda item: (
                item["discovery"]["generation"],
                item["discovery"]["run_id"],
            ),
        ):
            position_id = row["discovery"]["position_id"]
            if position_id in seen:
                continue
            picked.append(row)
            seen.add(position_id)
            if len(picked) == 4:
                break
        require(len(picked) == 4, f"{job}: expected four distinct eligible positions")
        selected.extend(picked)
    require(len(selected) == 16, "candidate witness selection did not produce 16 cases")
    return selected


def extract(artifact_zip: Path, source_metadata: Path, output: Path) -> dict:
    source = load_source_metadata(source_metadata)
    artifact_zip = artifact_zip.resolve()
    require(artifact_zip.is_file(), f"artifact ZIP is missing: {artifact_zip}")
    observed = sha256_file(artifact_zip)
    require(
        observed == source["artifact_sha256"],
        f"artifact digest mismatch: expected {source['artifact_sha256']}, observed {observed}",
    )

    campaign_rel = PurePosixPath(source["campaign_path"])
    require(not campaign_rel.is_absolute() and ".." not in campaign_rel.parts, "invalid campaign_path")
    with archive_replay_scope(artifact_zip):
        with tempfile.TemporaryDirectory(prefix="g3-witness-artifact-") as tmp:
            extracted = Path(tmp) / "artifact"
            safe_extract_zip(artifact_zip, extracted)
            campaign = extracted / Path(*campaign_rel.parts)
            _, _, report_rows = verify_campaign(campaign, source)
            selected = collect_candidates(campaign, report_rows)

    document = {
        "schema_version": 1,
        "corpus_id": "g3-candidate-local1-discovery-v1",
        "source": {
            "repository": source["repository"],
            "source_commit": source["source_commit"],
            "workflow_run": source["workflow_run"],
            "artifact_id": source["artifact_id"],
            "artifact_name": source["artifact_name"],
            "artifact_digest": "sha256:" + source["artifact_sha256"],
        },
        "selection_algorithm": {
            "id": "qualified-natural-override-base-round-robin-v1",
            "description": "From candidate LOCAL-1 base-* allfather-g3 replays, retain only valid non-anchor HYBRID decisions with granted authorization, BUY_STAGED_VERIFY, completed staged verification, claimed envelope, qualified resource evidence, output within deadline, startpos history, and available_clock_ms >= 10000. Deduplicate position_id, sort each job by (generation, run_id), sort job ids lexicographically, and take the earliest four distinct positions from each override-bearing base job. No validation-run result may alter this frozen set.",
            "required_per_job": 4,
            "minimum_available_clock_ms": 10000,
            "case_count": 16,
        },
        "claim_boundary": {
            "discovery_only": True,
            "validation_authority": False,
            "promotion_authority": False,
            "strength": False,
            "elo": False,
        },
        "cases": selected,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-zip", type=Path, required=True)
    parser.add_argument(
        "--source-metadata",
        type=Path,
        default=ROOT / "qualification/g3-candidate-discovery-source.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = extract(args.artifact_zip, args.source_metadata, args.output)
    print(
        json.dumps(
            {
                "cases": len(document["cases"]),
                "sha256": sha256_file(args.output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
