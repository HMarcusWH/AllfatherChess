# STRENGTH S0 — historical G3 evidence repair and source-locked audit

**Status:** Implementation candidate on branch strength/s0-verified-baseline,
stacked on unmerged [PR #81](https://github.com/HMarcusWH/AllfatherChess/pull/81).
This work is **offline read-only telemetry**, not a new playing policy, Elo
claim, matched-compute confirmation or release/live authorization.

## Historical baseline (not a holdout)

The actual G3 pilot was run on 25 October 2026 on commit
796228796c098df88ad406d595dda4c6db3d5848, Actions run 40216238892,
artifact 12609439308. The retained ZIP's SHA256 is

    9be9eb650689916847f1fa61f7835f3c7c3a6d4b399070cf03d454435fee68f6

The prior source-verified campaign reported 16 completed games (G3 0W/4D/12L),
993 replay decisions, 402 authorized HYBRID decisions and 140 actual changes
from the anchor. Its report incorrectly said zero completed VERIFY triples:
the original producer counted VERIFY phases in the parent EXPLORE manifest.
Nested evidence shows **925 completed three-owner VERIFY sets**, plus **923
completed and 2 incomplete three-owner STAGED_VERIFY sets**. Do not treat
the two stopped LC0 extension stages as completed.

## Source implementation

- tools/g3_pilot_health.py: read only actual verification/manifest.json and
  staged_verification/manifest.json; call the original independent stream,
  provenance and hash verifiers; check that each nested source's SHA256
  appears in the final decision source index; differentiate not-attempted,
  incomplete and complete without retroactive source modification.
- tests/g3_pilot/test_nested_measurement.py: missing/orphan, hash tampering,
  invalid owner, failed independent verifier, incomplete extension.
- tools/strength_lab/extract.py: bounded, source-locked ZIP acquisition or
  historical Actions artifact directory; independent PGN W/D/L, occurrence
  identity, original manifest identity and per-move nested evidence audit.
- qualification/strength/dev-g3-20261025-v1.json: immutable audit source and
  expected baseline counts; development-only claim cap.
- .github/workflows/strength-s0-audit.yml: independently fetches original
  Actions artifact, remeasures the complete game trace, emits a new
  source-bound report. It does not rerun/re-label the old games.

The extracted-folder route can prove the retained contents' source and hash
consistency, but it does not by itself rehash the original zipped artifact.
For byte-level archive acquisition, run the --archive mode against the
named zip file with the frozen outer SHA256. The derived audit output
explicitly records which acquisition proof was performed.

Example local command once the historical source ZIP is available:

    python3 -m tools.strength_lab.extract \
      --archive build/reference/g3-pilot-raw-40216238892-1.zip \
      --audit-id dev-g3-20261025 \
      --output build/strength/s0/reconciled-<unique-attempt>.json

Do not overwrite the raw ZIP, its original report.json, or any other original
replay. The corrected counters are independently derived, not edited in place.

## Stop gates and later stages

S0 must pass the full existing local-game/G3 unit suite, historical archive
reconciliation and destructive mutations, without changing the original
qualified profiles or failing the other CI gates.

S1 is next: a **separately versioned G3 anchor-output-only diagnostic
control** and whole-participant resource accounting. Do not conflate it
with the existing J12 ANCHOR_CONTROL which has different admission rules.
S2–S4 then introduce native-clock incumbent tracking, bounded specialist
challenges, adequate two-root comparisons and a new quality/authority
policy in opt-in experimental mode. S5 measures net compute value;
S6/S7 are conditional; S8 must independently demonstrate any strength claim.

No existing G3/J12 outcome may be promoted by merely fixing its telemetry.
