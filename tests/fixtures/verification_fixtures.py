"""Synthetic parent+VERIFY fixtures for COMPARE / RELOCK tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from controller.replay import sha256_file
from tests.fixtures.replay_fixtures import (
    BundleScript,
    EngineScript,
    STARTPOS_FEN,
    _stage_events,
    write_bundle,
)


OWNERS = ("stockfish", "reckless", "lc0")
INSTANCES = {
    "stockfish": "stockfish-shadow",
    "reckless": "reckless-shadow",
    "lc0": "lc0-shadow",
}
FAMILIES = {
    "stockfish": "stockfish",
    "reckless": "reckless",
    "lc0": "lc0",
}
CANDIDATES = ("e2e4", "d2d4", "g1f3")
NOMINEES = dict(zip(OWNERS, CANDIDATES))


def _parent(root: Path, run_id: str, *, anchor_move: str = "e2e4") -> Path:
    return write_bundle(
        BundleScript(
            run_id=run_id,
            scripts=[
                EngineScript(
                    "stockfish-anchor",
                    "stockfish",
                    "anchor",
                    None,
                    (),
                    ((anchor_move,),) * 4,
                    bestmove=anchor_move,
                ),
                EngineScript(
                    "stockfish-shadow",
                    "stockfish",
                    "shadow",
                    "stockfish",
                    ("e2e4", "c2c4"),
                    (("e2e4", "c2c4"),) * 4,
                    bestmove="e2e4",
                ),
                EngineScript(
                    "reckless-shadow",
                    "reckless",
                    "shadow",
                    "reckless",
                    ("d2d4", "b1c3"),
                    (("d2d4", "b1c3"),) * 4,
                    bestmove="d2d4",
                ),
                EngineScript(
                    "lc0-shadow",
                    "lc0",
                    "shadow",
                    "lc0",
                    ("g1f3", "g2g3"),
                    (("g1f3", "g2g3"),) * 4,
                    bestmove="g1f3",
                ),
            ],
        ),
        root,
    )


def _shift_events(events: list[dict], start_ms: float) -> list[dict]:
    shifted: list[dict] = []
    for event in events:
        value = json.loads(json.dumps(event))
        if "observed_ms" in value:
            value["observed_ms"] = float(value["observed_ms"]) + start_ms
        if value.get("event_type") == "search.started":
            controller = value.setdefault("controller", {})
            controller["phase"] = "VERIFY"
            controller["execution_mode"] = "shadow"
            controller["instance_role"] = "shadow"
            controller["decision_authority"] = False
        shifted.append(value)
    return shifted


def attach_verification(
    run_dir: Path,
    rankings_by_owner: dict[str, Sequence[Sequence[str]]],
    *,
    bestmoves: dict[str, str] | None = None,
    starts_ms: dict[str, float] | None = None,
    complete_by_owner: dict[str, bool] | None = None,
    disposition: str | None = None,
) -> Path:
    parent = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    position_id = parent["position"]["position_id"]
    variant = parent["position"]["variant"]
    starts_ms = starts_ms or {
        "stockfish": 200.0,
        "reckless": 205.0,
        "lc0": 210.0,
    }
    complete_by_owner = complete_by_owner or {owner: True for owner in OWNERS}
    bestmoves = bestmoves or {
        owner: tuple(rankings_by_owner[owner])[-1][0] for owner in OWNERS
    }

    verify_dir = run_dir / "verification"
    verify_dir.mkdir(parents=True, exist_ok=True)
    stages: list[dict] = []
    streams: list[dict] = []

    for order, owner in enumerate(OWNERS, 1):
        instance = INSTANCES[owner]
        search_id = f"{parent['run_id']}:verify:{instance}:0"
        script = EngineScript(
            instance=instance,
            family=FAMILIES[owner],
            role="shadow",
            owner=owner,
            roots=CANDIDATES,
            rankings=tuple(tuple(ranking) for ranking in rankings_by_owner[owner]),
            bestmove=bestmoves[owner],
            complete=complete_by_owner.get(owner, True),
        )
        events = _stage_events(
            script,
            search_id=search_id,
            position_id=position_id,
            variant=variant,
        )
        events = _shift_events(events, starts_ms[owner])
        path = verify_dir / f"{instance}.jsonl"
        path.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
            encoding="utf-8",
        )
        completed_event = next(
            (event for event in reversed(events) if event.get("event_type") == "search.complete"),
            None,
        )
        completed_ms = None if completed_event is None else float(completed_event["observed_ms"])
        stage_disposition = "completed" if completed_event is not None else "failed"
        stages.append(
            {
                "owner": owner,
                "instance": instance,
                "family": FAMILIES[owner],
                "search_id": search_id,
                "command": "go nodes 12000 searchmoves " + " ".join(CANDIDATES),
                "candidate_roots": list(CANDIDATES),
                "dispatch_order": order,
                "dispatched_ms": starts_ms[owner],
                "completed_ms": completed_ms,
                "completion_order": order if completed_ms is not None else None,
                "disposition": stage_disposition,
                "bestmove": bestmoves[owner] if completed_ms is not None else None,
                "stop_reason": None,
                "failure": None if completed_ms is not None else "synthetic incomplete verifier",
            }
        )
        streams.append(
            {
                "instance": instance,
                "engine": FAMILIES[owner],
                "role": "shadow",
                "path": path.name,
                "search_ids": [search_id],
                "event_count": len(events),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "complete": completed_ms is not None,
                "contract_validatable": completed_ms is not None,
                "dropped_events": 0,
                "post_complete_lines": 0,
                "queued_peak": 0,
                "live_view_truncated": False,
                "adapter_errors": [],
            }
        )

    if disposition is None:
        disposition = (
            "completed"
            if all(complete_by_owner.get(owner, True) for owner in OWNERS)
            else "incomplete"
        )

    manifest = {
        "schema_version": 1,
        "verification_id": f"{parent['run_id']}:verify-v1",
        "source": {
            "run_id": parent["run_id"],
            "manifest_sha256": sha256_file(run_dir / "manifest.json"),
        },
        "generation": parent["generation"],
        "position_id": position_id,
        "nomination": {
            "method": "owner_bestmove_union_v1",
            "nominees_by_owner": dict(NOMINEES),
            "candidate_roots": list(CANDIDATES),
        },
        "participants": dict(INSTANCES),
        "dispatch_limit": {"nodes": 12000},
        "stages": stages,
        "streams": streams,
        "disposition": {"run": disposition, "stop_reason": None},
        "notes": [],
    }
    (verify_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return run_dir


def unanimous_on_reckless(root: Path) -> Path:
    run = _parent(root, "verify-unanimous-reckless")
    rankings = {
        "stockfish": (
            ("e2e4", "d2d4", "g1f3"),
            ("d2d4", "e2e4", "g1f3"),
            ("d2d4", "g1f3", "e2e4"),
        ),
        "reckless": (("d2d4", "e2e4", "g1f3"),) * 3,
        "lc0": (
            ("g1f3", "d2d4", "e2e4"),
            ("d2d4", "g1f3", "e2e4"),
            ("d2d4", "e2e4", "g1f3"),
        ),
    }
    return attach_verification(run, rankings, bestmoves={owner: "d2d4" for owner in OWNERS})


def two_one_split(root: Path) -> Path:
    run = _parent(root, "verify-two-one")
    rankings = {
        "stockfish": (("e2e4", "d2d4", "g1f3"),) * 3,
        "reckless": (("e2e4", "d2d4", "g1f3"),) * 3,
        "lc0": (("g1f3", "d2d4", "e2e4"),) * 3,
    }
    return attach_verification(
        run,
        rankings,
        bestmoves={"stockfish": "e2e4", "reckless": "e2e4", "lc0": "g1f3"},
    )


def all_different(root: Path) -> Path:
    run = _parent(root, "verify-all-different")
    rankings = {
        "stockfish": (("e2e4", "d2d4", "g1f3"),) * 3,
        "reckless": (("d2d4", "e2e4", "g1f3"),) * 3,
        "lc0": (("g1f3", "d2d4", "e2e4"),) * 3,
    }
    return attach_verification(run, rankings)


def temporary_unanimity_then_diverge(root: Path) -> Path:
    run = _parent(root, "verify-temp-unanimity")
    rankings = {
        "stockfish": (
            ("d2d4", "e2e4", "g1f3"),
            ("d2d4", "e2e4", "g1f3"),
            ("e2e4", "d2d4", "g1f3"),
        ),
        "reckless": (("d2d4", "e2e4", "g1f3"),) * 3,
        "lc0": (
            ("d2d4", "g1f3", "e2e4"),
            ("d2d4", "g1f3", "e2e4"),
            ("g1f3", "d2d4", "e2e4"),
        ),
    }
    return attach_verification(
        run,
        rankings,
        bestmoves={"stockfish": "e2e4", "reckless": "d2d4", "lc0": "g1f3"},
    )


def late_relock(root: Path) -> Path:
    run = _parent(root, "verify-late-relock")
    rankings = {
        owner: (
            (NOMINEES[owner], "d2d4", "g1f3" if owner == "stockfish" else "e2e4")
            if owner != "reckless"
            else ("d2d4", "e2e4", "g1f3"),
            ("d2d4", "e2e4", "g1f3"),
            ("d2d4", "g1f3", "e2e4"),
        )
        for owner in OWNERS
    }
    return attach_verification(run, rankings, bestmoves={owner: "d2d4" for owner in OWNERS})




def bestmove_only_relock(root: Path) -> Path:
    run = _parent(root, "verify-bestmove-only")
    rankings = {owner: () for owner in OWNERS}
    return attach_verification(
        run,
        rankings,
        bestmoves={owner: "d2d4" for owner in OWNERS},
    )


def incomplete_verifier(root: Path) -> Path:
    run = _parent(root, "verify-incomplete")
    rankings = {
        "stockfish": (("e2e4", "d2d4", "g1f3"),) * 3,
        "reckless": (("d2d4", "e2e4", "g1f3"),) * 3,
        "lc0": (("g1f3", "d2d4", "e2e4"),) * 3,
    }
    return attach_verification(
        run,
        rankings,
        complete_by_owner={"stockfish": True, "reckless": True, "lc0": False},
        disposition="incomplete",
    )


def anchor_outside_candidates(root: Path) -> Path:
    run = _parent(root, "verify-anchor-outside", anchor_move="a2a3")
    rankings = {
        "stockfish": (("e2e4", "d2d4", "g1f3"),) * 3,
        "reckless": (("d2d4", "e2e4", "g1f3"),) * 3,
        "lc0": (("g1f3", "d2d4", "e2e4"),) * 3,
    }
    return attach_verification(run, rankings)


SCENARIOS = {
    "unanimous_on_reckless": unanimous_on_reckless,
    "two_one_split": two_one_split,
    "all_different": all_different,
    "temporary_unanimity_then_diverge": temporary_unanimity_then_diverge,
    "late_relock": late_relock,
    "bestmove_only_relock": bestmove_only_relock,
    "incomplete_verifier": incomplete_verifier,
    "anchor_outside_candidates": anchor_outside_candidates,
}


def write_all(root: Path) -> dict[str, Path]:
    return {name: builder(Path(root)) for name, builder in SCENARIOS.items()}
