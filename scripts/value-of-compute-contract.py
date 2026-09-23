#!/usr/bin/env python3
"""Real-engine paired VERIFY value-of-compute contract.

Runs the same position twice with identical upstream settings and different
VERIFY node budgets. The contract checks causal pairing and label construction;
it does not require the decision to change and makes no strength claim.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.counterfactual import load_counterfactual_artifact
from controller.replay import discover_replay_bundles, load_manifest
from controller.value_of_compute import (
    ValueOfComputeError,
    build_dataset,
    build_transition,
    load_budget_point,
)
from tests.harness.uci_session import UciError, UciSession


BASE_CONFIG = ROOT / "config" / "allfather.value.validation.json"
RESULT_DIR = ROOT / "build" / "test-results" / "value-of-compute"
REPLAY_ROOT = RESULT_DIR / "replays"
BUDGETS = (64, 128)
ANCHOR_MOVETIME_MS = 5000


class ContractError(RuntimeError):
    pass


def _write_config(base: dict, nodes: int) -> Path:
    doc = json.loads(json.dumps(base))
    doc["root"] = str(ROOT)
    doc["shadow"]["replay_root"] = str(REPLAY_ROOT.resolve())
    doc["verification"]["dispatch_limit"] = {"nodes": nodes}
    doc.pop("refinement", None)
    path = RESULT_DIR / f"config-n{nodes}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _wait_run(known: set[str]) -> Path:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        discovery = discover_replay_bundles(REPLAY_ROOT)
        fresh = [path for path in discovery.bundles if path.name not in known]
        if fresh:
            candidate = fresh[-1]
            if (candidate / "decision" / "counterfactual.json").is_file():
                return candidate
            # A finalized parent manifest with no decision artifact is terminal
            # for this arm: VERIFY/counterfactual work was never launched or was
            # cut off. Fail immediately with the actual lifecycle boundary
            # instead of waiting 30 seconds for an artifact that cannot appear.
            manifest = load_manifest(candidate)
            raise ContractError(
                "paired arm sealed without counterfactual evidence; "
                f"disposition={manifest.get('disposition')}, "
                f"notes={manifest.get('notes')}"
            )
        time.sleep(0.05)
    raise ContractError("timed out waiting for paired counterfactual run")


def _run_arm(config: Path) -> tuple[Path, str]:
    discovery = discover_replay_bundles(REPLAY_ROOT)
    known = {path.name for path in discovery.bundles} | {
        item.path.name for item in discovery.skipped
    }
    with UciSession(
        Path(sys.executable),
        cwd=ROOT,
        timeout=60.0,
        args=["-m", "controller", "--config", str(config)],
    ) as shell:
        shell.configure({"UCI_Chess960": False})
        shell.new_game()
        shell.set_position({"startpos_moves": []})
        # A short fixed-node anchor can finish before LC0 EXPLORE completes.
        # Once the outward anchor boundary is crossed, shadow mode drains
        # already-dispatched work but does not start new VERIFY stages. Keep the
        # anchor deliberately alive so both budget arms actually reach VERIFY.
        shell.send(f"go movetime {ANCHOR_MOVETIME_MS}")
        lines = shell.read_until(
            lambda line: line.startswith("bestmove "),
            label="value-of-compute anchor bestmove",
            timeout=90.0,
        )
        outward = next(
            line.split()[1].lower()
            for line in lines
            if line.startswith("bestmove ")
        )
        # The anchor may finish before VERIFY. Keep the UCI process alive until
        # the derived decision artifact seals; exiting the context would send
        # quit and could turn an otherwise valid arm into cancelled evidence.
        run_dir = _wait_run(known)
        return run_dir, outward


def main() -> int:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    REPLAY_ROOT.mkdir(parents=True, exist_ok=True)
    base = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))

    points = []
    arms = []
    for nodes in BUDGETS:
        run_dir, outward = _run_arm(_write_config(base, nodes))
        point = load_budget_point(
            run_dir,
            position_group="startpos",
            replicate=0,
        )
        parent = load_manifest(run_dir)
        decision = load_counterfactual_artifact(run_dir)
        anchor = next(
            stage
            for stage in parent.get("stages") or []
            if stage.get("role") == "anchor"
        )
        if anchor.get("bestmove") != outward:
            raise ContractError(
                f"n{nodes}: outward {outward} differs from recorded anchor "
                f"{anchor.get('bestmove')}"
            )
        if (decision.get("counterfactual") or {}).get("outward_authority") != "stockfish-anchor":
            raise ContractError(f"n{nodes}: decision artifact changed outward authority")
        points.append(point)
        arms.append(
            {
                "verify_nodes": nodes,
                "run_id": point.run_id,
                "outward_bestmove": outward,
                "proposal_disposition": point.proposal_disposition,
                "proposal_move": point.proposal_move,
                "feature_digest": point.feature_digest,
                "upstream_fingerprint": point.upstream_fingerprint,
            }
        )

    transition = build_transition(points[0], points[1])
    dataset = build_dataset(points)
    if len(dataset.get("transitions") or []) != 1:
        raise ContractError("paired contract did not produce exactly one transition")
    if points[0].upstream_fingerprint != points[1].upstream_fingerprint:
        raise ContractError("VERIFY budget arms do not share upstream fingerprint")
    if transition.additional_nodes_per_owner != BUDGETS[1] - BUDGETS[0]:
        raise ContractError("additional VERIFY node label is incorrect")
    if transition.feature_digest != points[0].feature_digest:
        raise ContractError("transition feature address is not the lower arm")
    if transition.feature_digest == transition.label_digest:
        raise ContractError("feature and future-label addresses must remain separate")

    report = {
        "schema_version": 1,
        "base_config": str(BASE_CONFIG.relative_to(ROOT)),
        "budgets": list(BUDGETS),
        "arms": arms,
        "transition": {
            "key": transition.transition_key,
            "additional_nodes_per_owner": transition.additional_nodes_per_owner,
            "decision_changed": transition.decision_changed,
            "proposal_emerged": transition.proposal_emerged,
            "proposal_disappeared": transition.proposal_disappeared,
            "proposal_move_changed": transition.proposal_move_changed,
            "terminal_vector_changed": transition.terminal_vector_changed,
            "feature_digest": transition.feature_digest,
            "label_digest": transition.label_digest,
        },
        "dataset_id": dataset["dataset_id"],
        "claim": (
            "Control/calibration mechanism only: two actual completed VERIFY arms "
            "with matching upstream evidence produced one causally eligible "
            "decision-change label. Whether the decision changed is descriptive; "
            "no correctness, Elo or strength claim follows."
        ),
    }
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "Value-of-compute contract passed: "
        f"transition={transition.transition_key}, "
        f"changed={transition.decision_changed}, dataset={dataset['dataset_id']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, ValueOfComputeError, UciError, OSError, ValueError) as exc:
        print(f"value-of-compute contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
