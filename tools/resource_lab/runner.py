"""Execute the exact-head J6 resource laboratory.

The runner records exactly one outcome for each predeclared attempt.  It never
retries a failed candidate and never promotes a profile.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.resource.linux_proc import LinuxProcProvider
from tests.harness.uci_session import UciSession
from tools.engine_opt.corpus import load_epd
from tools.engine_opt.domain import candidate_bundle_identity, load_execution_domain
from tools.engine_opt.report import sha256, source_identity

from .candidate_matrix import (
    Candidate,
    expand_candidates,
    expand_compositions,
    load_lab_spec,
)
from .compose import run_composition_batch, summarize_composition_interference
from .measure import parse_search_observation
from .pareto import build_pareto_report


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected object")
    return value


def run_isolated(
    *,
    candidate: Candidate,
    bundle_root: Path,
    case,
    repeat_index: int,
    deadline_ms: float,
) -> dict[str, Any]:
    row = {
        "schema_version": 1,
        "candidate_id": candidate.candidate_id,
        "candidate_digest": candidate.digest,
        "family": candidate.family,
        "variant": candidate.variant,
        "nodes": candidate.nodes,
        "repeat_index": repeat_index,
        "case_id": case.case_id,
        "attempt_index": 0,
    }
    session: UciSession | None = None
    try:
        session = UciSession(
            bundle_root / candidate.binary_relpath,
            cwd=ROOT,
            timeout=max(10.0, deadline_ms / 1000.0 + 5.0),
            args=list(candidate.args),
            environment=dict(candidate.environment),
            start_new_session=True,
        )
        session.start()
        session.configure(candidate.execution_options(bundle_root))
        session.new_game()
        warmup = None
        if candidate.warmup_nodes is not None:
            session.set_position({"startpos_moves": []})
            warm_lines = session.search_nodes(
                candidate.warmup_nodes,
                timeout=max(5.0, deadline_ms / 1000.0 + 2.0),
            )
            warmup = {
                "nodes": candidate.warmup_nodes,
                "bestmove_line": next(
                    (line for line in reversed(warm_lines) if line.startswith("bestmove ")),
                    None,
                ),
            }
            session.new_game()
        session.set_position({"fen": case.fen, "moves": []})
        if session.proc is None:
            raise RuntimeError("UCI process missing before measurement")
        provider = LinuxProcProvider()
        before = provider.snapshot(session.proc.pid)
        lines = session.search_nodes(
            candidate.nodes,
            timeout=max(5.0, deadline_ms / 1000.0 + 2.0),
        )
        after = provider.snapshot(session.proc.pid)
        delta = provider.delta(before, after)
        observation = parse_search_observation(
            lines,
            family=candidate.family,
            delta=delta,
        )
        row.update(
            {
                "status": "completed",
                "warmup": warmup,
                "measurement": observation.as_dict(),
                "error": None,
                "transcript": list(session.transcript),
            }
        )
    except Exception as exc:
        row.update(
            {
                "status": "error",
                "measurement": None,
                "error": f"{type(exc).__name__}: {exc}",
                "transcript": [] if session is None else list(session.transcript),
            }
        )
    finally:
        if session is not None:
            session.close()
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec",
        type=Path,
        default=ROOT / "qualification/resource-lab-v1.json",
    )
    parser.add_argument("--execution-domain", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "build/test-results/resource-lab-v1",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="contract smoke only: first case, one repeat, reference candidates",
    )
    args = parser.parse_args()

    spec_path = args.spec.resolve()
    spec = load_lab_spec(spec_path)
    source = source_identity(ROOT)
    execution_domain = load_execution_domain(
        args.execution_domain,
        expected_source_commit=source["commit"],
    )
    bundle_root = (
        args.bundle_root.resolve()
        if args.bundle_root is not None
        else (ROOT / spec.bundle_root).resolve()
    )
    candidate_bundle = candidate_bundle_identity(
        bundle_root,
        expected_source_commit=source["commit"],
    )
    build_manifest = load(bundle_root / "build-manifest.json")
    candidates = expand_candidates(spec, build_manifest)
    compositions = expand_compositions(spec, candidates)
    cases = load_epd(ROOT / spec.corpus)
    if len(cases) != spec.corpus_cases:
        raise SystemExit(
            f"resource lab corpus case count drift: {len(cases)} != {spec.corpus_cases}"
        )

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "lab_id": spec.lab_id,
        "source_commit": source["commit"],
        "source_tree": source["tree"],
        "lab_spec": {
            "path": str(spec_path.relative_to(ROOT) if ROOT in spec_path.parents else spec_path),
            "sha256": sha256(spec_path),
            "canonical_digest": spec.digest,
        },
        "corpus": {
            "path": spec.corpus,
            "sha256": sha256(ROOT / spec.corpus),
            "cases": list(case.case_id for case in cases),
        },
        "candidate_bundle": candidate_bundle,
        "execution_domain": execution_domain,
        "attempt_policy": spec.raw["attempt_policy"],
        "placement_mode": spec.raw["placement_mode"],
        "claim_boundary": dict(spec.raw["claim_boundary"]),
    }
    save(output / "manifest.json", manifest)
    save(
        output / "stage-a/candidates.json",
        {
            "schema_version": 1,
            "candidates": [candidate.as_dict() for candidate in candidates],
        },
    )
    save(
        output / "stage-b/compositions.json",
        {
            "schema_version": 1,
            "compositions": [composition.as_dict() for composition in compositions],
        },
    )

    selected_candidates = candidates
    selected_cases = cases
    repeats = range(spec.repeats)
    selected_compositions = compositions
    if args.quick:
        selected_candidates = tuple(candidate for candidate in candidates if candidate.reference)
        selected_cases = cases[:1]
        repeats = range(1)
        selected_compositions = compositions[:1]

    stage_a_rows: list[dict[str, Any]] = []
    for candidate in selected_candidates:
        for repeat_index in repeats:
            for case in selected_cases:
                row = run_isolated(
                    candidate=candidate,
                    bundle_root=bundle_root,
                    case=case,
                    repeat_index=repeat_index,
                    deadline_ms=float(spec.raw["isolated_deadline_ms"]),
                )
                stage_a_rows.append(row)
                print(
                    json.dumps(
                        {
                            "stage": "A",
                            "candidate": candidate.candidate_id,
                            "repeat": repeat_index,
                            "case": case.case_id,
                            "status": row["status"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
    save(output / "stage-a/raw/rows.json", stage_a_rows)

    if args.quick:
        pareto = {
            "schema_version": 1,
            "kind": "resource-lab-stage-a-pareto-quick",
            "lab_id": spec.lab_id,
            "quick": True,
            "claim_boundary": dict(spec.raw["claim_boundary"]),
        }
    else:
        pareto = build_pareto_report(
            spec=spec,
            candidates=candidates,
            rows=stage_a_rows,
            case_ids=tuple(case.case_id for case in cases),
        )
    save(output / "stage-a/pareto.json", pareto)

    candidate_map = {candidate.candidate_id: candidate for candidate in candidates}
    stage_b_rows: list[dict[str, Any]] = []
    for composition in selected_compositions:
        for repeat_index in repeats:
            for case in selected_cases:
                row = run_composition_batch(
                    composition=composition,
                    candidate_map=candidate_map,
                    bundle_root=bundle_root,
                    case=case,
                    repeat_index=repeat_index,
                    deadline_ms=float(spec.raw["composition_deadline_ms"]),
                )
                stage_b_rows.append(row)
                print(
                    json.dumps(
                        {
                            "stage": "B",
                            "composition": composition.composition_id,
                            "repeat": repeat_index,
                            "case": case.case_id,
                            "status": row["status"],
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
    save(output / "stage-b/raw/rows.json", stage_b_rows)
    save(
        output / "stage-b/summary.json",
        summarize_composition_interference(
            rows=stage_b_rows,
            isolated_rows=stage_a_rows,
        ),
    )
    save(
        output / "runner-report.json",
        {
            "schema_version": 1,
            "lab_id": spec.lab_id,
            "quick": bool(args.quick),
            "stage_a_attempts": len(stage_a_rows),
            "stage_a_errors": sum(row["status"] != "completed" for row in stage_a_rows),
            "stage_b_batches": len(stage_b_rows),
            "stage_b_errors": sum(row["status"] != "completed" for row in stage_b_rows),
            "promotion_ready": False,
            "promotion_requires": "J7 frozen profile selection",
            "claim_boundary": dict(spec.raw["claim_boundary"]),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
