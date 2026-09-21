#!/usr/bin/env python3
"""Qualify concurrent shadow execution against the three real engines.

This contract establishes decision non-intervention at the outward Stockfish
boundary under a deterministic fixed-node request, and proves that three
restricted shadow workers stay inside their ledger-owned regions while an
unrestricted anchor holds sole outward authority.

It deliberately makes no timed-equivalence claim: concurrent shadow execution
perturbs wall-clock search through CPU/GPU contention, and no resource isolation
is established here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.replay import load_manifest, verify_bundle_integrity
from controller.runtime import load_runtime_config
from tests.harness.uci_session import UciError, UciSession


CONFIG_PATH = ROOT / "config" / "allfather.shadow.validation.json"
ANCHOR_CONFIG_PATH = ROOT / "config" / "allfather.validation.json"
CORPUS_PATH = ROOT / "tests" / "baseline" / "corpus.json"
LEGAL_PATH = ROOT / "tests" / "baseline" / "golden" / "legal_moves.json"
RESULT_DIR = ROOT / "build" / "test-results" / "shadow-execution"
_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")

FIXED_NODES = 512
CONCURRENCY_MOVETIME_MS = 1500


class ContractError(RuntimeError):
    pass


def _load_validator():
    path = ROOT / "scripts" / "validate-telemetry-contract.py"
    spec = importlib.util.spec_from_file_location("telemetry_contract_validator", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


VALIDATOR = _load_validator()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def position_payload(position: dict[str, Any]) -> dict[str, Any]:
    if "fen" in position:
        return {"fen": position["fen"]}
    return {"startpos_moves": list(position.get("startpos_moves", []))}


def extract_bestmove(lines: list[str], label: str) -> str:
    matches = [line.split()[1] for line in lines if line.startswith("bestmove ")]
    if len(matches) != 1:
        raise ContractError(f"{label}: expected exactly one bestmove, got {matches}")
    move = matches[0].lower()
    if move == "(none)":
        return move
    if not _MOVE_RE.fullmatch(move):
        raise ContractError(f"{label}: non-move bestmove {move!r}")
    return move


def newest_run(replay_root: Path, known: set[str]) -> Path:
    candidates = sorted(
        (path for path in replay_root.iterdir() if path.is_dir() and path.name not in known),
        key=lambda path: path.name,
    )
    if not candidates:
        raise ContractError("shadow run produced no replay bundle")
    return candidates[-1]


def stream_events(run_dir: Path, record: dict[str, Any]) -> list[dict[str, Any]]:
    text = (run_dir / record["path"]).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def assert_region_containment(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Every shadow candidate, PV head, and bestmove must stay inside its region."""
    owner_roots = manifest["ledger"]["owner_roots"]
    findings: dict[str, Any] = {}
    for record in manifest["streams"]:
        instance = record["instance"]
        if record["role"] != "shadow":
            continue
        events = stream_events(run_dir, record)
        started = events[0]
        owner = started["controller"]["owner"]
        allowed = set(owner_roots[owner])
        if not allowed:
            raise ContractError(f"{instance}: dispatched with an empty owned region")
        candidates: set[str] = set()
        for event in events:
            if event["event_type"] == "candidate.update":
                move = event["candidate"]["move"]
                pv_head = event["candidate"]["pv"][0]
                if move not in allowed:
                    raise ContractError(
                        f"{instance}: candidate {move} escaped owned region {sorted(allowed)}"
                    )
                if pv_head not in allowed:
                    raise ContractError(
                        f"{instance}: PV head {pv_head} escaped owned region {sorted(allowed)}"
                    )
                candidates.add(move)
            elif event["event_type"] == "search.complete":
                bestmove = event["bestmove"]
                if bestmove is not None and bestmove not in allowed:
                    raise ContractError(
                        f"{instance}: bestmove {bestmove} escaped owned region {sorted(allowed)}"
                    )
        findings[instance] = {
            "owner": owner,
            "allowed_roots": sorted(allowed),
            "observed_candidates": sorted(candidates),
            "candidate_count": len(candidates),
        }
    return findings


def assert_disjoint_exact(manifest: dict[str, Any]) -> None:
    owner_roots = manifest["ledger"]["owner_roots"]
    union: list[str] = []
    for moves in owner_roots.values():
        union += moves
    if len(union) != len(set(union)):
        raise ContractError("shadow exploration ownership is not pairwise disjoint")
    pre = manifest["ledger"]["pre_dispatch_snapshot"]
    if pre is None:
        raise ContractError("shadow run recorded no pre-dispatch ledger snapshot")
    if sorted(union) != sorted(pre["candidate_roots"]):
        raise ContractError(
            "shadow ownership does not exactly cover the qualified legal-root universe"
        )


def engine_processes_alive(config) -> list[str]:
    alive: list[str] = []
    for spec in config.backends.values():
        probe = subprocess.run(
            ["pgrep", "-f", str(spec.binary)],
            capture_output=True,
            text=True,
        )
        pids = [pid for pid in probe.stdout.split() if pid and int(pid) != os.getpid()]
        if pids:
            alive.append(f"{spec.name}:{','.join(pids)}")
    return alive


def main() -> int:
    config = load_runtime_config(CONFIG_PATH)
    anchor_config = load_runtime_config(ANCHOR_CONFIG_PATH)
    corpus = {case["id"]: case["position"] for case in load_json(CORPUS_PATH)["cases"]}
    golden = load_json(LEGAL_PATH)["cases"]
    replay_root = config.shadow.replay_root
    replay_root.mkdir(parents=True, exist_ok=True)
    known = {path.name for path in replay_root.iterdir() if path.is_dir()}

    report: dict[str, Any] = {
        "schema_version": 1,
        "config": str(CONFIG_PATH.relative_to(ROOT)),
        "mode": config.mode,
        "anchor_instance": config.anchor,
        "oracle_instance": config.shadow.oracle,
        "claims": {},
    }

    # ------------------------------------------------------------------
    # 1. Decision firewall: fixed-node anchor equivalence with shadows live.
    # ------------------------------------------------------------------
    stockfish = anchor_config.backends["stockfish"]
    with UciSession(
        stockfish.binary, cwd=stockfish.cwd, timeout=20.0, args=list(stockfish.args)
    ) as direct:
        direct.configure(stockfish.options)
        direct.new_game()
        direct.set_position({"startpos_moves": []})
        direct_move = extract_bestmove(
            direct.search_nodes(FIXED_NODES, timeout=30.0), "direct Stockfish"
        )

    with UciSession(
        Path(sys.executable),
        cwd=ROOT,
        timeout=30.0,
        args=["-m", "controller", "--config", str(CONFIG_PATH)],
    ) as shell:
        handshake = "\n".join(shell.transcript)
        if "id name AllfatherChess" not in handshake:
            raise ContractError("external shell did not identify as AllfatherChess")
        for leaked in ("id name Stockfish", "id name Reckless"):
            if leaked in handshake:
                raise ContractError(f"constituent identity leaked externally: {leaked}")
        if "id name lc0" in handshake.lower():
            raise ContractError("constituent identity leaked externally: lc0")

        shell.configure({"UCI_Chess960": False})
        shell.new_game()
        shell.set_position({"startpos_moves": []})
        shadow_fixed_lines = shell.search_nodes(FIXED_NODES, timeout=60.0)
        shadow_fixed_move = extract_bestmove(shadow_fixed_lines, "Allfather shadow-mode anchor")
        if shadow_fixed_move != direct_move:
            raise ContractError(
                "shadow logic changed the outward fixed-node decision: "
                f"direct={direct_move}, shadow-mode={shadow_fixed_move}"
            )

        # --------------------------------------------------------------
        # 2. Concurrency: a long enough anchor search to overlap dispatch.
        # --------------------------------------------------------------
        shell.new_game()
        shell.set_position({"startpos_moves": []})
        shell.send(f"go movetime {CONCURRENCY_MOVETIME_MS}")
        concurrent_lines = shell.read_until(
            lambda line: line.startswith("bestmove "),
            label="Allfather concurrent shadow bestmove",
            timeout=90.0,
        )
        concurrent_move = extract_bestmove(concurrent_lines, "Allfather concurrent anchor")

        # --------------------------------------------------------------
        # 3. Terminal universe on a real checkmate position.
        # --------------------------------------------------------------
        shell.new_game()
        shell.set_position(position_payload(corpus["checkmate_terminal"]))
        shell.send(f"go nodes {FIXED_NODES}")
        terminal_lines = shell.read_until(
            lambda line: line.startswith("bestmove "),
            label="Allfather terminal bestmove",
            timeout=60.0,
        )
        terminal_move = extract_bestmove(terminal_lines, "Allfather terminal")

    # ------------------------------------------------------------------
    # 4. Replay evidence for the concurrent run.
    # ------------------------------------------------------------------
    runs = sorted(
        (path for path in replay_root.iterdir() if path.is_dir() and path.name not in known),
        key=lambda path: path.name,
    )
    if len(runs) < 3:
        raise ContractError(f"expected three replay bundles, found {[p.name for p in runs]}")
    fixed_run, concurrent_run, terminal_run = runs[0], runs[1], runs[2]

    concurrent_manifest = load_manifest(concurrent_run)
    integrity = verify_bundle_integrity(concurrent_run)
    if integrity:
        raise ContractError(f"replay bundle integrity failure: {integrity}")

    shadow_stages = [s for s in concurrent_manifest["stages"] if s["role"] == "shadow"]
    if len(shadow_stages) != 3:
        raise ContractError(
            "expected three concurrent shadow stages, got "
            f"{[(s['instance'], s['disposition']) for s in shadow_stages]}"
        )
    assert_disjoint_exact(concurrent_manifest)
    containment = assert_region_containment(concurrent_run, concurrent_manifest)

    live_roots = sorted(concurrent_manifest["ledger"]["pre_dispatch_snapshot"]["candidate_roots"])
    frozen_roots = sorted(golden["startpos"])
    if live_roots != frozen_roots:
        raise ContractError(
            f"live legal-root universe differs from the frozen oracle: "
            f"live={live_roots}, frozen={frozen_roots}"
        )

    anchor_stages = [s for s in concurrent_manifest["stages"] if s["role"] == "anchor"]
    if len(anchor_stages) != 1 or anchor_stages[0]["dispatched_roots"]:
        raise ContractError("the outward anchor must run exactly once and unrestricted")

    # Concurrency evidence: at least one shadow stage was dispatched before the
    # anchor completed, which is what makes this a concurrent observation.
    anchor_completed_ms = anchor_stages[0]["completed_ms"]
    overlapping = [
        stage["instance"]
        for stage in shadow_stages
        if anchor_completed_ms is not None and stage["dispatched_ms"] < anchor_completed_ms
    ]
    if len(overlapping) != 3:
        raise ContractError(
            f"shadow workers did not overlap the anchor search: overlapping={overlapping}"
        )

    # ------------------------------------------------------------------
    # 5. Telemetry v1 conformance.
    # ------------------------------------------------------------------
    contract = VALIDATOR.load_contract()
    validated: list[str] = []
    for record in concurrent_manifest["streams"]:
        if not record["contract_validatable"]:
            raise ContractError(f"stream {record['instance']} is not contract-validatable")
        VALIDATOR.validate_stream(concurrent_run / record["path"], contract)
        validated.append(record["instance"])
    if sorted(validated) != sorted(
        ["stockfish-anchor", "stockfish-shadow", "reckless-shadow", "lc0-shadow"]
    ):
        raise ContractError(f"unexpected telemetry stream set: {sorted(validated)}")

    instances = {record["instance"]: record for record in concurrent_manifest["streams"]}
    families = {name: record["engine"] for name, record in instances.items()}
    if families["stockfish-anchor"] != "stockfish" or families["stockfish-shadow"] != "stockfish":
        raise ContractError("solver-family identity was not preserved for both Stockfish roles")
    if instances["stockfish-anchor"]["role"] == instances["stockfish-shadow"]["role"]:
        raise ContractError("anchor and shadow Stockfish roles were conflated")

    terminal_manifest = load_manifest(terminal_run)
    if terminal_manifest["legal_root_oracle"]["root_count"] != 0:
        raise ContractError("checkmate position did not produce an empty legal-root universe")
    if [s for s in terminal_manifest["stages"] if s["role"] == "shadow"]:
        raise ContractError("a terminal position dispatched shadow work")

    # ------------------------------------------------------------------
    # 6. No orphan processes.
    # ------------------------------------------------------------------
    orphans = engine_processes_alive(config)
    if orphans:
        raise ContractError(f"engine processes outlived the shell: {orphans}")

    report["claims"] = {
        "single_external_identity": True,
        "fixed_node_decision_firewall": {
            "direct_stockfish_bestmove": direct_move,
            "allfather_shadow_bestmove": shadow_fixed_move,
            "equivalent": direct_move == shadow_fixed_move,
            "nodes": FIXED_NODES,
        },
        "concurrent_run": {
            "run_id": concurrent_manifest["run_id"],
            "movetime_ms": CONCURRENCY_MOVETIME_MS,
            "outward_bestmove": concurrent_move,
            "anchor_completed_ms": anchor_completed_ms,
            "shadow_overlap": overlapping,
            "owner_roots": concurrent_manifest["ledger"]["owner_roots"],
            "shadow_stages": [
                {
                    "instance": stage["instance"],
                    "owner": stage["owner"],
                    "command": stage["command"],
                    "disposition": stage["disposition"],
                    "bestmove": stage["bestmove"],
                    "dispatched_ms": stage["dispatched_ms"],
                    "completed_ms": stage["completed_ms"],
                }
                for stage in shadow_stages
            ],
            "region_containment": containment,
            "streams": {
                record["instance"]: {
                    "engine": record["engine"],
                    "role": record["role"],
                    "events": record["event_count"],
                    "sha256": record["sha256"],
                }
                for record in concurrent_manifest["streams"]
            },
        },
        "terminal_run": {
            "run_id": terminal_manifest["run_id"],
            "bestmove": terminal_move,
            "root_count": terminal_manifest["legal_root_oracle"]["root_count"],
            "disposition": terminal_manifest["disposition"]["run"],
        },
        "fixed_node_run_id": load_manifest(fixed_run)["run_id"],
        "telemetry_v1_validated_streams": sorted(validated),
        "replay_bundle_integrity": "verified",
        "no_orphan_processes": True,
    }
    report["not_claimed"] = [
        "timed-search equivalence to standalone Stockfish (no CPU/GPU resource isolation was tested)",
        "equal-envelope strength (shadow mode deliberately overspends compute)",
        "any Elo or routing-strength result",
        "LC0 strength qualification (the validation profile uses a backend-light random configuration)",
    ]

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        "shadow execution contract passed: "
        f"anchor={concurrent_move}, fixed-node firewall={direct_move}=={shadow_fixed_move}, "
        f"3 concurrent shadow regions contained, 4 telemetry v1 streams, no orphans"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, UciError, OSError, ValueError) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"shadow execution contract failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "failure.txt").write_text(message + "\n", encoding="utf-8")
        raise SystemExit(1)
