"""Execute the exact-head J6 resource laboratory.

Every predeclared attempt produces exactly one retained outcome. Failures are
evidence; the runner never retries and never promotes profiles.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.resource.linux_affinity import LinuxAffinityProvider
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
    reference_contract_manifest,
    stage_a_attempt_plan,
    stage_b_attempt_plan,
    validate_reference_contract,
)
from .compose import run_composition_batch, summarize_composition_interference
from .measure import (
    classify_failure,
    parse_search_observation,
    physical_primitives,
    reconstruct_physical_measurement,
)
from .observe import observe_affinity, process_cpu_scope
from .pareto import build_pareto_report
from .process_cpu import ProcessCpuClock


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
    clock_ticks_per_second: int,
    required_cpu_method: str,
    max_cpu_resolution_ns: int,
    affinity_max_attempts: int,
    block_index: int,
    order_index: int,
    attempt_ordinal: int,
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
        "block_index": block_index,
        "order_index": order_index,
        "attempt_ordinal": attempt_ordinal,
    }
    session: UciSession | None = None
    fault_stage = "process_start"
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

        fault_stage = "engine_configure"
        session.configure(candidate.execution_options(bundle_root))
        session.new_game()

        warmup = None
        if candidate.warmup_nodes is not None:
            fault_stage = "warmup"
            session.set_position({"startpos_moves": []})
            warm_lines = session.search_nodes(
                candidate.warmup_nodes,
                timeout=max(5.0, deadline_ms / 1000.0 + 2.0),
            )
            warmup = {
                "nodes": candidate.warmup_nodes,
                "bestmove_line": next(
                    (
                        line
                        for line in reversed(warm_lines)
                        if line.startswith("bestmove ")
                    ),
                    None,
                ),
            }
            session.new_game()

        fault_stage = "engine_configure"
        session.set_position({"fen": case.fen, "moves": []})
        if session.proc is None:
            raise RuntimeError("UCI process missing before measurement")

        pid = session.proc.pid
        proc = LinuxProcProvider(clock_ticks=clock_ticks_per_second)
        affinity = LinuxAffinityProvider()

        # Bind the external process CPU clock to an already-proven Linux
        # process identity before starting the measured interval.
        fault_stage = "physical_snapshot"
        identity_binding = proc.snapshot(pid)

        fault_stage = "cpu_clock_init"
        cpu_clock = ProcessCpuClock(
            pid,
            max_resolution_ns=max_cpu_resolution_ns,
        )

        fault_stage = "affinity_observation"
        affinity_before = observe_affinity(
            affinity,
            pid,
            max_attempts=affinity_max_attempts,
        )

        fault_stage = "physical_snapshot"
        proc_before = proc.snapshot(pid)
        if (
            proc_before.pid != identity_binding.pid
            or proc_before.start_time_ticks != identity_binding.start_time_ticks
        ):
            raise RuntimeError(
                "engine process identity changed before measured search"
            )
        cpu_before_ns = cpu_clock.sample_ns()

        fault_stage = "search"
        lines = session.search_nodes(
            candidate.nodes,
            timeout=max(5.0, deadline_ms / 1000.0 + 2.0),
        )

        fault_stage = "physical_snapshot"
        cpu_after_ns = cpu_clock.sample_ns()
        proc_after = proc.snapshot(pid)

        fault_stage = "affinity_observation"
        affinity_after = observe_affinity(
            affinity,
            pid,
            max_attempts=affinity_max_attempts,
        )
        scope = process_cpu_scope(affinity_before, affinity_after)

        fault_stage = "physical_reconstruction"
        cpu_evidence = cpu_clock.evidence(cpu_before_ns, cpu_after_ns)
        primitives = physical_primitives(
            identity_binding,
            proc_before,
            proc_after,
            cpu_evidence,
        )
        physical = reconstruct_physical_measurement(
            primitives,
            clock_ticks_per_second=clock_ticks_per_second,
            required_cpu_method=required_cpu_method,
            max_cpu_resolution_ns=max_cpu_resolution_ns,
        )

        fault_stage = "transcript_parse"
        observation = parse_search_observation(
            lines,
            family=candidate.family,
            physical=physical,
        )
        row.update(
            {
                "status": "completed",
                "fault_stage": None,
                "fault_class": None,
                "warmup": warmup,
                "measurement": observation.as_dict(),
                "physical_primitives": primitives,
                "process_cpu_scope": scope,
                "affinity_observation_before": affinity_before.as_dict(),
                "affinity_observation_after": affinity_after.as_dict(),
                "error": None,
                "transcript": list(session.transcript),
            }
        )
    except Exception as exc:
        stage, fault_class = classify_failure(fault_stage, exc)
        row.update(
            {
                "status": "error",
                "fault_stage": stage,
                "fault_class": fault_class,
                "measurement": None,
                "physical_primitives": None,
                "process_cpu_scope": None,
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
        default=None,
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="contract smoke only: first case, one repeat, reference candidates",
    )
    args = parser.parse_args()

    spec_path = args.spec.resolve()
    spec = load_lab_spec(spec_path)
    validate_reference_contract(spec, ROOT)
    source = source_identity(ROOT)
    execution_domain = load_execution_domain(
        args.execution_domain,
        expected_source_commit=source["commit"],
    )
    clock_ticks_per_second = int(
        execution_domain["runtime_substrate"]["clock_ticks_per_second"]
    )
    cpu_policy = dict(spec.raw["cpu_measurement"])
    affinity_policy = dict(spec.raw["affinity_observation"])

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

    output = (
        args.output.resolve()
        if args.output is not None
        else (ROOT / "build/test-results" / spec.lab_id).resolve()
    )
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "lab_id": spec.lab_id,
        "source_commit": source["commit"],
        "source_tree": source["tree"],
        "lab_spec": {
            "path": str(
                spec_path.relative_to(ROOT)
                if ROOT in spec_path.parents
                else spec_path
            ),
            "sha256": sha256(spec_path),
            "canonical_digest": spec.digest,
        },
        "corpus": {
            "path": spec.corpus,
            "sha256": sha256(ROOT / spec.corpus),
            "cases": [case.case_id for case in cases],
        },
        "reference_contract": reference_contract_manifest(spec, ROOT),
        "candidate_bundle": candidate_bundle,
        "execution_domain": execution_domain,
        "attempt_policy": spec.raw["attempt_policy"],
        "attempt_order_policy": spec.raw["attempt_order_policy"],
        "placement_mode": spec.raw["placement_mode"],
        "cpu_measurement": cpu_policy,
        "affinity_observation": affinity_policy,
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
            "compositions": [
                composition.as_dict() for composition in compositions
            ],
        },
    )

    candidate_map = {candidate.candidate_id: candidate for candidate in candidates}
    case_map = {case.case_id: case for case in cases}
    composition_map = {
        composition.composition_id: composition
        for composition in compositions
    }

    if args.quick:
        selected_candidates = tuple(
            candidate for candidate in candidates if candidate.reference
        )
        selected_case = cases[0]
        stage_a_plan = tuple(
            {
                "candidate_id": candidate.candidate_id,
                "repeat_index": 0,
                "case_id": selected_case.case_id,
                "block_index": 0,
                "order_index": index,
                "attempt_ordinal": index,
            }
            for index, candidate in enumerate(selected_candidates)
        )
        stage_b_plan = (
            {
                "composition_id": compositions[0].composition_id,
                "repeat_index": 0,
                "case_id": selected_case.case_id,
                "block_index": 0,
                "order_index": 0,
                "attempt_ordinal": 0,
            },
        )
    else:
        case_ids = tuple(case.case_id for case in cases)
        stage_a_plan = stage_a_attempt_plan(
            candidates,
            case_ids,
            spec.repeats,
        )
        stage_b_plan = stage_b_attempt_plan(
            compositions,
            case_ids,
            spec.repeats,
        )

    stage_a_rows: list[dict[str, Any]] = []
    for attempt in stage_a_plan:
        candidate = candidate_map[attempt["candidate_id"]]
        case = case_map[attempt["case_id"]]
        row = run_isolated(
            candidate=candidate,
            bundle_root=bundle_root,
            case=case,
            repeat_index=attempt["repeat_index"],
            deadline_ms=float(spec.raw["isolated_deadline_ms"]),
            clock_ticks_per_second=clock_ticks_per_second,
            required_cpu_method=str(cpu_policy["required_method"]),
            max_cpu_resolution_ns=int(cpu_policy["max_resolution_ns"]),
            affinity_max_attempts=int(affinity_policy["max_attempts"]),
            block_index=attempt["block_index"],
            order_index=attempt["order_index"],
            attempt_ordinal=attempt["attempt_ordinal"],
        )
        stage_a_rows.append(row)
        print(
            json.dumps(
                {
                    "stage": "A",
                    "candidate": candidate.candidate_id,
                    "repeat": attempt["repeat_index"],
                    "case": case.case_id,
                    "block": attempt["block_index"],
                    "order": attempt["order_index"],
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

    stage_b_rows: list[dict[str, Any]] = []
    for attempt in stage_b_plan:
        composition = composition_map[attempt["composition_id"]]
        case = case_map[attempt["case_id"]]
        row = run_composition_batch(
            composition=composition,
            candidate_map=candidate_map,
            bundle_root=bundle_root,
            case=case,
            repeat_index=attempt["repeat_index"],
            deadline_ms=float(spec.raw["composition_deadline_ms"]),
            clock_ticks_per_second=clock_ticks_per_second,
            required_cpu_method=str(cpu_policy["required_method"]),
            max_cpu_resolution_ns=int(cpu_policy["max_resolution_ns"]),
            affinity_max_attempts=int(affinity_policy["max_attempts"]),
            block_index=attempt["block_index"],
            order_index=attempt["order_index"],
            attempt_ordinal=attempt["attempt_ordinal"],
        )
        stage_b_rows.append(row)
        print(
            json.dumps(
                {
                    "stage": "B",
                    "composition": composition.composition_id,
                    "repeat": attempt["repeat_index"],
                    "case": case.case_id,
                    "block": attempt["block_index"],
                    "order": attempt["order_index"],
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
            "stage_a_errors": sum(
                row["status"] != "completed" for row in stage_a_rows
            ),
            "stage_b_batches": len(stage_b_rows),
            "stage_b_errors": sum(
                row["status"] != "completed" for row in stage_b_rows
            ),
            "promotion_ready": False,
            "promotion_requires": "J7 frozen profile selection",
            "claim_boundary": dict(spec.raw["claim_boundary"]),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
