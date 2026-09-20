#!/usr/bin/env python3
"""Qualify RootShardLedger v1 against live legal roots and restricted backends."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.runtime import BackendManager, RuntimeError, load_runtime_config
from controller.shards import RootShardLedger, ShardLedgerError
from tests.harness.normalize import normalize_search
from tests.harness.uci_session import UciError, UciSession


CONFIG_PATH = ROOT / "config" / "allfather.validation.json"
CORPUS_PATH = ROOT / "tests" / "baseline" / "corpus.json"
LEGAL_PATH = ROOT / "tests" / "baseline" / "golden" / "legal_moves.json"
RESULT_DIR = ROOT / "build" / "test-results" / "shard-ledger"
OWNERS = ("stockfish", "reckless", "lc0")
PV_HEAD_RE = re.compile(r"(?:^|\s)pv\s+([a-h][1-8][a-h][1-8][qrbn]?)(?:\s|$)")


class ContractError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def position_command(position: dict[str, Any]) -> str:
    if "fen" in position:
        return f"position fen {position['fen']}"
    moves = list(position.get("startpos_moves", []))
    command = "position startpos"
    if moves:
        command += " moves " + " ".join(moves)
    return command


def inspect_pv_heads(lines: list[str]) -> list[str]:
    heads: list[str] = []
    for line in lines:
        if not line.startswith("info "):
            continue
        match = PV_HEAD_RE.search(line)
        if match is not None:
            heads.append(match.group(1))
    return heads


def assert_restricted(
    *,
    label: str,
    lines: list[str],
    allowed: tuple[str, ...],
) -> dict[str, Any]:
    allowed_set = set(allowed)
    normalized = normalize_search(lines)
    bestmove = normalized["bestmove"]
    if bestmove not in allowed_set:
        raise ContractError(
            f"{label}: bestmove {bestmove!r} outside ledger-owned roots {sorted(allowed_set)}"
        )
    pv_heads = inspect_pv_heads(lines)
    unauthorized = sorted({move for move in pv_heads if move not in allowed_set})
    if unauthorized:
        raise ContractError(
            f"{label}: PV roots escaped ledger-owned region: {unauthorized}"
        )
    return {
        "bestmove": bestmove,
        "pv_heads": pv_heads,
        "allowed_roots": list(allowed),
    }


def round_robin_partition(roots: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    buckets: dict[str, list[str]] = {owner: [] for owner in OWNERS}
    for index, move in enumerate(roots):
        buckets[OWNERS[index % len(OWNERS)]].append(move)
    return {owner: tuple(buckets[owner]) for owner in OWNERS}


def main() -> int:
    config = load_runtime_config(CONFIG_PATH)
    corpus_doc = load_json(CORPUS_PATH)
    legal_doc = load_json(LEGAL_PATH)
    corpus = {case["id"]: case["position"] for case in corpus_doc["cases"]}
    golden = legal_doc["cases"]

    oracle_cases = (
        "startpos",
        "kiwipete_castling",
        "en_passant_available",
        "promotion_available",
        "history_ruy_lopez",
        "checkmate_terminal",
    )

    report: dict[str, Any] = {
        "schema_version": 1,
        "oracle_cases": {},
        "chess960": None,
        "partition": None,
        "backend_restrictions": {},
        "final_ledger": None,
    }

    manager = BackendManager(config)
    live_startpos: tuple[str, ...] | None = None
    try:
        manager.start()
        manager.set_chess960(False)

        for case_id in oracle_cases:
            manager.new_game()
            manager.set_position(position_command(corpus[case_id]))
            roots = manager.legal_root_moves()
            expected = tuple(golden[case_id])
            if len(roots) != len(set(roots)):
                raise ContractError(f"{case_id}: live oracle returned duplicate roots")
            if set(roots) != set(expected) or len(roots) != len(expected):
                raise ContractError(
                    f"{case_id}: live Stockfish roots differ from frozen legal oracle; "
                    f"live={sorted(roots)}, frozen={sorted(expected)}"
                )
            report["oracle_cases"][case_id] = {
                "root_count": len(roots),
                "live_order": list(roots),
                "matches_frozen_set": True,
            }
            if case_id == "startpos":
                live_startpos = roots

        # Qualify that the legal-root oracle follows synchronized Chess960 move
        # encoding rather than assuming standard castling notation.
        manager.set_chess960(True)
        manager.new_game()
        manager.set_position(position_command(corpus["kiwipete_castling"]))
        chess960_roots = manager.legal_root_moves()
        expected_960 = set(golden["kiwipete_castling"])
        expected_960.remove("e1g1")
        expected_960.remove("e1c1")
        expected_960.update({"e1h1", "e1a1"})
        if set(chess960_roots) != expected_960 or len(chess960_roots) != len(expected_960):
            raise ContractError(
                "Chess960 root encoding mismatch: "
                f"live={sorted(chess960_roots)}, expected={sorted(expected_960)}"
            )
        report["chess960"] = {
            "root_count": len(chess960_roots),
            "contains_kingside_rook_square": "e1h1" in chess960_roots,
            "contains_queenside_rook_square": "e1a1" in chess960_roots,
        }
        manager.set_chess960(False)
    finally:
        manager.close()

    if live_startpos is None:
        raise ContractError("startpos legal-root universe was not captured")

    ledger = RootShardLedger(live_startpos, owners=OWNERS, generation=1)
    partition = round_robin_partition(live_startpos)
    ledger.assign_partition(partition)
    report["partition"] = {owner: list(partition[owner]) for owner in OWNERS}

    # Activate every non-empty owner region. Startpos has 20 roots so all three
    # owners are non-empty in this qualification partition.
    for owner in OWNERS:
        ledger.activate_owner(owner)

    # This is deliberately sequential qualification, not PR #11 shadow search.
    for owner in OWNERS:
        spec = config.backends[owner]
        allowed = ledger.active_roots(owner)
        with UciSession(
            spec.binary,
            cwd=spec.cwd,
            timeout=15.0,
            args=list(spec.args),
        ) as session:
            session.configure(spec.options)
            session.new_game()
            session.set_position(corpus["startpos"])
            lines = session.search_nodes(
                256,
                searchmoves=list(allowed),
                timeout=25.0,
            )
        report["backend_restrictions"][owner] = assert_restricted(
            label=owner,
            lines=lines,
            allowed=allowed,
        )
        ledger.seal_owner(owner)

    final_snapshot = ledger.snapshot()
    if any(item["state"] != "sealed" for item in final_snapshot["shards"]):
        raise ContractError("qualification completed with unsealed shards")
    report["final_ledger"] = final_snapshot

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(
        "root ShardLedger contract passed: "
        f"{len(live_startpos)} startpos roots, exact three-owner coverage, "
        "sequential restricted qualification for Stockfish/Reckless/LC0"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, RuntimeError, ShardLedgerError, UciError, ValueError) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"root ShardLedger contract failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "failure.txt").write_text(message + "\n", encoding="utf-8")
        raise SystemExit(1)
