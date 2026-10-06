#!/usr/bin/env python3
"""Freeze source-controlled v2 resource-profile evidence from one retained lab artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
METRICS = (
    "median_wall_ms",
    "p95_wall_ms",
    "median_cpu_ms",
    "p95_cpu_ms",
    "p95_vm_hwm_bytes",
)
AUTH = {"runtime_authority": False, "resource_authorization": False, "outward_move": False}
SOURCE_METADATA = ROOT / "qualification/resource-profile-evidence-v2-source.json"


class FreezeError(RuntimeError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise FreezeError(message)


def load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FreezeError(f"{path}: cannot load JSON: {exc}") from exc


def load_source_metadata(path: Path) -> dict[str, Any]:
    source = load(path)
    require(source.get("schema_version") == 1, "unsupported v2 source-metadata schema")
    require(
        source.get("repository") == "HMarcusWH/AllfatherChess",
        "v2 source metadata repository mismatch",
    )
    for key in (
        "workflow_run",
        "artifact_id",
        "artifact_name",
        "artifact_sha256",
        "source_commit",
        "source_tree",
        "lab_spec",
        "execution_domain",
        "candidate_build_manifest_sha256",
    ):
        require(source.get(key) not in (None, ""), f"v2 source metadata missing {key}")
    digest = source.get("artifact_sha256")
    require(
        isinstance(digest, str)
        and len(digest) == 64
        and all(ch in "0123456789abcdef" for ch in digest),
        "v2 source metadata artifact SHA is malformed",
    )
    return source


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    root = destination.resolve()
    seen: set[str] = set()
    total_size = 0
    try:
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                name = info.filename
                require(name and "\x00" not in name, "artifact has invalid member name")
                require("\\" not in name, f"artifact member uses backslashes: {name!r}")
                member = PurePosixPath(name)
                require(not member.is_absolute(), f"artifact has absolute path: {name!r}")
                require(".." not in member.parts, f"artifact has traversal path: {name!r}")
                normalized = str(member)
                require(normalized not in seen, f"artifact has duplicate member: {normalized!r}")
                seen.add(normalized)
                mode = (info.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(mode) if mode else 0
                require(
                    file_type in (0, stat.S_IFREG, stat.S_IFDIR),
                    f"artifact has symlink or special file: {name!r}",
                )
                total_size += int(info.file_size)
                require(total_size <= 2 * 1024**3, "artifact exceeds extraction size limit")
                target = (destination / Path(*member.parts)).resolve()
                require(target.is_relative_to(root), f"artifact member escapes root: {name!r}")
                if info.is_dir() or file_type == stat.S_IFDIR:
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(info, "r") as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
    except zipfile.BadZipFile as exc:
        raise FreezeError(f"invalid artifact ZIP: {exc}") from exc


def vector_digest(summary: dict[str, Any]) -> str:
    vectors = summary.get("bestmove_vectors")
    require(isinstance(vectors, list) and vectors, "candidate summary lacks bestmove vectors")
    vector = vectors[0]
    require(isinstance(vector, list) and vector, "candidate bestmove vector malformed")
    raw = json.dumps(vector, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def metric(summary: dict[str, Any], name: str) -> float:
    if name == "median_wall_ms":
        return float(summary["wall_ms"]["median"])
    if name == "p95_wall_ms":
        return float(summary["wall_ms"]["p95"])
    if name == "median_cpu_ms":
        return float(summary["cpu_ms"]["median"])
    if name == "p95_cpu_ms":
        return float(summary["cpu_ms"]["p95"])
    if name == "p95_vm_hwm_bytes":
        return float(summary["vm_hwm_bytes"]["p95"])
    raise FreezeError(f"unknown metric: {name}")


def option_delta(candidate: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    options = candidate.get("options")
    base = reference.get("options")
    require(isinstance(options, dict) and isinstance(base, dict), "candidate options missing")
    return {
        name: value
        for name, value in sorted(options.items())
        if base.get(name) != value
    }


def locate_lab_root(extracted: Path) -> Path:
    matches = sorted(extracted.glob("**/resource-lab-v2/report.json"))
    require(len(matches) == 1, f"expected exactly one resource-lab-v2 report, got {len(matches)}")
    return matches[0].parent


def build_documents(
    *,
    archive: Path,
    workflow_run: int,
    artifact_id: int,
    evidence_path: str,
    source_metadata: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact_sha = sha256_file(archive)
    require(
        workflow_run == source_metadata["workflow_run"]
        and artifact_id == source_metadata["artifact_id"],
        "workflow/artifact id differs from frozen v2 source metadata",
    )
    require(
        artifact_sha == source_metadata["artifact_sha256"],
        "artifact ZIP SHA-256 differs from frozen v2 source metadata",
    )
    with tempfile.TemporaryDirectory() as tmp:
        extracted = Path(tmp) / "artifact"
        safe_extract_zip(archive, extracted)
        lab = locate_lab_root(extracted)

        manifest = load(lab / "manifest.json")
        report = load(lab / "report.json")
        candidates_doc = load(lab / "stage-a/candidates.json")
        pareto = load(lab / "stage-a/pareto.json")
        compositions_doc = load(lab / "stage-b/compositions.json")
        stage_b_summary = load(lab / "stage-b/summary.json")

        require(manifest.get("lab_id") == "resource-lab-v2", "artifact lab id is not v2")
        require(
            manifest.get("source_commit") == source_metadata["source_commit"]
            and manifest.get("source_tree") == source_metadata["source_tree"],
            "artifact source commit/tree differs from frozen v2 source metadata",
        )
        require(
            manifest.get("lab_spec") == source_metadata["lab_spec"],
            "artifact lab-spec identity differs from frozen v2 source metadata",
        )
        artifact_domain = manifest.get("execution_domain") or {}
        require(
            {
                "id": artifact_domain.get("execution_domain_id"),
                "digest": artifact_domain.get("execution_domain_digest"),
                "binding_scope": artifact_domain.get("binding_scope"),
            }
            == source_metadata["execution_domain"],
            "artifact execution domain differs from frozen v2 source metadata",
        )
        require(
            (manifest.get("candidate_bundle") or {}).get("build_manifest_sha256")
            == source_metadata["candidate_build_manifest_sha256"],
            "artifact candidate build manifest differs from frozen v2 source metadata",
        )
        require(report.get("profile_id") == "resource-lab-v2-report", "wrong v2 report id")
        require(report.get("evidence_valid") is True, "resource-lab-v2 evidence invalid")
        require(report.get("lab_complete") is True, "resource-lab-v2 did not complete")
        require(report.get("stage_a_error_measurements") == 0, "Stage-A contains errors")
        require(report.get("stage_b_error_batches") == 0, "Stage-B contains errors")
        require(report.get("stage_a_promotion_blocked_groups") == 0, "one or more resource groups are not promotable")
        require(report.get("reference_native_work_blockers") == [], "reference native-work blockers retained")

        source_commit = manifest.get("source_commit")
        source_tree = manifest.get("source_tree")
        require(isinstance(source_commit, str) and len(source_commit) == 40, "source commit missing")
        require(isinstance(source_tree, str) and len(source_tree) == 40, "source tree missing")
        require(report.get("source_commit") == source_commit, "report/manifest source mismatch")

        domain = manifest.get("execution_domain") or {}
        require(report.get("execution_domain_id") == domain.get("execution_domain_id"), "execution-domain id mismatch")
        require(report.get("execution_domain_digest") == domain.get("execution_domain_digest"), "execution-domain digest mismatch")

        candidates = candidates_doc.get("candidates")
        require(isinstance(candidates, list) and len(candidates) == report.get("stage_a_candidates"), "candidate matrix count mismatch")
        by_id = {row.get("candidate_id"): row for row in candidates}
        require(len(by_id) == len(candidates) and None not in by_id, "candidate ids missing/duplicate")

        groups = pareto.get("groups")
        summaries = pareto.get("candidate_summaries")
        require(isinstance(groups, list) and len(groups) == 9, "expected nine resource groups")
        require(isinstance(summaries, dict), "candidate summaries missing")

        reference_candidates: list[dict[str, Any]] = []
        frozen_groups: list[dict[str, Any]] = []
        pareto_ids: set[str] = set()
        for group in groups:
            require(group.get("promotion_eligible") is True, f"group not promotion-eligible: {group.get('family')}/n{group.get('nodes')}")
            require(group.get("promotion_blockers") == [], "eligible group carries promotion blockers")
            ids = group.get("pareto")
            require(isinstance(ids, list) and ids, "eligible group has empty Pareto set")
            reference_id = group.get("reference_candidate_id")
            require(reference_id in by_id and reference_id in summaries, "reference candidate missing")
            ref_summary = summaries[reference_id]
            reference_candidates.append({
                "family": group["family"],
                "nodes": group["nodes"],
                "id": reference_id,
                "digest": by_id[reference_id]["candidate_digest"],
                "bestmove_digest": vector_digest(ref_summary),
            })
            frozen_groups.append({
                "family": group["family"],
                "nodes": group["nodes"],
                "reference_id": reference_id,
                "pareto": sorted(ids),
            })
            pareto_ids.update(ids)

        pareto_candidates: list[dict[str, Any]] = []
        for candidate_id in sorted(pareto_ids):
            require(candidate_id in by_id and candidate_id in summaries, f"Pareto candidate missing: {candidate_id}")
            candidate = by_id[candidate_id]
            summary = summaries[candidate_id]
            key = (candidate["family"], candidate["nodes"])
            reference_id = next(
                row["reference_id"]
                for row in frozen_groups
                if (row["family"], row["nodes"]) == key
            )
            reference = by_id[reference_id]
            flags = [
                summary.get("complete") is True,
                summary.get("bestmove_repeatable") is True,
                summary.get("native_work_repeatable") is True,
                isinstance(summary.get("cpu_measurement_quality"), dict)
                and summary["cpu_measurement_quality"].get("usable") is True,
                summary.get("process_cpu_scope_complete") is True,
            ]
            require(all(flags), f"Pareto candidate lacks promotion gates: {candidate_id}")
            pareto_candidates.append({
                "id": candidate_id,
                "digest": candidate["candidate_digest"],
                "family": candidate["family"],
                "nodes": candidate["nodes"],
                "variant": candidate["variant"],
                "reference": candidate.get("reference") is True,
                "delta": option_delta(candidate, reference),
                "bestmove_digest": vector_digest(summary),
                "flags": flags,
                "metrics": [metric(summary, name) for name in METRICS],
            })

        by_pareto = {row["id"]: row for row in pareto_candidates}
        selections: list[dict[str, Any]] = []
        for group in sorted(frozen_groups, key=lambda row: (row["family"], row["nodes"])):
            ids = group["pareto"]
            chosen_id = min(
                ids,
                key=lambda cid: tuple(by_pareto[cid]["metrics"]) + (cid,),
            )
            chosen = by_pareto[chosen_id]
            selections.append({
                "family": group["family"],
                "nodes": group["nodes"],
                "candidate_id": chosen_id,
                "candidate_digest": chosen["digest"],
                "variant": chosen["variant"],
                "reference_candidate_id": group["reference_id"],
                "option_overrides": chosen["delta"],
                "selection_key": {
                    name: chosen["metrics"][index]
                    for index, name in enumerate(METRICS)
                },
                "selection_status": "isolated_resource_profile",
                "composition_qualification": "not_established",
            })

        ref_manifest = manifest.get("reference_contract") or {}
        reference_contract: dict[str, Any] = {}
        for key in ("selection", "runtime", "build_policy"):
            row = ref_manifest.get(key)
            require(isinstance(row, dict), f"reference contract missing {key}")
            reference_contract[f"{key}_path"] = row.get("path")
            reference_contract[f"{key}_sha256"] = row.get("sha256")

        compositions = compositions_doc.get("compositions")
        require(isinstance(compositions, list), "Stage-B composition matrix missing")
        composition_members = {
            row["composition_id"]: row["members"]
            for row in compositions
        }

        selection_contract = {
            "dimensions": list(METRICS),
            "require_bestmove_reference_match": True,
            "require_repeatable_bestmove": True,
            "require_repeatable_native_work": True,
            "require_usable_cpu": True,
            "require_complete_process_cpu_scope": True,
            "tie_break": "lexicographic-v1",
            "tie_break_order": [*METRICS, "candidate_id"],
        }

        evidence = {
            "schema_version": 1,
            "evidence_id": "resource-profile-evidence-v2",
            "source": {
                "lab_id": "resource-lab-v2",
                "source_commit": source_commit,
                "source_tree": source_tree,
                "workflow_run": workflow_run,
                "artifact_id": artifact_id,
                "artifact_sha256": artifact_sha,
                "candidate_bundle": manifest.get("candidate_bundle"),
                "lab_spec": manifest.get("lab_spec"),
                "corpus": manifest.get("corpus"),
                "artifact_members": {
                    "manifest_sha256": sha256_file(lab / "manifest.json"),
                    "report_sha256": sha256_file(lab / "report.json"),
                    "stage_a_candidates_sha256": sha256_file(lab / "stage-a/candidates.json"),
                    "stage_a_pareto_sha256": sha256_file(lab / "stage-a/pareto.json"),
                    "stage_b_compositions_sha256": sha256_file(lab / "stage-b/compositions.json"),
                    "stage_b_summary_sha256": sha256_file(lab / "stage-b/summary.json"),
                },
                "reference_contract": reference_contract,
            },
            "execution_domain": {
                "id": domain.get("execution_domain_id"),
                "digest": domain.get("execution_domain_digest"),
                "content_sha256": domain.get("content_sha256"),
                "binding_scope": domain.get("binding_scope"),
            },
            "selection_contract": selection_contract,
            "stage_a": {
                "candidates": report.get("stage_a_candidates"),
                "expected_measurements": report.get("stage_a_expected_measurements"),
                "completed_measurements": report.get("stage_a_completed_measurements"),
                "errors": report.get("stage_a_error_measurements"),
                "pareto_groups": len(groups),
                "pareto_candidates": len(pareto_candidates),
            },
            "stage_b": {
                "compositions": report.get("stage_b_compositions"),
                "expected_batches": report.get("stage_b_expected_batches"),
                "completed_batches": report.get("stage_b_completed_batches"),
                "errors": report.get("stage_b_error_batches"),
                "composition_ids": [row["composition_id"] for row in compositions],
                "members": composition_members,
                "summary_sha256": sha256_file(lab / "stage-b/summary.json"),
            },
            "reference_candidates": sorted(reference_candidates, key=lambda row: (row["family"], row["nodes"])),
            "pareto_candidates": pareto_candidates,
            "groups": sorted(frozen_groups, key=lambda row: (row["family"], row["nodes"])),
            "authority": AUTH,
            "claim_boundary": {
                "isolated_resource_measurement": True,
                "profile_selection_freeze": True,
                "composition_qualification": False,
                "runtime_profile_selection": False,
                "deployment": False,
                "strength": False,
                "elo": False,
                "equal_compute": False,
            },
        }

        evidence_bytes = (
            json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        selection = {
            "schema_version": 1,
            "selection_id": "resource-profile-selection-v2",
            "evidence_path": evidence_path,
            "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            "source_commit": source_commit,
            "source_tree": source_tree,
            "execution_domain": {
                "id": domain.get("execution_domain_id"),
                "digest": domain.get("execution_domain_digest"),
                "binding_scope": domain.get("binding_scope"),
            },
            "selection_rule": selection_contract,
            "selections": selections,
            "authority": AUTH,
            "claim_boundary": {
                "isolated_resource_profile_selection": True,
                "composition_qualification": False,
                "runtime_profile_selection": False,
                "adaptive_allocation": False,
                "deployment": False,
                "strength": False,
                "elo": False,
                "equal_compute": False,
            },
        }
        return evidence, selection


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-zip", type=Path, required=True)
    parser.add_argument("--workflow-run", type=int, required=True)
    parser.add_argument("--artifact-id", type=int, required=True)
    parser.add_argument(
        "--source-metadata",
        type=Path,
        default=SOURCE_METADATA,
    )
    parser.add_argument(
        "--evidence-output",
        type=Path,
        default=ROOT / "qualification/resource-profile-evidence-v2.json",
    )
    parser.add_argument(
        "--selection-output",
        type=Path,
        default=ROOT / "qualification/resource-profile-selection-v2.json",
    )
    args = parser.parse_args()

    evidence_rel = (
        str(args.evidence_output.resolve().relative_to(ROOT))
        if args.evidence_output.resolve().is_relative_to(ROOT)
        else str(args.evidence_output)
    )
    source_metadata = load_source_metadata(args.source_metadata.resolve())
    evidence, selection = build_documents(
        archive=args.artifact_zip.resolve(),
        workflow_run=args.workflow_run,
        artifact_id=args.artifact_id,
        evidence_path=evidence_rel,
        source_metadata=source_metadata,
    )
    args.evidence_output.parent.mkdir(parents=True, exist_ok=True)
    args.selection_output.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    args.selection_output.write_text(
        json.dumps(selection, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "evidence": str(args.evidence_output),
        "selection": str(args.selection_output),
        "source_commit": evidence["source"]["source_commit"],
        "pareto_candidates": evidence["stage_a"]["pareto_candidates"],
        "selections": {
            f"{row['family']}/n{row['nodes']}": row["variant"]
            for row in selection["selections"]
        },
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
