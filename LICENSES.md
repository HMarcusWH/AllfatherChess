# Licensing and provenance notice

> **Provenance/legal status (25 October 2026):** The repository also pins
> `lichess-bot` (AGPL-3.0) as the separate API bridge in
> `deploy/lichess/bridge.lock.json`; consult its actual upstream license
> and the component map in `qualification/license-compliance-v1.json`.
> A source lock or offline SLSA/SPDX audit is not a combined-work licensing
> opinion. Controller distribution license, GPL/AGPL obligations for any
> combined release or remote network service, corresponding source packaging,
> and a human legal review all remain **unapproved**. No distribution or public
> BOT use is authorized by this notice.


AllfatherChess contains vendored third-party/open-source engine code with its original notices and license texts preserved in the corresponding engine directories.

- `engines/stockfish/` — Stockfish source, distributed under GNU GPL v3; see `engines/stockfish/Copying.txt`.
- `engines/lc0/` — LC0 source, distributed under GNU GPL v3-or-later terms in its source notices, including its documented Section 7 permission for certain NVIDIA CUDA/cuDNN combinations; see `engines/lc0/COPYING` and source headers.
- `engines/reckless/` — Reckless source, distributed under GNU AGPL v3; see `engines/reckless/LICENSE`.

The exact source snapshots are recorded in `vendor.lock.json` and `docs/UPSTREAM_PROVENANCE.md`.

No top-level relicensing of third-party code is implied by this file. AllfatherChess-specific controller code will receive an explicit licensing decision before a combined distribution is promoted. Distribution of a tightly combined/native binary should receive a deliberate license-compliance review.

This document is a repository provenance notice, not legal advice.
