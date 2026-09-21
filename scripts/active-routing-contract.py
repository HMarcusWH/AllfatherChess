#!/usr/bin/env python3
"""Qualify active budget routing against the three real engines.

The contract runs the whole pipeline end to end:

```text
shadow evidence -> derived residual features -> calibrated reversal risk
                -> active routing under a declared envelope
```

It establishes that routing spends compute inside the declared envelope and
cannot touch outward decision authority. It establishes **no** strength result:
the outward move is still the unrestricted anchor's, and no Elo experiment is
run here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.calibration import ReversalRiskModel, training_rows_from_derived, write_calibration
from controller.replay import load_manifest, sha256_file
from controller.residuals import build_derived_artifact, write_derived_artifact
from controller.runtime import load_runtime_config
from tests.harness.uci_session import UciError, UciSession


SHADOW_CONFIG = ROOT / "config" / "allfather.shadow.validation.json"
ACTIVE_CONFIG = ROOT / "config" / "allfather.active.validation.json"
ANCHOR_CONFIG = ROOT / "config" / "allfather.validation.json"
CORPUS_PATH = ROOT / "tests" / "baseline" / "corpus.json"
RESULT_DIR = ROOT / "build" / "test-results" / "active-routing"

#: Enough distinct runs that the deterministic by-run split leaves held-out
#: rows. Without them the fitted model is not out-of-sample validated and the
#: router's `calibration_validated` gate correctly refuses to act on it.
EVIDENCE_CASES = (
    "startpos",
    "history_ruy_lopez",
    "perft_position_3",
    "perft_position_4",
    "perft_position_5",
    "perft_position_6",
    "en_passant_available",
    "promotion_available",
    "side_in_check",
    "kiwipete_castling",
)
ACTIVE_CASES = ("startpos", "history_ruy_lopez", "perft_position_5", "perft_position_6")
#: Two passes over the corpus. Removing right-censored labels made each run
#: contribute far fewer honest rows, so bucket support has to come from more
#: evidence rather than from a lower support floor.
EVIDENCE_PASSES = 2
EVIDENCE_MOVETIME_MS = 500
ACTIVE_MOVETIME_MS = 1200
FIXED_NODES = 512


class ContractError(RuntimeError):
    pass


def position_payload(position: dict[str, Any]) -> dict[str, Any]:
    if "fen" in position:
        return {"fen": position["fen"]}
    return {"startpos_moves": list(position.get("startpos_moves", []))}


def bestmove_of(lines: list[str], label: str) -> str:
    matches = [line.split()[1] for line in lines if line.startswith("bestmove ")]
    if len(matches) != 1:
        raise ContractError(f"{label}: expected exactly one bestmove, got {matches}")
    return matches[0].lower()


def drive(config: Path, cases: list[dict[str, Any]], movetime_ms: int) -> list[str]:
    moves: list[str] = []
    with UciSession(
        Path(sys.executable),
        cwd=ROOT,
        timeout=90.0,
        args=["-m", "controller", "--config", str(config)],
    ) as shell:
        shell.configure({"UCI_Chess960": False})
        for position in cases:
            shell.new_game()
            shell.set_position(position)
            shell.send(f"go movetime {movetime_ms}")
            lines = shell.read_until(
                lambda line: line.startswith("bestmove "),
                label="bestmove",
                timeout=120.0,
            )
            moves.append(bestmove_of(lines, "active shell"))
    return moves


def write_variant(base: Path, workdir: Path, *, replay_root: Path, overrides: dict[str, Any]) -> Path:
    document = json.loads(base.read_text(encoding="utf-8"))
    document["root"] = str(ROOT)
    document.setdefault("shadow", {})["replay_root"] = str(replay_root)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(document.get(key), dict):
            document[key].update(value)
        else:
            document[key] = value
    path = workdir / f"{base.stem}.contract.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def engine_processes_alive(config) -> list[str]:
    alive: list[str] = []
    for spec in config.backends.values():
        probe = subprocess.run(["pgrep", "-f", str(spec.binary)], capture_output=True, text=True)
        pids = [pid for pid in probe.stdout.split() if pid and int(pid) != os.getpid()]
        if pids:
            alive.append(f"{spec.name}:{','.join(pids)}")
    return alive


def main() -> int:
    corpus = {case["id"]: case["position"] for case in json.loads(CORPUS_PATH.read_text())["cases"]}
    report: dict[str, Any] = {"schema_version": 1, "stages": {}}

    with tempfile.TemporaryDirectory(prefix="allfather-active-") as tmp:
        workdir = Path(tmp)
        evidence_root = workdir / "replays-evidence"
        active_root = workdir / "replays-active"

        # 1. Collect shadow evidence.
        evidence_config = write_variant(
            SHADOW_CONFIG, workdir, replay_root=evidence_root, overrides={}
        )
        drive(
            evidence_config,
            [position_payload(corpus[case]) for case in EVIDENCE_CASES] * EVIDENCE_PASSES,
            EVIDENCE_MOVETIME_MS,
        )
        evidence_runs = sorted(path for path in evidence_root.iterdir() if path.is_dir())
        expected = len(EVIDENCE_CASES) * EVIDENCE_PASSES
        if len(evidence_runs) < expected:
            raise ContractError(
                f"evidence sweep produced {len(evidence_runs)} bundles, expected {expected}"
            )
        report["stages"]["evidence"] = {
            "cases": list(EVIDENCE_CASES),
            "passes": EVIDENCE_PASSES,
            "movetime_ms": EVIDENCE_MOVETIME_MS,
            "bundles": len(evidence_runs),
        }

        # 2. Derive features and 3. fit a calibration.
        artifact = build_derived_artifact(evidence_runs)
        derived_path = write_derived_artifact(artifact, workdir / "derived")
        rows = training_rows_from_derived(artifact.as_dict())
        if not rows:
            raise ContractError("derived evidence produced no labelled calibration rows")
        model = ReversalRiskModel.fit(
            rows,
            min_support=10,
            sources=[{"derived_id": artifact.derived_id, "sha256": sha256_file(derived_path)}],
        )
        if not model.evaluation.get("test_rows"):
            raise ContractError(
                "the fitted calibration has no held-out rows, so it is not validated "
                "out of sample; collect more distinct runs before qualifying routing"
            )
        model_path = write_calibration(model, workdir / "calibration")
        report["stages"]["calibration"] = {
            "derived_id": artifact.derived_id,
            "model_id": model.model_id,
            "rows": len(rows),
            "buckets": len(model.buckets),
            "evaluation": model.evaluation,
        }

        # 4. Active routing against that calibration.
        active_config = write_variant(
            ACTIVE_CONFIG,
            workdir,
            replay_root=active_root,
            overrides={
                "routing": {
                    "calibration": str(model_path),
                    "checkpoint_interval_ms": 60,
                    "min_observation_nodes": 1000,
                    "stop_min_support": 10,
                    "stage_cpu_ms_estimate": 600,
                    "anchor_cpu_ms_estimate": 1200,
                },
                "budget": {
                    "wall_ms": ACTIVE_MOVETIME_MS,
                    "cpu_ms": 6000,
                    "gpu_ms": 0,
                    "verification_reserve_fraction": 0.1,
                    "controller_overhead_reserve_ms": 150,
                },
                "shadow": {"dispatch_limit": {"nodes": 200000}},
            },
        )
        active_moves = drive(
            active_config,
            [position_payload(corpus[case]) for case in ACTIVE_CASES],
            ACTIVE_MOVETIME_MS,
        )

        # 5. Decision firewall under a deterministic fixed-node request.
        anchor_config = load_runtime_config(ANCHOR_CONFIG)
        stockfish = anchor_config.backends["stockfish"]
        with UciSession(
            stockfish.binary, cwd=stockfish.cwd, timeout=30.0, args=list(stockfish.args)
        ) as direct:
            direct.configure(stockfish.options)
            direct.new_game()
            direct.set_position({"startpos_moves": []})
            direct_move = bestmove_of(direct.search_nodes(FIXED_NODES, timeout=30.0), "direct")

        with UciSession(
            Path(sys.executable),
            cwd=ROOT,
            timeout=90.0,
            args=["-m", "controller", "--config", str(active_config)],
        ) as shell:
            shell.configure({"UCI_Chess960": False})
            shell.new_game()
            shell.set_position({"startpos_moves": []})
            active_fixed = bestmove_of(shell.search_nodes(FIXED_NODES, timeout=60.0), "active")
        if active_fixed != direct_move:
            raise ContractError(
                f"active routing changed the outward fixed-node decision: "
                f"direct={direct_move}, active={active_fixed}"
            )

        # 6. Audit every active run.
        runs = sorted(path for path in active_root.iterdir() if path.is_dir())
        if len(runs) < len(ACTIVE_CASES):
            raise ContractError("active mode produced fewer replay bundles than searches")

        granted_stops = 0
        denied_stops = 0
        decisions = 0
        claimed_runs = 0
        unclaimable_runs = 0
        wall_short_runs = 0
        wall_overshoot_ms = 0.0
        max_stages = json.loads(active_config.read_text())["routing"]["max_stages_per_owner"]
        per_run: list[dict[str, Any]] = []

        for run_dir in runs:
            manifest = load_manifest(run_dir)
            route_path = run_dir / "route.json"
            if not route_path.is_file():
                raise ContractError(f"{run_dir.name}: active run wrote no route.json")
            route = json.loads(route_path.read_text(encoding="utf-8"))

            budget = route["budget"]
            if not budget["within_envelope"]:
                raise ContractError(f"{run_dir.name}: routing exceeded the declared envelope")

            # `within_envelope` is the CPU/GPU RESERVATION bit alone. The claim
            # the report makes is `envelope_claim.claimed`, which additionally
            # requires a bounded outward request, a reserved anchor cost, GPU
            # accounting and wall-time compliance. Asserting the weaker bit and
            # reporting the stronger sentence is exactly the silent upgrade the
            # claim ledger forbids.
            claim = route["envelope_claim"]
            # `limits` is a LIST of {name, value, semantics}, not a mapping.
            limits = manifest["external_request"]["request"].get("limits", [])
            claimable = any(entry.get("name") == "movetime" for entry in limits)
            if not claimable:
                # The fixed-node firewall search bounds work but not wall time,
                # so it CANNOT claim wall compliance and must not be counted as
                # if it had. Assert that it says so rather than letting it pass
                # silently into the total.
                if claim["anchor_request_bounded"] or claim["claimed"]:
                    raise ContractError(
                        f"{run_dir.name}: a wall-unbounded request claimed envelope "
                        f"compliance: {json.dumps(claim, sort_keys=True)}"
                    )
                unclaimable_runs += 1
            else:
                for component in (
                    "anchor_request_bounded",
                    "anchor_cost_reserved",
                    "gpu_accounted",
                    "reservations_within_envelope",
                ):
                    if not claim[component]:
                        raise ContractError(
                            f"{run_dir.name}: envelope claim component {component} is "
                            f"false: {json.dumps(claim, sort_keys=True)}"
                        )
                if claim["claimed"]:
                    claimed_runs += 1
                else:
                    # The ONLY shortfall this contract tolerates is wall time,
                    # and only because this configuration declares
                    # `budget.wall_ms` EQUAL to the anchor's own `movetime`:
                    # the outward search alone saturates the envelope, leaving
                    # nothing for controller overhead. That figure is left as
                    # declared -- raising it to make the claim true would be
                    # manufacturing the result -- so the shortfall is measured
                    # and reported instead. Any OTHER reason the claim is false
                    # is a regression and fails here.
                    if claim["wall_within_envelope"]:
                        raise ContractError(
                            f"{run_dir.name}: the envelope claim is false although every "
                            f"component is true: {json.dumps(claim, sort_keys=True)}"
                        )
                    wall_short_runs += 1
                    wall_overshoot_ms = max(
                        wall_overshoot_ms,
                        claim["wall_ms_elapsed"] - route["envelope"]["wall_ms"],
                    )
            if budget["open_reservations"] != 0:
                raise ContractError(f"{run_dir.name}: reservations were left open")
            if budget["committed_cpu_ms"] > budget["envelope"]["cpu_ms"]:
                raise ContractError(f"{run_dir.name}: committed CPU exceeded the envelope")
            if "controller" not in budget["lanes"]:
                raise ContractError(f"{run_dir.name}: controller overhead was not charged")

            anchor_stages = [s for s in manifest["stages"] if s["role"] == "anchor"]
            if len(anchor_stages) != 1 or anchor_stages[0]["dispatched_roots"]:
                raise ContractError(f"{run_dir.name}: the anchor was not a single unrestricted search")

            counts: dict[str, int] = {}
            for stage in manifest["stages"]:
                if stage["role"] == "shadow":
                    counts[stage["owner"]] = counts.get(stage["owner"], 0) + 1
                    owned = manifest["ledger"]["owner_roots"][stage["owner"]]
                    if stage["dispatched_roots"] != owned:
                        raise ContractError(
                            f"{run_dir.name}: {stage['instance']} dispatched outside its region"
                        )
            for owner, count in counts.items():
                if count > max_stages:
                    raise ContractError(
                        f"{run_dir.name}: owner {owner} received {count} stages, max is {max_stages}"
                    )

            for decision in route["decisions"]:
                decisions += 1
                if decision["proposal"]["action"] != "stop_worker":
                    continue
                if decision["granted"]:
                    granted_stops += 1
                    if not all(gate["passed"] for gate in decision["gates"]):
                        raise ContractError(
                            f"{run_dir.name}: a stop was granted with a failing gate"
                        )
                    if decision["action"] != "stop_worker":
                        raise ContractError(f"{run_dir.name}: granted stop did not act")
                else:
                    denied_stops += 1
                    if decision["action"] != "continue":
                        raise ContractError(
                            f"{run_dir.name}: a denied stop degraded to "
                            f"{decision['action']!r} instead of continued observation"
                        )

            blob = json.dumps(manifest).lower()
            for needle in ("routing_decision", "reversal_risk", "calibration", "proposal"):
                if needle in blob:
                    raise ContractError(f"{run_dir.name}: policy material leaked into raw evidence")

            per_run.append(
                {
                    "run_id": manifest["run_id"],
                    "disposition": manifest["disposition"]["run"],
                    "stages_per_owner": counts,
                    "committed_cpu_ms": budget["committed_cpu_ms"],
                    "envelope_cpu_ms": budget["envelope"]["cpu_ms"],
                    "controller_overhead_ms": budget["lanes"]["controller"]["spent_cpu_ms"],
                    "decisions": len(route["decisions"]),
                }
            )

        orphans = engine_processes_alive(load_runtime_config(active_config))
        if orphans:
            raise ContractError(f"engine processes outlived the active shell: {orphans}")

        report["stages"]["active"] = {
            "movetime_ms": ACTIVE_MOVETIME_MS,
            "outward_moves": active_moves,
            "runs": per_run,
            "decisions": decisions,
            "granted_stops": granted_stops,
            "denied_stops": denied_stops,
            "envelope_claimed_runs": claimed_runs,
            "envelope_unclaimable_runs": unclaimable_runs,
            "envelope_wall_short_runs": wall_short_runs,
            "envelope_wall_overshoot_ms": round(wall_overshoot_ms, 3),
            "envelope_wall_note": (
                "budget.wall_ms is declared EQUAL to the anchor's own movetime in this "
                "configuration, so the outward search alone saturates the wall envelope "
                "and no run can claim wall compliance. The reservation, anchor-bound, "
                "anchor-cost and GPU components are asserted; the wall shortfall is "
                "measured rather than removed by raising the declared figure."
            ),
        }
        report["stages"]["decision_firewall"] = {
            "direct_stockfish_bestmove": direct_move,
            "active_mode_bestmove": active_fixed,
            "equivalent": True,
            "nodes": FIXED_NODES,
        }
        report["evidence_sufficiency"] = {
            # Recorded, not asserted. A contract that required authorized stops
            # would be a contract that rewards loosening the gate.
            "authorized_stops": granted_stops,
            "denied_stops": denied_stops,
            "in_domain_rate": model.evaluation.get("in_domain_rate"),
            "note": (
                "Zero authorized stops means the evidence collected here does not "
                "support suppression under the declared thresholds. That is the "
                "policy working, not failing."
            ),
        }
        report["not_claimed"] = [
            "no strength or Elo result: the outward move is still the unrestricted anchor's",
            "no timed equivalence: CPU/GPU resource isolation was not established",
            "the calibration is fitted from a small in-session sweep and is not a general model",
        ]

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        "active routing contract passed: "
        f"{report['stages']['active']['decisions']} decisions, "
        f"{granted_stops} authorized stops, {denied_stops} denied stops, "
        f"envelope components verified in {claimed_runs + wall_short_runs} movetime run(s) "
        f"({claimed_runs} full claim, {wall_short_runs} short on wall by up to "
        f"{wall_overshoot_ms:.1f}ms against a wall_ms declared equal to the movetime), "
        f"{unclaimable_runs} wall-unbounded run(s) correctly claimed nothing, "
        f"fixed-node firewall {direct_move}=={active_fixed}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, UciError, OSError, ValueError) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"active routing contract failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "failure.txt").write_text(message + "\n", encoding="utf-8")
        raise SystemExit(1)
