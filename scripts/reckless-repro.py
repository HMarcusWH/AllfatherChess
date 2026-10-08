#!/usr/bin/env python3
"""Exact-head, single-worker Reckless A/B/A-build diagnostic; NEVER promotion evidence."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.engine_opt.corpus import load_epd
from tools.engine_opt.runner import run_case
from tools.engine_opt.report import source_identity, write_report
from tools.reckless_repro.analysis import DiagnosticError, NODES, PROTOCOL_ID, REPEATS, analyze, schedule
from tools.reckless_repro.provenance import provenance, sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for label in ("derived-a", "derived-b", "pristine"):
        parser.add_argument("--" + label, type=Path, required=True)
        parser.add_argument("--build-log-" + label, type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--host-report", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    paths = {label: getattr(args, label.replace("-", "_")).resolve()
             for label in ("derived-a", "derived-b", "pristine")}
    logs = {label: getattr(args, "build_log_" + label.replace("-", "_")).resolve()
            for label in paths}
    report = {
        "schema_version": 1,
        "kind": "reckless-repro-diagnostic",
        "protocol_id": PROTOCOL_ID,
        "source_commit": args.expected_head,
        "diagnostic_complete": False,
        "promotable": False,
        "promotion_evidence": False,
        "qualification_disposition": "DIAGNOSTIC_INCOMPLETE",
        "claim_boundary": {"canonical_b4_promotion": False, "engine_profile_qualified": False,
                           "generic_host_portability": False, "elo": False,
                           "strength": False, "equal_compute": False, "deployment": False},
        "rows": [],
        "errors": [],
    }
    try:
        source = source_identity(ROOT)
        if source["commit"] != args.expected_head:
            raise DiagnosticError("diagnostic source is not exact expected HEAD")
        lock = json.loads((ROOT / "vendor.lock.json").read_text())
        engine_lock = json.loads((ROOT / "qualification/engine-derived-lock.json").read_text())
        actual_tree = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD:engines/reckless"], text=True
        ).strip()
        if actual_tree != engine_lock["engines"]["reckless"]["derived_tree"]:
            raise DiagnosticError("derived Reckless source tree differs from frozen lock")
        if sha256(args.model) != lock["engines"]["reckless"]["artifacts"]["default_nnue"]["sha256"]:
            raise DiagnosticError("Reckless model is not the locked NNUE")
        report["source"] = source
        report["derived_reckless_tree"] = actual_tree
        report["pristine_reckless_commit"] = lock["engines"]["reckless"]["commit"]
        report["pristine_reckless_tree"] = lock["engines"]["reckless"]["tree"]
        report["provenance"] = provenance(paths, args.model, logs, args.host_report)
        derived_a = report["provenance"]["binaries"]["derived-a"]
        derived_b = report["provenance"]["binaries"]["derived-b"]
        a_text, b_text = derived_a["elf_text"], derived_b["elf_text"]
        report["build_reproducibility"] = {
            "derived_bytes_equal": derived_a["sha256"] == derived_b["sha256"],
            "derived_text_digest_available": a_text.get("available") is True and b_text.get("available") is True,
            "derived_text_equal": (
                a_text["sha256"] == b_text["sha256"]
                if a_text.get("available") is True and b_text.get("available") is True
                else None
            ),
            "claim": "diagnostic only; binary metadata may vary even with identical source trees",
        }

        affinity = sorted(os.sched_getaffinity(0))
        if not affinity:
            raise DiagnosticError("CPU affinity mask empty")
        target_cpu = affinity[0]
        os.sched_setaffinity(0, {target_cpu})
        if os.sched_getaffinity(0) != {target_cpu}:
            raise DiagnosticError("could not enforce stable single-logical-CPU placement")
        report["affinity"] = {"original_allowed_cpus": affinity,
                              "pinned_logical_cpu": target_cpu,
                              "effective_cpus": sorted(os.sched_getaffinity(0)),
                              "claim": "same-worker-single-logical-cpu; not a fixed physical host"}
        cases = load_epd(ROOT / "tests/fixtures/engine_opt/positions.epd")
        ids = [c.case_id for c in cases]
        case_map = {c.case_id: c for c in cases}
        slots = schedule(ids)
        report["plan"] = {"repeats": REPEATS, "nodes": NODES,
                          "attempt_policy": "single-predeclared-schedule-no-retry",
                          "ordering": "per-case-ABBA/BAAB",
                          "planned_rows": len(slots),
                          "cases": ids,
                          "pairs": [["derived-a", "pristine"], ["derived-b", "pristine"]]}
        options = {"Threads": 1, "Hash": 16, "MultiPV": 1,
                   "Minimal": False, "UCI_Chess960": False}
        for slot in slots:
            try:
                row = run_case(binary=paths[slot["binary"]], cwd=ROOT,
                               family="reckless", case=case_map[slot["case_id"]],
                               options=dict(options), nodes=NODES, deadline_ms=10000.0)
                if row["binary_sha256"] != report["provenance"]["binaries"][slot["binary"]]["sha256"]:
                    raise DiagnosticError("binary changed during diagnostic run")
                row.update(slot)
                report["rows"].append(row)
            except Exception as exc:
                report["errors"].append({**slot, "error": f"{type(exc).__name__}: {exc}"})
                # No selective retries or cherry-picked partial pair.
        if report["errors"]:
            raise DiagnosticError(f"predeclared schedule has {len(report['errors'])} execution errors")
        result = analyze(report["rows"], ids)
        report["analysis"] = result
        divergent = any(item["bestmove_mismatches"] or item["native_work_mismatches"]
                        for item in result["comparisons"].values())
        if divergent:
            raise DiagnosticError("search work/bestmove differs; performance not comparable")
        report["diagnostic_complete"] = True
        report["qualification_disposition"] = "DIAGNOSTIC_COMPLETE_NOT_PROMOTION"
    except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError,
            subprocess.CalledProcessError) as exc:
        report["errors"].append({"error": f"{type(exc).__name__}: {exc}"})
        report["qualification_disposition"] = "DIAGNOSTIC_INCOMPLETE"
    finally:
        write_report(args.output, report)
    return 0 if report["diagnostic_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
