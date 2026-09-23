#!/usr/bin/env python3
"""M14-D recursive REFINE nomination policy regressions."""

from __future__ import annotations

import unittest

from controller.refinement_policy import (
    RECURSIVE_NOMINATION_POLICY,
    RefinementPolicyError,
    RefinementStageEvidence,
    evaluate_recursive_nominations,
)


OWNERS = ("stockfish", "reckless", "lc0")


def evidence(
    owner: str,
    bestmove: str | None,
    *,
    disposition: str = "completed",
    lossy: bool = False,
    truncated: bool = False,
) -> RefinementStageEvidence:
    moves = {
        "stockfish": ("e7e5", "c7c5"),
        "reckless": ("e7e6", "c7c6"),
        "lc0": ("g8f6", "b8c6"),
    }[owner]
    return RefinementStageEvidence(
        expansion_id="target-000-e2e4",
        parent_prefix=("e2e4",),
        owner=owner,
        search_id=f"run:refine:{owner}",
        child_moves=moves,
        bestmove=bestmove,
        disposition=disposition,
        evidence_lossy=lossy,
        evidence_truncated=truncated,
    )


class RecursiveRefinementPolicyTests(unittest.TestCase):
    def test_clean_terminal_bestmoves_nominate_owned_children(self):
        rows = (
            evidence("lc0", "g8f6"),
            evidence("stockfish", "c7c5"),
            evidence("reckless", "e7e6"),
        )
        nominations = evaluate_recursive_nominations(rows, owner_order=OWNERS)
        self.assertEqual(
            [item.owner for item in nominations],
            ["stockfish", "reckless", "lc0"],
        )
        self.assertEqual(
            [item.prefix for item in nominations],
            [
                ("e2e4", "c7c5"),
                ("e2e4", "e7e6"),
                ("e2e4", "g8f6"),
            ],
        )

    def test_failed_lossy_truncated_or_outside_terminal_does_not_nominate(self):
        rows = (
            evidence("stockfish", "e7e5", disposition="failed"),
            evidence("reckless", "e7e6", lossy=True),
            evidence("lc0", "g8f6", truncated=True),
            evidence("stockfish", "a7a6"),
        )
        self.assertEqual(
            evaluate_recursive_nominations(rows, owner_order=OWNERS),
            (),
        )

    def test_input_completion_order_does_not_change_nomination_order(self):
        rows = (
            evidence("stockfish", "e7e5"),
            evidence("reckless", "c7c6"),
            evidence("lc0", "b8c6"),
        )
        forward = evaluate_recursive_nominations(rows, owner_order=OWNERS)
        reverse = evaluate_recursive_nominations(
            tuple(reversed(rows)), owner_order=OWNERS
        )
        self.assertEqual(forward, reverse)

    def test_unknown_policy_fails_closed(self):
        with self.assertRaises(RefinementPolicyError):
            evaluate_recursive_nominations(
                (evidence("stockfish", "e7e5"),),
                owner_order=OWNERS,
                policy="vote-the-scores",
            )

    def test_policy_identifier_is_frozen(self):
        self.assertEqual(
            RECURSIVE_NOMINATION_POLICY,
            "stage_terminal_bestmove_v1",
        )


if __name__ == "__main__":
    unittest.main()
