#!/usr/bin/env python3
"""Recursive PrefixShardLedger v2 and descendant-dispatch regressions."""

from __future__ import annotations

import copy
import json
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common.prefix_dispatch import compile_descendant_region, compile_prefix_dispatch, descendant_position
from common.search_request import SearchRequestError, parse_position_command
from controller.prefix_shards import (
    PrefixShardLedger,
    PrefixShardLedgerError,
)


OWNERS = ("stockfish", "reckless", "lc0")
ROOTS = ("e2e4", "d2d4", "g1f3", "c2c4", "b1c3", "g2g3")


def valid_partition() -> dict[str, tuple[str, ...]]:
    return {
        "stockfish": ("e2e4", "c2c4"),
        "reckless": ("d2d4", "b1c3"),
        "lc0": ("g1f3", "g2g3"),
    }


def sealed_root(ledger: PrefixShardLedger, move: str, owner: str) -> str:
    shard_id = next(
        item["id"] for item in ledger.frontier() if item["prefix"] == [move]
    )
    ledger.activate_shard(shard_id, owner=owner)
    ledger.seal_shard(shard_id, owner=owner)
    return str(shard_id)


class PrefixShardLedgerTests(unittest.TestCase):
    def test_deterministic_root_ids_and_input_order(self):
        ledger = PrefixShardLedger(ROOTS, generation=7)
        snap = ledger.snapshot()
        self.assertEqual(tuple(snap["candidate_roots"]), ROOTS)
        self.assertEqual(
            [item["id"] for item in snap["shards"]],
            [
                "g000007:d001:e2e4",
                "g000007:d001:d2d4",
                "g000007:d001:g1f3",
                "g000007:d001:c2c4",
                "g000007:d001:b1c3",
                "g000007:d001:g2g3",
            ],
        )
        self.assertEqual(snap["schema_version"], 2)
        self.assertEqual(snap["revision"], 0)

    def test_candidate_roots_must_be_canonical_unique_and_generation_valid(self):
        with self.assertRaises(PrefixShardLedgerError):
            PrefixShardLedger(("E2E4",))
        with self.assertRaises(PrefixShardLedgerError):
            PrefixShardLedger(("e2e9",))
        with self.assertRaises(PrefixShardLedgerError):
            PrefixShardLedger(("e2e4", "e2e4"))
        for bad in (0, -1, True):
            with self.assertRaises(PrefixShardLedgerError):
                PrefixShardLedger(("e2e4",), generation=bad)

    def test_exact_root_partition_is_atomic_and_one_shot(self):
        ledger = PrefixShardLedger(ROOTS)
        ledger.assign_root_partition(valid_partition())
        snap = ledger.snapshot()
        self.assertEqual(snap["revision"], 1)
        self.assertTrue(snap["partition_assigned"])
        self.assertTrue(all(item["state"] == "leased" for item in snap["shards"]))
        before = copy.deepcopy(snap)
        with self.assertRaises(PrefixShardLedgerError):
            ledger.assign_root_partition(valid_partition())
        self.assertEqual(ledger.snapshot(), before)

    def test_bad_root_partitions_roll_back_exactly(self):
        cases: list[dict[str, tuple[str, ...]]] = []

        missing_owner = valid_partition()
        missing_owner.pop("lc0")
        cases.append(missing_owner)

        overlap = valid_partition()
        overlap["lc0"] = ("g1f3", "e2e4")
        cases.append(overlap)

        omitted = valid_partition()
        omitted["lc0"] = ("g1f3",)
        cases.append(omitted)

        unknown = valid_partition()
        unknown["lc0"] = ("g1f3", "a2a5")
        cases.append(unknown)

        duplicate = valid_partition()
        duplicate["stockfish"] = ("e2e4", "e2e4")
        cases.append(duplicate)

        for partition in cases:
            ledger = PrefixShardLedger(ROOTS)
            before = copy.deepcopy(ledger.snapshot())
            with self.assertRaises(PrefixShardLedgerError):
                ledger.assign_root_partition(partition)
            self.assertEqual(ledger.snapshot(), before)

    def test_per_shard_activation_and_seal_require_owner_and_state(self):
        ledger = PrefixShardLedger(ROOTS)
        ledger.assign_root_partition(valid_partition())
        shard_id = next(
            item["id"] for item in ledger.frontier() if item["prefix"] == ["e2e4"]
        )

        with self.assertRaises(PrefixShardLedgerError):
            ledger.seal_shard(str(shard_id), owner="stockfish")
        with self.assertRaises(PrefixShardLedgerError):
            ledger.activate_shard(str(shard_id), owner="reckless")

        ledger.activate_shard(str(shard_id), owner="stockfish")
        self.assertEqual(ledger.get(str(shard_id))["state"], "active")
        with self.assertRaises(PrefixShardLedgerError):
            ledger.activate_shard(str(shard_id), owner="stockfish")
        ledger.seal_shard(str(shard_id), owner="stockfish")
        self.assertEqual(ledger.get(str(shard_id))["state"], "sealed")

    def test_split_retires_parent_and_creates_owner_inherited_children(self):
        ledger = PrefixShardLedger(ROOTS, generation=3)
        ledger.assign_root_partition(valid_partition())
        parent_id = sealed_root(ledger, "e2e4", "stockfish")
        before_revision = ledger.revision

        child_ids = ledger.split_shard(
            parent_id,
            ("e7e5", "c7c5", "e7e6"),
            owner="stockfish",
        )
        self.assertEqual(
            child_ids,
            (
                "g000003:d002:e2e4.e7e5",
                "g000003:d002:e2e4.c7c5",
                "g000003:d002:e2e4.e7e6",
            ),
        )
        self.assertEqual(ledger.revision, before_revision + 1)

        parent = ledger.get(parent_id)
        self.assertEqual(parent["state"], "retired")
        self.assertEqual(tuple(parent["child_ids"]), child_ids)
        children = ledger.children(parent_id)
        self.assertEqual([item["prefix"] for item in children], [
            ["e2e4", "e7e5"],
            ["e2e4", "c7c5"],
            ["e2e4", "e7e6"],
        ])
        self.assertTrue(all(item["owner"] == "stockfish" for item in children))
        self.assertTrue(all(item["state"] == "leased" for item in children))
        self.assertNotIn(parent_id, ledger.snapshot()["frontier_ids"])
        ledger.validate_invariants()

    def test_split_is_rejected_before_seal_and_cannot_repeat(self):
        ledger = PrefixShardLedger(ROOTS)
        ledger.assign_root_partition(valid_partition())
        parent_id = next(
            item["id"] for item in ledger.frontier() if item["prefix"] == ["e2e4"]
        )

        with self.assertRaises(PrefixShardLedgerError):
            ledger.split_shard(
                str(parent_id), ("e7e5",), owner="stockfish"
            )

        ledger.activate_shard(str(parent_id), owner="stockfish")
        with self.assertRaises(PrefixShardLedgerError):
            ledger.split_shard(
                str(parent_id), ("e7e5",), owner="stockfish"
            )

        ledger.seal_shard(str(parent_id), owner="stockfish")
        ledger.split_shard(str(parent_id), ("e7e5",), owner="stockfish")
        with self.assertRaises(PrefixShardLedgerError):
            ledger.split_shard(str(parent_id), ("c7c5",), owner="stockfish")

    def test_failed_split_is_byte_for_byte_atomic(self):
        for children in (
            (),
            ("e7e5", "e7e5"),
            ("E7E5",),
            ("e7e9",),
        ):
            ledger = PrefixShardLedger(ROOTS)
            ledger.assign_root_partition(valid_partition())
            parent_id = sealed_root(ledger, "e2e4", "stockfish")
            before = copy.deepcopy(ledger.snapshot())
            with self.assertRaises(PrefixShardLedgerError):
                ledger.split_shard(
                    parent_id, children, owner="stockfish"
                )
            self.assertEqual(ledger.snapshot(), before)

    def test_recursive_split_preserves_prefix_free_frontier(self):
        ledger = PrefixShardLedger(ROOTS, generation=9)
        ledger.assign_root_partition(valid_partition())
        root_id = sealed_root(ledger, "e2e4", "stockfish")
        first_children = ledger.split_shard(
            root_id, ("e7e5", "c7c5"), owner="stockfish"
        )
        e4e5 = first_children[0]
        ledger.activate_shard(e4e5, owner="stockfish")
        ledger.seal_shard(e4e5, owner="stockfish")
        second_children = ledger.split_shard(
            e4e5, ("g1f3", "f1c4"), owner="stockfish"
        )

        frontier = [tuple(item["prefix"]) for item in ledger.frontier()]
        self.assertIn(("e2e4", "c7c5"), frontier)
        self.assertIn(("e2e4", "e7e5", "g1f3"), frontier)
        self.assertIn(("e2e4", "e7e5", "f1c4"), frontier)
        self.assertNotIn(("e2e4",), frontier)
        self.assertNotIn(("e2e4", "e7e5"), frontier)
        self.assertEqual(
            ledger.parent(second_children[0])["id"],
            e4e5,
        )
        ledger.validate_invariants()

    def test_recursive_lookup_helpers_require_a_sealed_frontier_leaf(self):
        ledger = PrefixShardLedger(ROOTS)
        ledger.assign_root_partition(valid_partition())
        root = ledger.get_by_prefix(("e2e4",))
        self.assertEqual(root["owner"], "stockfish")
        self.assertIsNone(ledger.sealed_frontier_leaf(("e2e4",)))

        ledger.activate_shard(str(root["id"]), owner="stockfish")
        ledger.seal_shard(str(root["id"]), owner="stockfish")
        eligible = ledger.sealed_frontier_leaf(("e2e4",))
        self.assertIsNotNone(eligible)
        self.assertEqual(eligible["state"], "sealed")

        ledger.split_shard(str(root["id"]), ("e7e5",), owner="stockfish")
        self.assertIsNone(ledger.sealed_frontier_leaf(("e2e4",)))
        with self.assertRaises(PrefixShardLedgerError):
            ledger.get_by_prefix(("a1a9",))

    def test_atomic_transfer_moves_only_leased_frontier_shards(self):
        ledger = PrefixShardLedger(ROOTS)
        ledger.assign_root_partition(valid_partition())
        root_id = sealed_root(ledger, "e2e4", "stockfish")
        child_ids = ledger.split_shard(
            root_id, ("e7e5", "c7c5"), owner="stockfish"
        )
        before_revision = ledger.revision
        ledger.transfer_shards(
            child_ids,
            from_owner="stockfish",
            to_owner="reckless",
        )
        self.assertEqual(ledger.revision, before_revision + 1)
        self.assertTrue(
            all(ledger.get(shard_id)["owner"] == "reckless" for shard_id in child_ids)
        )

        ledger.activate_shard(child_ids[0], owner="reckless")
        before = copy.deepcopy(ledger.snapshot())
        with self.assertRaises(PrefixShardLedgerError):
            ledger.transfer_shards(
                child_ids,
                from_owner="reckless",
                to_owner="lc0",
            )
        self.assertEqual(ledger.snapshot(), before)

    def test_transfer_rejects_duplicates_same_owner_and_historical_parent(self):
        ledger = PrefixShardLedger(ROOTS)
        ledger.assign_root_partition(valid_partition())
        root_id = sealed_root(ledger, "e2e4", "stockfish")
        child_ids = ledger.split_shard(
            root_id, ("e7e5", "c7c5"), owner="stockfish"
        )
        for ids, source, target in (
            ((child_ids[0], child_ids[0]), "stockfish", "reckless"),
            ((child_ids[0],), "stockfish", "stockfish"),
            ((root_id,), "stockfish", "reckless"),
        ):
            before = copy.deepcopy(ledger.snapshot())
            with self.assertRaises(PrefixShardLedgerError):
                ledger.transfer_shards(ids, from_owner=source, to_owner=target)
            self.assertEqual(ledger.snapshot(), before)

    def test_snapshot_is_detached_json_and_dfs_ordered(self):
        ledger = PrefixShardLedger(ROOTS)
        ledger.assign_root_partition(valid_partition())
        root_id = sealed_root(ledger, "e2e4", "stockfish")
        ledger.split_shard(root_id, ("e7e5", "c7c5"), owner="stockfish")
        snap = ledger.snapshot()
        json.dumps(snap)
        self.assertEqual(snap["shards"][0]["prefix"], ["e2e4"])
        self.assertEqual(snap["shards"][1]["prefix"], ["e2e4", "e7e5"])
        self.assertEqual(snap["shards"][2]["prefix"], ["e2e4", "c7c5"])
        snap["shards"][0]["owner"] = "mutated"
        self.assertNotEqual(ledger.snapshot()["shards"][0]["owner"], "mutated")

    def test_conflicting_concurrent_splits_commit_exactly_once(self):
        ledger = PrefixShardLedger(ROOTS)
        ledger.assign_root_partition(valid_partition())
        parent_id = sealed_root(ledger, "e2e4", "stockfish")
        barrier = threading.Barrier(3)
        successes: list[str] = []
        failures: list[str] = []

        def worker(label: str, children: tuple[str, ...]) -> None:
            barrier.wait()
            try:
                ledger.split_shard(parent_id, children, owner="stockfish")
                successes.append(label)
            except PrefixShardLedgerError:
                failures.append(label)

        threads = [
            threading.Thread(target=worker, args=("a", ("e7e5", "c7c5"))),
            threading.Thread(target=worker, args=("b", ("e7e6", "c7c6"))),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2.0)

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(ledger.get(parent_id)["state"], "retired")
        ledger.validate_invariants()

    def test_conflicting_concurrent_transfers_never_double_own(self):
        ledger = PrefixShardLedger(ROOTS)
        ledger.assign_root_partition(valid_partition())
        parent_id = sealed_root(ledger, "e2e4", "stockfish")
        child_id = ledger.split_shard(
            parent_id, ("e7e5",), owner="stockfish"
        )[0]
        barrier = threading.Barrier(3)
        successes: list[str] = []
        failures: list[str] = []

        def worker(label: str, target: str) -> None:
            barrier.wait()
            try:
                ledger.transfer_shards(
                    (child_id,),
                    from_owner="stockfish",
                    to_owner=target,
                )
                successes.append(label)
            except PrefixShardLedgerError:
                failures.append(label)

        threads = [
            threading.Thread(target=worker, args=("a", "reckless")),
            threading.Thread(target=worker, args=("b", "lc0")),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2.0)

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIn(ledger.get(child_id)["owner"], ("reckless", "lc0"))


class PrefixDispatchTests(unittest.TestCase):
    def test_descendant_position_advances_by_complete_prefix(self):
        base = parse_position_command("position startpos moves d2d4")
        built = descendant_position(base, ("d7d5", "c2c4"))
        self.assertEqual(built.moves, ("d2d4", "d7d5", "c2c4"))

    def test_depth_one_matches_root_v1_semantics(self):
        base = parse_position_command("position startpos")
        dispatch = compile_prefix_dispatch(base, ("e2e4",), limit={"nodes": 256})
        self.assertEqual(dispatch.position_command, "position startpos")
        self.assertEqual(dispatch.searchmoves, ("e2e4",))
        self.assertEqual(dispatch.go_command, "go nodes 256 searchmoves e2e4")

    def test_depth_three_moves_prefix_parent_into_position(self):
        base = parse_position_command("position startpos")
        dispatch = compile_prefix_dispatch(
            base,
            ("e2e4", "e7e5", "g1f3"),
            limit={"nodes": 256},
        )
        self.assertEqual(
            dispatch.position_command,
            "position startpos moves e2e4 e7e5",
        )
        self.assertEqual(dispatch.searchmoves, ("g1f3",))
        self.assertEqual(dispatch.go_command, "go nodes 256 searchmoves g1f3")

    def test_existing_game_history_is_preserved(self):
        base = parse_position_command(
            "position startpos moves e2e4 e7e5"
        )
        dispatch = compile_prefix_dispatch(
            base,
            ("g1f3", "b8c6"),
            limit={"nodes": 64},
        )
        self.assertEqual(
            dispatch.position_command,
            "position startpos moves e2e4 e7e5 g1f3",
        )
        self.assertEqual(dispatch.go_command, "go nodes 64 searchmoves b8c6")

    def test_fen_and_variant_are_preserved(self):
        base = parse_position_command(
            "position fen r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
            variant="chess960",
        )
        dispatch = compile_prefix_dispatch(
            base,
            ("e1h1", "e8a8"),
            limit={"depth": 4},
        )
        self.assertEqual(dispatch.position.variant, "chess960")
        self.assertEqual(
            dispatch.position.moves,
            ("e1h1",),
        )
        self.assertEqual(dispatch.searchmoves, ("e8a8",))

    def test_sibling_descendant_region_preserves_parent_and_children(self):
        base = parse_position_command("position startpos moves d2d4")
        dispatch = compile_descendant_region(
            base,
            parent_prefix=("d7d5",),
            child_moves=("c2c4", "g1f3"),
            limit={"nodes": 96},
        )
        self.assertEqual(
            dispatch.position_command,
            "position startpos moves d2d4 d7d5",
        )
        self.assertEqual(dispatch.searchmoves, ("c2c4", "g1f3"))
        self.assertEqual(
            dispatch.go_command,
            "go nodes 96 searchmoves c2c4 g1f3",
        )

    def test_sibling_descendant_region_rejects_empty_or_duplicate_children(self):
        base = parse_position_command("position startpos")
        for children in ((), ("e7e5", "e7e5"), ("E7E5",)):
            with self.assertRaises(SearchRequestError):
                compile_descendant_region(
                    base,
                    parent_prefix=("e2e4",),
                    child_moves=children,
                    limit={"nodes": 8},
                )

    def test_invalid_or_empty_prefix_fails_without_mutating_base(self):
        base = parse_position_command("position startpos moves e2e4")
        before = base
        for prefix in ((), ("E7E5",), ("e7e9",)):
            with self.assertRaises(SearchRequestError):
                compile_prefix_dispatch(base, prefix, limit={"nodes": 8})
        self.assertEqual(base, before)


if __name__ == "__main__":
    unittest.main()
