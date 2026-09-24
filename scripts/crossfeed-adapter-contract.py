#!/usr/bin/env python3
"""Real-engine subprocess contract for M14-E cross-feed adapters.

The contract first seals ordinary cross-feed evidence through the existing
controller path, reconstructs the separate adapter evidence projection from
those artifacts, then proves all three vendored engines accept adapter-generated
ordinary UCI restrictions. It does not wire adapters into live routing and
makes no strength claim.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.crossfeed import (
    Lc0CrossFeedAdapter,
    RecklessCrossFeedAdapter,
    StockfishCrossFeedAdapter,
    build_adapter_evidence_from_run,
)
from common.search_request import parse_position_command
from controller.crossfeed import verify_crossfeed_integrity
from controller.replay import discover_replay_bundles, load_manifest
from controller.runtime import load_runtime_config
from tests.harness.uci_session import UciSession


CONFIG_PATH = ROOT / "config" / "allfather.crossfeed.validation.json"
RESULT_DIR = ROOT / "build" / "test-results" / "crossfeed-adapters"
ANCHOR_MOVETIME_MS = 5000
_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class ContractError(RuntimeError):
    pass


def _bestmove(lines: list[str]) -> str:
    values = [
        line.split()[1].lower()
        for line in lines
        if line.startswith("bestmove ") and len(line.split()) >= 2
    ]
    if len(values) != 1 or not _MOVE_RE.fullmatch(values[0]):
        raise ContractError(f"expected one canonical bestmove, got {values}")
    return values[0]


def _seal_source_run(config) -> Path:
    assert config.shadow is not None
    replay_root = config.shadow.replay_root
    replay_root.mkdir(parents=True, exist_ok=True)
    before = discover_replay_bundles(replay_root)
    known = {path.name for path in before.bundles} | {
        item.path.name for item in before.skipped
    }

    with UciSession(
        Path(sys.executable),
        cwd=ROOT,
        timeout=30.0,
        args=["-m", "controller", "--config", str(CONFIG_PATH)],
    ) as shell:
        shell.configure({"UCI_Chess960": False})
        shell.new_game()
        shell.set_position({"startpos_moves": []})
        shell.send(f"go movetime {ANCHOR_MOVETIME_MS}")
        shell.read_until(
            lambda line: line.startswith("bestmove "),
            label="cross-feed adapter source bestmove",
            timeout=90.0,
        )

        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            discovery = discover_replay_bundles(replay_root)
            candidates = [path for path in discovery.bundles if path.name not in known]
            if candidates:
                candidate = candidates[-1]
                if (candidate / "crossfeed" / "manifest.json").is_file():
                    return candidate
            time.sleep(0.05)

    raise ContractError("adapter source run did not seal cross-feed evidence")


def _run_operation(spec, operation) -> str:
    with UciSession(
        spec.binary,
        cwd=spec.cwd,
        timeout=30.0,
        args=list(spec.args),
    ) as session:
        session.configure(dict(spec.options))
        session.new_game()
        session.send(operation.position_command)
        session.send(operation.go_command)
        lines = session.read_until(
            lambda line: line.startswith("bestmove "),
            label=f"{spec.name} adapter operation",
            timeout=45.0,
        )
        return _bestmove(lines)


def _choose_refine_prefix(evidence) -> tuple[str, ...]:
    candidates = [
        tuple(hint.pv_prefix[:2])
        for hint in evidence.hints
        if hint.stage_disposition == "completed"
        and len(hint.pv_prefix) >= 2
        and hint.pv_prefix[0] in evidence.candidate_roots
    ]
    if not candidates:
        raise ContractError("sealed adapter evidence contains no two-move PV prefix")
    return sorted(set(candidates))[0]


def main() -> int:
    config = load_runtime_config(CONFIG_PATH)
    if config.shadow is None:
        raise ContractError("cross-feed validation config has no shadow instances")

    run_dir = _seal_source_run(config)
    source_problems = verify_crossfeed_integrity(run_dir)
    if source_problems:
        raise ContractError(f"source cross-feed integrity failed: {source_problems}")

    evidence = build_adapter_evidence_from_run(run_dir)
    if evidence.evidence_faults:
        raise ContractError(
            f"adapter source evidence is faulted: {list(evidence.evidence_faults)}"
        )
    if len(evidence.candidate_roots) < 2:
        raise ContractError(
            f"adapter source has too few candidate roots: {evidence.candidate_roots}"
        )

    position = parse_position_command("position startpos")
    roots = evidence.candidate_roots
    subset = (roots[0], roots[-1])
    prefix = _choose_refine_prefix(evidence)
    adapters = {
        "stockfish": StockfishCrossFeedAdapter(),
        "reckless": RecklessCrossFeedAdapter(),
        "lc0": Lc0CrossFeedAdapter(),
    }

    results: dict[str, object] = {}
    for family, adapter in adapters.items():
        instance = config.shadow.instance_by_owner[family]
        spec = config.backends[instance]

        verify = adapter.compile_verify_set(
            evidence,
            position,
            roots,
            limit={"nodes": 64},
        )
        verify_move = _run_operation(spec, verify)
        if verify_move not in verify.searchmoves:
            raise ContractError(
                f"{family} VERIFY escaped adapter searchmoves: {verify_move}"
            )

        subset_operation = adapter.compile_verify_set(
            evidence,
            position,
            subset,
            limit={"nodes": 64},
        )
        subset_move = _run_operation(spec, subset_operation)
        if subset_move not in subset_operation.searchmoves:
            raise ContractError(
                f"{family} subset VERIFY escaped adapter searchmoves: {subset_move}"
            )

        refine = adapter.compile_refine_prefix(
            evidence,
            position,
            prefix,
            limit={"nodes": 64},
        )
        refine_move = _run_operation(spec, refine)
        if refine_move != prefix[-1]:
            raise ContractError(
                f"{family} REFINE_PREFIX escaped certified prefix: {refine_move}"
            )

        results[family] = {
            "verify_operation": verify.as_dict(),
            "verify_bestmove": verify_move,
            "subset_operation": subset_operation.as_dict(),
            "subset_bestmove": subset_move,
            "refine_operation": refine.as_dict(),
            "refine_bestmove": refine_move,
            "priority_hint_count": len(adapter.priority_hints(evidence)),
            "tactical_alarm_count": len(adapter.tactical_alarms(evidence)),
        }

    parent = load_manifest(run_dir)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "config": str(CONFIG_PATH.relative_to(ROOT)),
        "source_run_id": parent["run_id"],
        "source_view_digest": evidence.source_view_digest,
        "adapter_evidence_digest": evidence.digest,
        "candidate_roots": list(roots),
        "refine_prefix": list(prefix),
        "families": results,
        "claim": (
            "Subprocess-safety qualification only: a sealed typed cross-feed run "
            "was reconstructed into adapter evidence, then each engine accepted "
            "deterministic adapter-generated VERIFY_SET and REFINE_PREFIX "
            "restrictions and returned inside the declared region. The adapters "
            "were not wired into controller routing, resource authorization, "
            "DecisionAuthorization or outward move selection. No Elo or strength "
            "claim."
        ),
    }
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "cross-feed adapter contract passed: "
        f"run={parent['run_id']}, prefix={' '.join(prefix)}, "
        + ", ".join(
            f"{family}={entry['verify_bestmove']}"
            for family, entry in results.items()
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractError as exc:
        print(f"cross-feed adapter contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
