# STRENGTH-S3-A — Contract-only specialist preparation

**27 October 2026: SOURCE-ONLY, OBSERVE-ONLY.** S3-A does not launch LC0,
Reckless or a Stockfish comparator, grant outward move authority, or qualify
host resources, playing strength, live APIs or release.

## Architectural contract

This is the first incremental part of S3 under the 25 October 2026 Strength
Rebuild Technical Specification, §§3–8 and 11.1. Maintain one unrestricted
Stockfish native-clock incumbent; specialists NOMINATE, never vote; one
auxiliary compute slot; reserve Stockfish two-root comparator capacity
BEFORE a proposition; stale incumbent epochs, missing receipts or costs
fail closed. Source-only fixtures never impersonate real host measurements.

The source implementation adds:

- controller/strength/candidates.py: immutable CandidateProposalV1 and
  CandidatePairV1, bound to TurnKey, both SearchIdentities, full history,
  incumbent root/epoch, external root restrictions, native work semantics,
  proposed root, network identity, grant and settlement hash references.
  Each proposed pair has exactly two distinct roots, one incumbent.
- tools/strength_lab/s3a_rules.py: independent source-pinned chess==1.11.2
  history replay and full legal-root universe verification. A producer's
  claimed legal move list does not certify chess legality.
- controller/strength/scheduler.py: StrengthTaskV1, CostObservationV1 and
  StrengthAuxScheduler, using the real BudgetLedger reserve/settle APIs
  without editing the existing frozen J3 catalog. The VERIFY comparator
  capacity is reserved before EXPLORE proposal capacity. Slots are
  sequential; failed/unknown work is charged conservatively and disables
  retries, stale epochs abort. Memory and timed feasibility are checked.
- qualification/strength/s3a-contracts-v1.json: denied source-only policy,
  default action NONE, missing measured cost profile and no dispatch authority.
- config/allfather.strength-s3a-observe.experimental.json: one Stockfish
  MultiPV1 anchor, exactly S2 native clocks and publication evidence, explicit
  ALLFATHER_S3A_OFFLINE=1 opt-in. S3A neither permits second engine instances
  nor the apply/control modes; schema-v1/v2 legacy remains unchanged.

The scheduler does not authenticate a measured WorkGrant or launch a
process in this PR. Unit tests use SOURCE_TEST_FIXTURE cost observations
to exercise ledgers, but ordinary use rejects them. A fabricated reviewed
digest is not a real certificate; production dispatch remains physically
unwired. It is an error to claim this is a completed S3 comparison.

## Regression and adversarial coverage

Run from a clean checked-out source:

    python3 -m pip install --require-hashes -r qualification/local-game-requirements.txt
    python3 -m unittest discover -s tests/strength_s3a -p 'test_*.py' -v
    python3 -m unittest discover -s tests/strength_s2 -p 'test_*.py' -v
    python3 scripts/check-current-status.py
    python3 scripts/check-docs-consistency.py

Tests cover malformed/fabricated candidates, full-history legality, legal
restriction and source-network mismatches, nonce/ABA stale incumbent,
comparator-first ledger reservations, concurrent slot collisions, insufficient
wall/CPU/RSS, early stop, missing resource measurement, overspend, one-use
policies, v3 opt-in isolation, and a real fake-UCI anchor-only process with
one published Stockfish move and independently reconstructed S2 flight.

## S3-B remains a separate implementation

Next integrate a source-qualified measured host cost catalog and authentic
versioned WorkGrants, then allow one real LC0/Reckless proposer using the full
permitted legal root universe. Settle its actual costs, then search exactly
the current incumbent and nominated move with separate pinned Stockfish
MultiPV2, same-depth complete unbounded paired score frames, current search
nonces/epoch and bounded process memory/CPU. Retain the independent evidence.
S3-B remains OBSERVE_ONLY; S4 is the distinct quality/publication authority.
