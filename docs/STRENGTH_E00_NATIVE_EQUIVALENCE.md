# STRENGTH-E00: real Stockfish source equivalence / observer overhead

**Status:** experimental source candidate; not an executed 512-history
qualification, playing-strength claim, Elo result, or release.

## Purpose and ancestry

The PR81 G3 pilot finished 0W/4D/12L. PR87 introduces the S2 native-clock
observe-only incumbent. E00 measures whether that actual observer preserves
Stockfish's **fixed-node bestmove** and the operational cost of the wrapper
before S3 permits a challenger to propose a move.

The E00 branch is explicitly stacked on PR87 while PR87 remains draft/open.
Merge/retarget only after the parent PR is independently reviewed.

## Strict distinction between clocks and nodes

S2 continues to admit only native two-sided game clocks and movetime, and
rejects go nodes. E00 has a separate experimental strength policy,
strength_e00_incumbent_observe_v1, and a different node plan,
strength_e00_fixed_nodes_watchdog_v1. It requires an explicit
ALLFATHER_E00_OFFLINE=1 environment opt-in. No new native-clock accept rule,
old serialized evidence enum, G3 move authority, engine binary/network or
old release profile is changed.

E00 forwards exactly go nodes N and optional declared searchmoves to one
Stockfish engine. A separate absolute watchdog guards a hung process. No
S2/E00 event recorder can authorize a move replacement: the only UCI stdout
writer is UciFrontend, and FinalSelection is still anchor-only.

## Corpus: 512 deterministic complete histories

The committed tests/fixtures/strength_e00/corpus-recipe-v1.json locks a
source recipe (seed, 512 count, source Git blob identifiers and pinned
chess==1.11.2 rules library). tools/strength_lab/e00_corpus.py builds
deterministic legal complete histories from 50 META-1 openings, eight
ENGINE-OPT EPD positions and three special-move histories. Every run
retains the exact corpus JSONL and SHA256. Source-only exact-head CI on
27 October 2026 independently reproduced the 512-row SHA256 twice:
`3cd76e1e6acf62a7ceedeec13ccc0c4ed619987d86cdf43bdaf0e427c387f1c5`.
That digest is now frozen in both the corpus recipe and policy.

This is a *source-frozen generated corpus*, not a checked-in 512-row file
and not a completed test. A separately reviewed static corpus artifact
would strengthen this later. FEN-only joins are explicitly forbidden.

## Real comparison and evidence

Both arms run the **same** canonical exact-head PGO Stockfish and NNUE,
Threads=1, Hash=64, MultiPV=1, Move Overhead=100, Ponder=false and
UCI_Chess960=false. Before each sample: ucinewgame, isready, exact full
position history, then go nodes 50000. Arm order alternates by occurrence.

The runner records all command/output streams, preserves stderr apart
from stdout, one legal bestmove per occurrence, binary/profile/network
identity, exact wall timing and strict cgroup-v2 CPU/RSS when delegated.
The comparator never averages engine evaluations or inserts an external
move/score. Missing cgroup measurements are UNAVAILABLE, not zero.

The independent validator verifies the exact source, corpus, executable,
network, session occurrence, command, raw terminal output, chess legality,
complete S2/E00 hash chain and all expected records. 512/512 matching
fixed-node root moves is required for the fixed-node functional result.
A native-clock smoke independently exercises real S2 clocks, but *does not*
require deterministic timed bestmoves. The two smoke phases are not the
complete native-clock or strength qualification.

Predeclared exploratory p95 overhead seeds: wrapper minus direct
wall <= 50 ms, CPU <= 100 ms. These are not known optimal values.
No equal-compute claim follows from a matched node request.

## Execution

Build the frozen real engines and install pinned independent rules first:

    python3 -m pip install --require-hashes -r qualification/local-game-requirements.txt
    bash scripts/build-online-engine-opt-v2.sh

Then execute the versioned harness:

    python3 scripts/qualify-strength-e00.py --phase contracts
    python3 -m unittest discover -s tests/strength_e00 -p 'test_*.py' -v
    python3 scripts/qualify-strength-e00.py --phase smoke
    python3 scripts/qualify-strength-e00.py --phase native-smoke
    python3 scripts/qualify-strength-e00.py --phase full
    python3 scripts/qualify-strength-e00.py --phase verify --run build/test-results/strength-e00/<run-id>

The full phase requires delegated cgroups; the hosted smoke may return
INCONCLUSIVE on its resource evidence even while the source/functional
smoke is valid. An incomplete 512-history campaign must never be
relabelled as completed E00.

## Claim ceiling and next milestone

No source or unit test promotes strength, equal compute, live use, public
release, deployment or S4 outward authorization. E01 matched G3 controls,
full native-clock qualification and an independently replicated E02
value-of-compute experiment remain separate.

Next after independent E00 review: S3 single-slot LC0/Reckless proposer,
same-Stockfish two-root comparator, no replacement authority yet.
