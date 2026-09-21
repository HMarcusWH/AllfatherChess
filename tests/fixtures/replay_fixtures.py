"""Deterministic synthetic replay bundles for residual and routing tests.

These builders write real telemetry v1 streams and a real replay manifest, so
the analysis layer is exercised through exactly the same path as live evidence.

Scenarios can give two workers an overlapping region. Live shadow mode never
does that, but the residual library must still be correct when a later
VERIFY/COMPARE phase produces shared support, so the fixtures cover it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from adapters.telemetry import (
    Lc0TelemetryAdapter,
    RecklessTelemetryAdapter,
    StockfishTelemetryAdapter,
)
from common.telemetry import STARTPOS_FEN

ADAPTERS = {
    "stockfish": StockfishTelemetryAdapter,
    "reckless": RecklessTelemetryAdapter,
    "lc0": Lc0TelemetryAdapter,
}

CHESS960_FEN = "bqnbnrkr/pppppppp/8/8/8/8/PPPPPPPP/BQNBNRKR w KQkq - 0 1"


@dataclass
class EngineScript:
    """One engine instance's scripted observation sequence."""

    instance: str
    family: str
    role: str
    owner: str | None
    roots: tuple[str, ...]
    #: One entry per iteration: the ranked moves reported at that iteration.
    rankings: tuple[tuple[str, ...], ...]
    #: Parallel to `rankings`: the primary line's score in this engine's scale.
    scores: tuple[int, ...] = ()
    pv_tail: tuple[str, ...] = ("e7e5", "g1f3")
    bestmove: str | None = None
    complete: bool = True
    #: Stage ordinal on this instance. Active routing can dispatch more than
    #: one, and every stage of an instance shares a single JSONL file.
    stage_index: int = 0
    #: Emit this many raw lines then stop, simulating a crashed worker.
    truncate_after: int | None = None
    malformed_lines: tuple[str, ...] = ()
    nodes_per_iteration: int = 1000


@dataclass
class BundleScript:
    run_id: str
    scripts: list[EngineScript]
    variant: str = "standard"
    base_fen: str = STARTPOS_FEN
    moves: tuple[str, ...] = ()
    external_command: str = "go movetime 800"
    anchor_instance: str = "stockfish-anchor"
    disposition: str = "completed"
    terminal: bool = False
    owner_roots: dict[str, list[str]] = field(default_factory=dict)
    missing_stream_instances: tuple[str, ...] = ()


def _position_id(variant: str, base_fen: str, moves: Sequence[str]) -> str:
    payload = f"{variant}|{base_fen}|{' '.join(moves)}"
    return "pos-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _adapter(script: EngineScript, *, search_id: str, position_id: str, variant: str):
    cls = ADAPTERS[script.family]
    kwargs: dict[str, Any] = {
        "search_id": search_id,
        "engine_instance": script.instance,
        "position_id": position_id,
        "variant": variant,
    }
    if script.family == "lc0":
        kwargs["score_type"] = "centipawn"
    return cls(**kwargs)


def _write_stages(
    path: Path,
    scripts: Sequence["EngineScript"],
    *,
    run_id: str,
    position_id: str,
    variant: str,
) -> int:
    """Write every stage of one instance into a single telemetry file."""
    total = 0
    chunks: list[str] = []
    for script in scripts:
        search_id = f"{run_id}:{script.instance}:{script.stage_index}"
        events = _stage_events(
            script, search_id=search_id, position_id=position_id, variant=variant
        )
        total += len(events)
        chunks.append("".join(json.dumps(event, sort_keys=True) + "\n" for event in events))
    path.write_text("".join(chunks), encoding="utf-8")
    return total


def _stage_events(script: EngineScript, *, search_id: str, position_id: str, variant: str) -> list:
    adapter = _adapter(script, search_id=search_id, position_id=position_id, variant=variant)
    request: dict[str, Any] = {
        "limits": [{"name": "nodes", "value": 20000, "semantics": "uci.go.nodes"}],
        "raw": f"go nodes 20000 searchmoves {' '.join(script.roots)}" if script.roots else "go nodes 20000",
    }
    if script.roots:
        request["root_moves"] = list(script.roots)
    controller = {
        "execution_mode": "baseline" if script.role == "anchor" else "shadow",
        "instance_role": script.role,
        "decision_authority": script.role == "anchor",
    }
    if script.owner:
        controller["owner"] = script.owner
        controller["phase"] = "EXPLORE"

    events = [
        adapter.start(
            position={"base_fen": "" if False else _base_fen_for(script, variant), "moves": []},
            request=request,
            observed_ms=0,
            controller=controller,
        )
    ]

    emitted = 0
    observed = 0.0
    for index, ranking in enumerate(script.rankings):
        score = script.scores[index] if index < len(script.scores) else 10 * (index + 1)
        for multipv, move in enumerate(ranking, start=1):
            observed += 10.0
            line = (
                f"info depth {index + 1} multipv {multipv} "
                f"nodes {script.nodes_per_iteration * (index + 1)} "
                f"score cp {score - 5 * (multipv - 1)} pv {move} {' '.join(script.pv_tail)}"
            )
            if script.truncate_after is not None and emitted >= script.truncate_after:
                return events
            events.extend(adapter.consume(line, observed_ms=observed))
            emitted += 1

    for raw in script.malformed_lines:
        observed += 10.0
        events.extend(adapter.consume(raw, observed_ms=observed))

    if script.complete:
        observed += 10.0
        bestmove = script.bestmove or (script.rankings[-1][0] if script.rankings else "0000")
        events.extend(adapter.consume(f"bestmove {bestmove}", observed_ms=observed))

    return events


def _stage_record(engine: "EngineScript", *, search_id: str, order: int) -> dict[str, Any]:
    return {
        "stage_index": engine.stage_index,
        "instance": engine.instance,
        "engine": engine.family,
        "role": engine.role,
        "owner": engine.owner,
        "search_id": search_id,
        "command": "go nodes 20000"
        + (f" searchmoves {' '.join(engine.roots)}" if engine.roots else ""),
        "request": {"limits": [], "raw": "go nodes 20000"},
        "dispatched_roots": list(engine.roots),
        "dispatch_order": order,
        "dispatched_ms": float(order),
        "completed_ms": 100.0 + order,
        "completion_order": order,
        "disposition": "completed" if engine.complete else "failed",
        "stop_reason": None,
        "bestmove": engine.bestmove
        or (engine.rankings[-1][0] if engine.rankings and engine.complete else None),
        "failure": None if engine.complete else "synthetic shadow failure",
    }


def _base_fen_for(script: EngineScript, variant: str) -> str:
    return CHESS960_FEN if variant == "chess960" else STARTPOS_FEN


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_bundle(script: BundleScript, root: Path) -> Path:
    """Materialize a complete replay bundle and return its run directory."""
    run_dir = Path(root) / script.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    position_id = _position_id(script.variant, script.base_fen, script.moves)

    owner_roots = dict(script.owner_roots)
    if not owner_roots:
        owner_roots = {
            engine.owner: list(engine.roots)
            for engine in script.scripts
            if engine.owner is not None
        }

    all_roots: list[str] = []
    for moves in owner_roots.values():
        all_roots += list(moves)
    universe = sorted(set(all_roots))

    by_instance: dict[str, list[EngineScript]] = {}
    for engine in script.scripts:
        by_instance.setdefault(engine.instance, []).append(engine)

    streams: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    for order, engine in enumerate(script.scripts, start=1):
        search_id = f"{script.run_id}:{engine.instance}:{engine.stage_index}"
        path = run_dir / f"{engine.instance}.jsonl"
        siblings = by_instance[engine.instance]
        if engine is not siblings[0]:
            # Every stage of an instance already went into the file written for
            # its first stage; only one stream record exists per instance.
            stages.append(_stage_record(engine, search_id=search_id, order=order))
            continue
        count = _write_stages(
            path,
            siblings,
            run_id=script.run_id,
            position_id=position_id,
            variant=script.variant,
        )
        streams.append(
            {
                "instance": engine.instance,
                "engine": engine.family,
                "role": engine.role,
                "path": path.name,
                "search_ids": [
                    f"{script.run_id}:{item.instance}:{item.stage_index}" for item in siblings
                ],
                "event_count": count,
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
                "complete": siblings[-1].complete,
                "contract_validatable": all(
                    item.complete and not item.malformed_lines for item in siblings
                ),
                "dropped_events": 0,
                "post_complete_lines": 0,
                "queued_peak": 0,
                "live_view_truncated": False,
                "adapter_errors": [],
            }
        )
        stages.append(_stage_record(engine, search_id=search_id, order=order))

    for instance in script.missing_stream_instances:
        streams = [record for record in streams if record["instance"] != instance]
        (run_dir / f"{instance}.jsonl").unlink(missing_ok=True)

    shards = [
        {
            "id": f"g000001:r{index:03d}:{move}",
            "ordinal": index,
            "prefix": [move],
            "owner": next(
                (owner for owner, moves in owner_roots.items() if move in moves), None
            ),
            "state": "sealed",
        }
        for index, move in enumerate(universe)
    ]
    ledger_snapshot = {
        "schema_version": 1,
        "generation": 1,
        "revision": 7,
        "partition_assigned": True,
        "owners": sorted(owner_roots),
        "candidate_roots": universe,
        "shards": shards,
    }

    manifest = {
        "schema_version": 1,
        "run_id": script.run_id,
        "generation": 1,
        "created_utc": "2026-01-01T00:00:00Z",
        "controller": {
            "mode": "shadow",
            "telemetry_execution_mode": "shadow",
            "config_path": "synthetic",
            "config_sha256": "0" * 64,
            "partition_method": "root_index_modulo",
            "overhead": {"prepare_ms": 0.5, "qualification_ms": 1.0},
        },
        "position": {
            "position_id": position_id,
            "variant": script.variant,
            "move_encoding": "uci" if script.variant == "standard" else "uci_chess960",
            "base_fen": script.base_fen,
            "moves": list(script.moves),
            "command": "position startpos",
        },
        "external_request": {
            "command": script.external_command,
            "request": {"limits": [], "raw": script.external_command},
        },
        "engines": {
            engine.instance: {
                "engine": engine.family,
                "role": engine.role,
                "binary": f"/synthetic/{engine.family}",
                "binary_sha256": None,
                "args": [],
                "options": {},
            }
            for engine in script.scripts
        },
        "legal_root_oracle": {
            "instance": "stockfish-shadow",
            "root_count": 0 if script.terminal else len(universe),
            "terminal_universe": script.terminal,
        },
        "ledger": {
            "owners": sorted(owner_roots),
            "owner_roots": {owner: list(moves) for owner, moves in sorted(owner_roots.items())},
            "pre_dispatch_snapshot": None if script.terminal else ledger_snapshot,
            "post_run_snapshot": None if script.terminal else ledger_snapshot,
        },
        "stages": stages,
        "streams": streams,
        "shadow_health": {
            engine.instance: {
                "instance": engine.instance,
                "alive": engine.complete,
                "failure": None if engine.complete else "synthetic shadow failure",
                "failed_generation": None if engine.complete else 1,
            }
            for engine in script.scripts
            if engine.role == "shadow"
        },
        "disposition": {"run": script.disposition, "stop_reason": None},
        "notes": [],
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return run_dir


# ---------------------------------------------------------------------------
# Named scenarios
# ---------------------------------------------------------------------------

_ANCHOR_ROOTS: tuple[str, ...] = ()


def _anchor(rankings, *, scores=(), bestmove=None, complete=True) -> EngineScript:
    return EngineScript(
        instance="stockfish-anchor",
        family="stockfish",
        role="anchor",
        owner=None,
        roots=_ANCHOR_ROOTS,
        rankings=rankings,
        scores=scores,
        bestmove=bestmove,
        complete=complete,
    )


def stable_agreement(root: Path) -> Path:
    """Every worker's leader is settled from the first observation."""
    return write_bundle(
        BundleScript(
            run_id="synthetic-stable-agreement",
            scripts=[
                _anchor((("e2e4",),) * 5, scores=(30, 31, 32, 32, 33)),
                EngineScript(
                    "stockfish-shadow", "stockfish", "shadow", "stockfish",
                    ("e2e4", "b1c3"), (("e2e4", "b1c3"),) * 5, scores=(30, 31, 32, 32, 33),
                ),
                EngineScript(
                    "reckless-shadow", "reckless", "shadow", "reckless",
                    ("d2d4", "c2c4"), (("d2d4", "c2c4"),) * 5, scores=(20, 21, 22, 22, 23),
                ),
                EngineScript(
                    "lc0-shadow", "lc0", "shadow", "lc0",
                    ("g1f3", "g2g3"), (("g1f3", "g2g3"),) * 5, scores=(15, 16, 17, 17, 18),
                ),
            ],
        ),
        root,
    )


def transient_disagreement(root: Path) -> Path:
    """A worker's leader wobbles mid-search but returns to its final choice."""
    return write_bundle(
        BundleScript(
            run_id="synthetic-transient-disagreement",
            scripts=[
                _anchor((("e2e4",), ("e2e4",), ("d2d4",), ("e2e4",), ("e2e4",))),
                EngineScript(
                    "stockfish-shadow", "stockfish", "shadow", "stockfish",
                    ("e2e4", "b1c3"),
                    (("e2e4", "b1c3"), ("b1c3", "e2e4"), ("e2e4", "b1c3"), ("e2e4", "b1c3")),
                ),
                EngineScript(
                    "reckless-shadow", "reckless", "shadow", "reckless",
                    ("d2d4", "c2c4"), (("d2d4", "c2c4"),) * 4,
                ),
                EngineScript(
                    "lc0-shadow", "lc0", "shadow", "lc0",
                    ("g1f3", "g2g3"), (("g1f3", "g2g3"),) * 4,
                ),
            ],
        ),
        root,
    )


def late_reversal(root: Path) -> Path:
    """The leader changes on the final iteration: the case early stopping misses."""
    return write_bundle(
        BundleScript(
            run_id="synthetic-late-reversal",
            scripts=[
                _anchor((("e2e4",), ("e2e4",), ("e2e4",), ("e2e4",), ("d2d4",)), bestmove="d2d4"),
                EngineScript(
                    "stockfish-shadow", "stockfish", "shadow", "stockfish",
                    ("e2e4", "b1c3"),
                    (("e2e4", "b1c3"), ("e2e4", "b1c3"), ("e2e4", "b1c3"), ("b1c3", "e2e4")),
                    bestmove="b1c3",
                ),
                EngineScript(
                    "reckless-shadow", "reckless", "shadow", "reckless",
                    ("d2d4", "c2c4"), (("d2d4", "c2c4"),) * 4,
                ),
                EngineScript(
                    "lc0-shadow", "lc0", "shadow", "lc0",
                    ("g1f3", "g2g3"), (("g1f3", "g2g3"),) * 4,
                ),
            ],
        ),
        root,
    )


def lc0_only_divergence(root: Path) -> Path:
    """Overlapping regions where only the MCTS worker disagrees."""
    shared = ("e2e4", "d2d4", "g1f3")
    return write_bundle(
        BundleScript(
            run_id="synthetic-lc0-only-divergence",
            scripts=[
                _anchor((("e2e4",),) * 4),
                EngineScript(
                    "stockfish-shadow", "stockfish", "shadow", "stockfish",
                    shared, (("e2e4", "d2d4", "g1f3"),) * 4,
                ),
                EngineScript(
                    "reckless-shadow", "reckless", "shadow", "reckless",
                    shared, (("e2e4", "d2d4", "g1f3"),) * 4,
                ),
                EngineScript(
                    "lc0-shadow", "lc0", "shadow", "lc0",
                    shared, (("g1f3", "d2d4", "e2e4"),) * 4,
                ),
            ],
            owner_roots={
                "stockfish": list(shared),
                "reckless": list(shared),
                "lc0": list(shared),
            },
        ),
        root,
    )


def alpha_beta_only_divergence(root: Path) -> Path:
    """Overlapping regions where the two alpha-beta workers disagree."""
    shared = ("e2e4", "d2d4", "g1f3")
    return write_bundle(
        BundleScript(
            run_id="synthetic-alpha-beta-only-divergence",
            scripts=[
                _anchor((("e2e4",),) * 4),
                EngineScript(
                    "stockfish-shadow", "stockfish", "shadow", "stockfish",
                    shared, (("e2e4", "d2d4", "g1f3"),) * 4,
                ),
                EngineScript(
                    "reckless-shadow", "reckless", "shadow", "reckless",
                    shared, (("d2d4", "e2e4", "g1f3"),) * 4,
                ),
                EngineScript(
                    "lc0-shadow", "lc0", "shadow", "lc0",
                    shared, (("e2e4", "d2d4", "g1f3"),) * 4,
                ),
            ],
            owner_roots={
                "stockfish": list(shared),
                "reckless": list(shared),
                "lc0": list(shared),
            },
        ),
        root,
    )


def failed_shadow_stream(root: Path) -> Path:
    """One worker dies mid-search: its stream has no `search.complete`."""
    return write_bundle(
        BundleScript(
            run_id="synthetic-failed-shadow",
            disposition="completed",
            scripts=[
                _anchor((("e2e4",),) * 4),
                EngineScript(
                    "stockfish-shadow", "stockfish", "shadow", "stockfish",
                    ("e2e4", "b1c3"), (("e2e4", "b1c3"),) * 4,
                ),
                EngineScript(
                    "reckless-shadow", "reckless", "shadow", "reckless",
                    ("d2d4", "c2c4"), (("d2d4", "c2c4"),) * 4,
                ),
                EngineScript(
                    "lc0-shadow", "lc0", "shadow", "lc0",
                    ("g1f3", "g2g3"), (("g1f3", "g2g3"),) * 4,
                    complete=False, truncate_after=2,
                ),
            ],
        ),
        root,
    )


def missing_shadow_stream(root: Path) -> Path:
    """The manifest declares a stage whose stream file is absent."""
    return write_bundle(
        BundleScript(
            run_id="synthetic-missing-stream",
            missing_stream_instances=("lc0-shadow",),
            scripts=[
                _anchor((("e2e4",),) * 4),
                EngineScript(
                    "stockfish-shadow", "stockfish", "shadow", "stockfish",
                    ("e2e4", "b1c3"), (("e2e4", "b1c3"),) * 4,
                ),
                EngineScript(
                    "reckless-shadow", "reckless", "shadow", "reckless",
                    ("d2d4", "c2c4"), (("d2d4", "c2c4"),) * 4,
                ),
                EngineScript(
                    "lc0-shadow", "lc0", "shadow", "lc0",
                    ("g1f3", "g2g3"), (("g1f3", "g2g3"),) * 4,
                ),
            ],
        ),
        root,
    )


def terminal_position(root: Path) -> Path:
    """An empty legal-root universe: anchor only, no shadow dispatch."""
    return write_bundle(
        BundleScript(
            run_id="synthetic-terminal",
            terminal=True,
            disposition="terminal_no_dispatch",
            owner_roots={},
            scripts=[_anchor((), bestmove="0000")],
        ),
        root,
    )


def chess960_run(root: Path) -> Path:
    """Chess960 encoding must survive reconstruction unchanged."""
    return write_bundle(
        BundleScript(
            run_id="synthetic-chess960",
            variant="chess960",
            base_fen=CHESS960_FEN,
            scripts=[
                _anchor((("g1h1",),) * 3),
                EngineScript(
                    "stockfish-shadow", "stockfish", "shadow", "stockfish",
                    ("g1h1", "b1c3"), (("g1h1", "b1c3"),) * 3,
                ),
                EngineScript(
                    "reckless-shadow", "reckless", "shadow", "reckless",
                    ("d2d4", "c2c4"), (("d2d4", "c2c4"),) * 3,
                ),
                EngineScript(
                    "lc0-shadow", "lc0", "shadow", "lc0",
                    ("e2e4", "f2f4"), (("e2e4", "f2f4"),) * 3,
                ),
            ],
        ),
        root,
    )


def malformed_telemetry(root: Path) -> Path:
    """Unparseable engine output must be preserved, not silently reinterpreted."""
    return write_bundle(
        BundleScript(
            run_id="synthetic-malformed",
            scripts=[
                _anchor((("e2e4",),) * 3),
                EngineScript(
                    "stockfish-shadow", "stockfish", "shadow", "stockfish",
                    ("e2e4", "b1c3"), (("e2e4", "b1c3"),) * 3,
                    malformed_lines=("info depth score pv", "garbage line from engine"),
                ),
                EngineScript(
                    "reckless-shadow", "reckless", "shadow", "reckless",
                    ("d2d4", "c2c4"), (("d2d4", "c2c4"),) * 3,
                ),
                EngineScript(
                    "lc0-shadow", "lc0", "shadow", "lc0",
                    ("g1f3", "g2g3"), (("g1f3", "g2g3"),) * 3,
                ),
            ],
        ),
        root,
    )


def multi_stage_worker(root: Path) -> Path:
    """One worker dispatched twice, as active routing does when it extends."""
    return write_bundle(
        BundleScript(
            run_id="synthetic-multi-stage",
            scripts=[
                _anchor((("e2e4",),) * 4),
                EngineScript(
                    "stockfish-shadow", "stockfish", "shadow", "stockfish",
                    ("e2e4", "b1c3"), (("e2e4", "b1c3"),) * 3, stage_index=0,
                ),
                EngineScript(
                    "stockfish-shadow", "stockfish", "shadow", "stockfish",
                    ("e2e4", "b1c3"), (("b1c3", "e2e4"),) * 3,
                    stage_index=1, bestmove="b1c3",
                ),
                EngineScript(
                    "reckless-shadow", "reckless", "shadow", "reckless",
                    ("d2d4", "c2c4"), (("d2d4", "c2c4"),) * 3,
                ),
                EngineScript(
                    "lc0-shadow", "lc0", "shadow", "lc0",
                    ("g1f3", "g2g3"), (("g1f3", "g2g3"),) * 3,
                ),
            ],
        ),
        root,
    )


SCENARIOS = {
    "stable_agreement": stable_agreement,
    "transient_disagreement": transient_disagreement,
    "late_reversal": late_reversal,
    "lc0_only_divergence": lc0_only_divergence,
    "alpha_beta_only_divergence": alpha_beta_only_divergence,
    "failed_shadow_stream": failed_shadow_stream,
    "missing_shadow_stream": missing_shadow_stream,
    "terminal_position": terminal_position,
    "chess960_run": chess960_run,
    "malformed_telemetry": malformed_telemetry,
    "multi_stage_worker": multi_stage_worker,
}


def write_all(root: Path) -> dict[str, Path]:
    return {name: builder(Path(root)) for name, builder in SCENARIOS.items()}
