#!/usr/bin/env python3
"""RootShardLedger invariant tests."""

from __future__ import annotations

import copy
import json
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.shards import RootShardLedger, ShardLedgerError


OWNERS = ("stockfish", "reckless", "lc0")
ROOTS = ("e2e4", "d2d4", "g1f3", "c2c4", "b1c3", "g2g3")


def valid_partition() -> dict[str, tuple[str, ...]]:
    return {
        "stockfish": ("e2e4", "c2c4"),
        "reckless": ("d2d4", "b1c3"),
        "lc0": ("g1f3", "g2g3"),
    }


class RootShardLedgerTests(unittest.TestCase):
    def test_deterministic_ids_and_input_order(self):
        ledger = RootShardLedger(ROOTS, generation=7)
        snap = ledger.snapshot()
        self.assertEqual(tuple(snap["candidate_roots"]), ROOTS)
        self.assertEqual(
            [item["id"] for item in snap["shards"]],
            [
                "g000007:r000:e2e4",
                "g000007:r001:d2d4",
                "g000007:r002:g1f3",
                "g000007:r003:c2c4",
                "g000007:r004:b1c3",
                "g000007:r005:g2g3",
            ],
        )
        self.assertEqual(snap["revision"], 0)

    def test_candidate_roots_must_be_canonical_and_unique(self):
        with self.assertRaises(ShardLedgerError):
            RootShardLedger(("E2E4",))
        with self.assertRaises(ShardLedgerError):
            RootShardLedger(("e2e9",))
        with self.assertRaises(ShardLedgerError):
            RootShardLedger(("e2e4", "e2e4"))

    def test_exact_partition_leases_every_root_atomically(self):
        ledger = RootShardLedger(ROOTS)
        ledger.assign_partition(valid_partition())
        snap = ledger.snapshot()
        self.assertEqual(snap["revision"], 1)
        self.assertTrue(snap["partition_assigned"])
        self.assertEqual(
            {item["prefix"][0] for item in snap["shards"]},
            set(ROOTS),
        )
        self.assertTrue(all(item["state"] == "leased" for item in snap["shards"]))
        self.assertEqual(
            {item["owner"] for item in snap["shards"]},
            set(OWNERS),
        )

    def test_partition_rejects_owner_key_mismatch(self):
        ledger = RootShardLedger(ROOTS)
        bad = valid_partition()
        bad.pop("lc0")
        with self.assertRaises(ShardLedgerError):
            ledger.assign_partition(bad)
        self.assertEqual(ledger.revision, 0)

    def test_partition_rejects_overlap_omission_unknown_and_duplicate(self):
        cases = []

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
            ledger = RootShardLedger(ROOTS)
            before = copy.deepcopy(ledger.snapshot())
            with self.assertRaises(ShardLedgerError):
                ledger.assign_partition(partition)
            self.assertEqual(ledger.snapshot(), before)

    def test_partition_is_one_shot(self):
        ledger = RootShardLedger(ROOTS)
        ledger.assign_partition(valid_partition())
        before = copy.deepcopy(ledger.snapshot())
        with self.assertRaises(ShardLedgerError):
            ledger.assign_partition(valid_partition())
        self.assertEqual(ledger.snapshot(), before)

    def test_owner_activation_dispatch_and_seal_state_machine(self):
        ledger = RootShardLedger(ROOTS)
        ledger.assign_partition(valid_partition())

        with self.assertRaises(ShardLedgerError):
            ledger.active_roots("stockfish")

        ledger.activate_owner("stockfish")
        self.assertEqual(ledger.active_roots("stockfish"), ("e2e4", "c2c4"))
        self.assertEqual(ledger.revision, 2)

        with self.assertRaises(ShardLedgerError):
            ledger.activate_owner("stockfish")

        ledger.seal_owner("stockfish")
        self.assertEqual(ledger.revision, 3)
        with self.assertRaises(ShardLedgerError):
            ledger.active_roots("stockfish")
        with self.assertRaises(ShardLedgerError):
            ledger.seal_owner("stockfish")

    def test_unknown_or_empty_owner_dispatch_is_forbidden(self):
        ledger = RootShardLedger(("e2e4", "d2d4"))
        ledger.assign_partition(
            {
                "stockfish": ("e2e4",),
                "reckless": ("d2d4",),
                "lc0": (),
            }
        )
        with self.assertRaises(ShardLedgerError):
            ledger.activate_owner("ghost")
        with self.assertRaises(ShardLedgerError):
            ledger.activate_owner("lc0")
        with self.assertRaises(ShardLedgerError):
            ledger.active_roots("lc0")

    def test_empty_terminal_ledger_can_be_partitioned_but_not_dispatched(self):
        ledger = RootShardLedger(())
        ledger.assign_partition({owner: () for owner in OWNERS})
        snap = ledger.snapshot()
        self.assertTrue(snap["partition_assigned"])
        self.assertEqual(snap["revision"], 1)
        self.assertEqual(snap["shards"], [])
        for owner in OWNERS:
            with self.assertRaises(ShardLedgerError):
                ledger.active_roots(owner)

    def test_snapshot_is_json_serializable_and_detached(self):
        ledger = RootShardLedger(ROOTS)
        ledger.assign_partition(valid_partition())
        snap = ledger.snapshot()
        json.dumps(snap)
        snap["shards"][0]["owner"] = "mutated"
        self.assertNotEqual(ledger.snapshot()["shards"][0]["owner"], "mutated")

    def test_conflicting_concurrent_partitions_cannot_create_overlap(self):
        ledger = RootShardLedger(ROOTS)
        partition_a = valid_partition()
        partition_b = {
            "stockfish": ("d2d4", "g1f3"),
            "reckless": ("e2e4", "g2g3"),
            "lc0": ("c2c4", "b1c3"),
        }

        barrier = threading.Barrier(3)
        successes: list[str] = []
        failures: list[str] = []

        def worker(label: str, partition: dict[str, tuple[str, ...]]) -> None:
            barrier.wait()
            try:
                ledger.assign_partition(partition)
                successes.append(label)
            except ShardLedgerError:
                failures.append(label)

        threads = [
            threading.Thread(target=worker, args=("a", partition_a)),
            threading.Thread(target=worker, args=("b", partition_b)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2.0)

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)

        snap = ledger.snapshot()
        assigned = [item["prefix"][0] for item in snap["shards"]]
        self.assertEqual(set(assigned), set(ROOTS))
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(snap["revision"], 1)


if __name__ == "__main__":
    unittest.main()
