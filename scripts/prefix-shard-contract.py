#!/usr/bin/env python3
"""Qualify PrefixShardLedger v2 and descendant UCI dispatch on real engines.

This is capability/orchestration evidence only.  The three engines are run
sequentially against one certified descendant prefix; no strength or
equal-resource claim is made.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.prefix_dispatch import compile_prefix_dispatch
from common.search_request import parse_position_command
from controller.prefix_shards import PrefixShardLedger, PrefixShardLedgerError
from controller.runtime import BackendManager, RuntimeError, load_runtime_config
from tests.harness.normalize import normalize_search
from tests.harness.uci_session import UciError, UciSession


CONFIG_PATH = ROOT / "config" / "allfather.validation.json"
RESULT_DIR = ROOT / "build" / "test-results" / "prefix-shards"
OWNERS = ("stockfish", "reckless", "lc0")
PV_HEAD_RE = re.compile(r"(?:^|\s)pv\s+([a-h][1-8][a-h][1-8][qrbn]?)(?:\s|$)")


class ContractError(RuntimeError):
    pass


def round_robin_partition(
    roots: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    buckets: dict[str, list[str]] = {owner: [] for owner in OWNERS}
    for index, move in enumerate(roots):
        buckets[OWNERS[index % len(OWNERS)]].append(move)
    return {owner: tuple(buckets[owner]) for owner in OWNERS}


def owner_for(
    partition: dict[str, tuple[str, ...]],
    move: str,
) -> str:
    matches = [owner for owner, moves in partition.items() if move in moves]
    if len(matches) != 1:
        raise ContractError(f"expected exactly one owner for {move}, got {matches}")
    return matches[0]


def other_owner(owner: str) -> str:
    return next(value for value in OWNERS if value != owner)


def pv_heads(lines: list[str]) -> list[str]:
    values: list[str] = []
    for line in lines:
        if not line.startswith("info "):
            continue
        match = PV_HEAD_RE.search(line)
        if match is not None:
            values.append(match.group(1))
    return values


def qualify_backend(
    *,
    spec: Any,
    position: dict[str, object],
    searchmove: str,
) -> dict[str, Any]:
    with UciSession(
        spec.binary,
        cwd=spec.cwd,
        timeout=20.0,
        args=list(spec.args),
    ) as session:
        session.configure(spec.options)
        session.new_game()
        session.set_position(position)
        lines = session.search_nodes(
            256,
            searchmoves=[searchmove],
            timeout=30.0,
        )

    normalized = normalize_search(lines)
    bestmove = normalized["bestmove"]
    if bestmove != searchmove:
        raise ContractError(
            f"{spec.name}: bestmove {bestmove!r} escaped single descendant "
            f"searchmove {searchmove!r}"
        )
    heads = pv_heads(lines)
    escaped = sorted({move for move in heads if move != searchmove})
    if escaped:
        raise ContractError(
            f"{spec.name}: PV heads escaped descendant prefix: {escaped}"
        )
    return {
        "instance": spec.name,
        "family": spec.family,
        "bestmove": bestmove,
        "pv_heads": heads,
        "searchmove": searchmove,
    }


def main() -> int:
    config = load_runtime_config(CONFIG_PATH)
    manager = BackendManager(config)
    try:
        manager.start()
        manager.set_chess960(False)
        manager.new_game()
        manager.set_position("position startpos")
        roots = manager.legal_root_moves()
        if "e2e4" not in roots:
            raise ContractError("startpos oracle did not contain e2e4")

        partition = round_robin_partition(roots)
        ledger = PrefixShardLedger(roots, owners=OWNERS, generation=1)
        ledger.assign_root_partition(partition)

        first_owner = owner_for(partition, "e2e4")
        e4_id = next(
            str(item["id"])
            for item in ledger.frontier()
            if item["prefix"] == ["e2e4"]
        )
        ledger.activate_shard(e4_id, owner=first_owner)
        ledger.seal_shard(e4_id, owner=first_owner)

        manager.set_position("position startpos moves e2e4")
        first_children = manager.legal_root_moves()
        if not first_children or "e7e5" not in first_children:
            raise ContractError(
                "depth-2 oracle returned no e7e5 child after e2e4"
            )
        first_child_ids = ledger.split_shard(
            e4_id,
            first_children,
            owner=first_owner,
        )
        e4e5_id = next(
            shard_id
            for shard_id in first_child_ids
            if ledger.get(shard_id)["prefix"] == ["e2e4", "e7e5"]
        )

        second_owner = other_owner(first_owner)
        ledger.transfer_shards(
            (e4e5_id,),
            from_owner=first_owner,
            to_owner=second_owner,
        )
        ledger.activate_shard(e4e5_id, owner=second_owner)
        ledger.seal_shard(e4e5_id, owner=second_owner)

        manager.set_position("position startpos moves e2e4 e7e5")
        second_children = manager.legal_root_moves()
        if not second_children or "g1f3" not in second_children:
            raise ContractError(
                "depth-3 oracle returned no g1f3 child after e2e4 e7e5"
            )
        second_child_ids = ledger.split_shard(
            e4e5_id,
            second_children,
            owner=second_owner,
        )
        target_id = next(
            shard_id
            for shard_id in second_child_ids
            if ledger.get(shard_id)["prefix"]
            == ["e2e4", "e7e5", "g1f3"]
        )
        ledger.validate_invariants()
    finally:
        manager.close()

    base = parse_position_command("position startpos")
    dispatch = compile_prefix_dispatch(
        base,
        ("e2e4", "e7e5", "g1f3"),
        limit={"nodes": 256},
    )
    if dispatch.position_command != "position startpos moves e2e4 e7e5":
        raise ContractError(
            f"unexpected descendant position command: {dispatch.position_command}"
        )
    if dispatch.go_command != "go nodes 256 searchmoves g1f3":
        raise ContractError(
            f"unexpected descendant go command: {dispatch.go_command}"
        )

    position_payload: dict[str, object] = {
        "startpos_moves": list(dispatch.position.moves)
    }
    backend_results: dict[str, Any] = {}
    for owner in OWNERS:
        spec = config.backends[owner]
        backend_results[owner] = qualify_backend(
            spec=spec,
            position=position_payload,
            searchmove=dispatch.searchmoves[0],
        )

    final_snapshot = ledger.snapshot()
    if target_id not in final_snapshot["frontier_ids"]:
        raise ContractError("qualified depth-3 target is not on the final frontier")

    report = {
        "schema_version": 1,
        "root_count": len(roots),
        "root_partition": {
            owner: list(partition[owner]) for owner in OWNERS
        },
        "first_split": {
            "parent_id": e4_id,
            "parent_owner": first_owner,
            "child_count": len(first_children),
            "children": list(first_children),
        },
        "transfer": {
            "shard_id": e4e5_id,
            "from_owner": first_owner,
            "to_owner": second_owner,
        },
        "second_split": {
            "parent_id": e4e5_id,
            "parent_owner": second_owner,
            "child_count": len(second_children),
            "children": list(second_children),
        },
        "qualified_prefix": ["e2e4", "e7e5", "g1f3"],
        "qualified_shard_id": target_id,
        "compiled_position": dispatch.position_command,
        "compiled_go": dispatch.go_command,
        "backend_restrictions": backend_results,
        "final_ledger": final_snapshot,
        "claim": (
            "Capability qualification only: exact oracle children were represented "
            "as a prefix-free recursive frontier and all three backends enforced "
            "one depth-3 descendant restriction sequentially. No strength, live "
            "routing, replay-v1, or equal-resource claim."
        ),
    }

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "PrefixShardLedger v2 contract passed: "
        f"roots={len(roots)}, first_children={len(first_children)}, "
        f"second_children={len(second_children)}, prefix=e2e4.e7e5.g1f3"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        ContractError,
        PrefixShardLedgerError,
        RuntimeError,
        UciError,
        ValueError,
        OSError,
    ) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"prefix shard contract failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "failure.txt").write_text(message + "\n", encoding="utf-8")
        raise SystemExit(1)
