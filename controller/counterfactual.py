"""Counterfactual decision persistence and deterministic replay.

The live proposal is frozen from in-memory evidence while the anchor is still
(or no longer) searching. The sealed artifact is written only after the parent,
VERIFY, optional REFINE, and cross-feed artifacts have finalized.

Nothing in this module writes UCI output or grants DecisionAuthorization.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from controller.crossfeed import (
    CrossFeedError,
    CrossFeedView,
    build_crossfeed_view_from_run,
    load_crossfeed_manifest,
    verify_crossfeed_integrity,
)
from controller.decision import (
    COUNTERFACTUAL_POLICY,
    DECISION_SCHEMA_VERSION,
    OWNER_ORDER,
    CounterfactualDecision,
    DecisionError,
    DecisionEvidence,
    DecisionEvaluation,
    DecisionProposal,
    VerificationTerminalEvidence,
    attach_anchor,
    build_decision_evidence,
    canonical_digest,
    evaluate_decision_policy,
)
from controller.refinement import RefinementError
from controller.replay import (
    ReplayError,
    load_manifest,
    sha256_file,
    verify_bundle_integrity,
)
from controller.verification import (
    VerificationError,
    VerificationRun,
    load_verification_manifest,
    verify_verification_integrity,
)


COUNTERFACTUAL_ARTIFACT_VERSION = 1


class CounterfactualError(RuntimeError):
    """Raised when counterfactual evidence cannot be frozen or replayed honestly."""


def terminal_evidence_from_verification_run(
    verification: VerificationRun,
) -> VerificationTerminalEvidence:
    """Read the authoritative live VERIFY stage terminal facts."""

    final_by_owner: list[tuple[str, str | None]] = []
    stage_dispositions: list[tuple[str, str]] = []
    faults: list[str] = []

    for owner in OWNER_ORDER:
        stage = verification.stage_for_owner(owner)
        if stage is None:
            final_by_owner.append((owner, None))
            stage_dispositions.append((owner, "missing"))
            faults.append(f"VERIFY terminal stage missing for {owner}")
            continue
        if stage.owner != owner:
            faults.append(f"VERIFY terminal stage owner mismatch for {owner}")
        if stage.family != owner:
            faults.append(f"VERIFY terminal family mismatch for {owner}")
        final_by_owner.append((owner, stage.bestmove))
        stage_dispositions.append((owner, stage.disposition))

    complete = (
        verification.disposition == "completed"
        and not faults
        and all(disposition == "completed" for _, disposition in stage_dispositions)
    )

    return VerificationTerminalEvidence(
        verification_id=verification.plan.verification_id,
        candidate_roots=tuple(verification.plan.candidate_roots),
        final_by_owner=tuple(final_by_owner),
        stage_disposition_by_owner=tuple(stage_dispositions),
        run_disposition=verification.disposition,
        complete=complete,
        faults=tuple(sorted(set(faults))),
    )


def terminal_evidence_from_manifest(
    manifest: dict[str, Any],
) -> VerificationTerminalEvidence:
    nomination = manifest.get("nomination") or {}
    candidates = tuple(nomination.get("candidate_roots") or ())
    stage_records = {
        record.get("owner"): record
        for record in manifest.get("stages") or []
        if isinstance(record, dict) and isinstance(record.get("owner"), str)
    }

    final_by_owner: list[tuple[str, str | None]] = []
    stage_dispositions: list[tuple[str, str]] = []
    faults: list[str] = []
    for owner in OWNER_ORDER:
        record = stage_records.get(owner)
        if record is None:
            final_by_owner.append((owner, None))
            stage_dispositions.append((owner, "missing"))
            faults.append(f"VERIFY terminal stage missing for {owner}")
            continue
        if record.get("family") != owner:
            faults.append(f"VERIFY terminal family mismatch for {owner}")
        move = record.get("bestmove")
        final_by_owner.append((owner, move if isinstance(move, str) else None))
        disposition = record.get("disposition")
        stage_dispositions.append(
            (owner, disposition if isinstance(disposition, str) else "invalid")
        )

    run_disposition = (manifest.get("disposition") or {}).get("run")
    run_disposition = (
        run_disposition if isinstance(run_disposition, str) else "invalid"
    )
    complete = (
        run_disposition == "completed"
        and not faults
        and all(disposition == "completed" for _, disposition in stage_dispositions)
    )

    return VerificationTerminalEvidence(
        verification_id=str(manifest.get("verification_id") or ""),
        candidate_roots=candidates,
        final_by_owner=tuple(final_by_owner),
        stage_disposition_by_owner=tuple(stage_dispositions),
        run_disposition=run_disposition,
        complete=complete,
        faults=tuple(sorted(set(faults))),
    )


def prepare_counterfactual(
    *,
    view: CrossFeedView,
    verification: VerificationRun,
    policy: str = COUNTERFACTUAL_POLICY,
) -> tuple[DecisionEvidence, DecisionEvaluation]:
    terminal = terminal_evidence_from_verification_run(verification)
    evidence = build_decision_evidence(view, terminal)
    evaluation = evaluate_decision_policy(evidence, policy=policy)
    return evidence, evaluation


def replay_counterfactual_inputs(
    run_dir: Path | str,
    *,
    policy: str = COUNTERFACTUAL_POLICY,
) -> tuple[CrossFeedView, VerificationTerminalEvidence, DecisionEvidence, DecisionEvaluation]:
    """Reconstruct policy inputs solely from sealed source evidence."""

    run_dir = Path(run_dir)
    view = build_crossfeed_view_from_run(run_dir)
    verification = load_verification_manifest(run_dir)
    terminal = terminal_evidence_from_manifest(verification)
    evidence = build_decision_evidence(view, terminal)
    evaluation = evaluate_decision_policy(evidence, policy=policy)
    return view, terminal, evidence, evaluation


def _anchor_stage(parent: dict[str, Any]) -> dict[str, Any]:
    anchors = [
        stage
        for stage in parent.get("stages") or []
        if isinstance(stage, dict) and stage.get("role") == "anchor"
    ]
    if len(anchors) != 1:
        raise CounterfactualError(
            f"expected exactly one parent anchor stage, found {len(anchors)}"
        )
    anchor = anchors[0]
    if anchor.get("disposition") != "completed":
        raise CounterfactualError("counterfactual sealing requires completed anchor")
    move = anchor.get("bestmove")
    if not isinstance(move, str) or not move:
        raise CounterfactualError("completed anchor is missing bestmove")
    completed_ms = anchor.get("completed_ms")
    if isinstance(completed_ms, bool) or not isinstance(completed_ms, (int, float)):
        raise CounterfactualError("completed anchor is missing completed_ms")
    return anchor


def _proposal_semantics(proposal: DecisionProposal) -> dict[str, Any]:
    return {
        "policy": proposal.policy,
        "disposition": proposal.disposition.as_dict(),
        "move": proposal.move,
        "source_owner": proposal.source_owner,
        "evidence_digest": proposal.evidence_digest,
    }


def _evaluation_semantics(evaluation: DecisionEvaluation) -> dict[str, Any]:
    return {
        "policy": evaluation.policy,
        "disposition": evaluation.disposition.as_dict(),
        "move": evaluation.move,
        "source_owner": evaluation.source_owner,
        "evidence_digest": evaluation.evidence_digest,
    }


def _artifact_core(
    *,
    proposal: DecisionProposal,
    decision: CounterfactualDecision,
    parent_sha: str,
    verification_sha: str,
    crossfeed_manifest: dict[str, Any],
    crossfeed_sha: str,
    anchor_completed_ms: float,
) -> dict[str, Any]:
    return {
        "schema_version": COUNTERFACTUAL_ARTIFACT_VERSION,
        "decision_schema_version": DECISION_SCHEMA_VERSION,
        "policy": proposal.policy,
        "source": {
            "run_id": crossfeed_manifest.get("source", {}).get("run_id"),
            "parent_manifest_sha256": parent_sha,
            "verification_manifest_sha256": verification_sha,
            "crossfeed_id": crossfeed_manifest.get("crossfeed_id"),
            "crossfeed_manifest_sha256": crossfeed_sha,
            "crossfeed_content_sha256": crossfeed_manifest.get("content_sha256"),
        },
        "proposal": proposal.as_dict(),
        "anchor": {
            "move": decision.anchor_move,
            "completed_ms": round(float(anchor_completed_ms), 6),
        },
        "counterfactual": {
            "proposal_matches_anchor": decision.proposal_matches_anchor,
            "would_change_outward_move": decision.would_change_outward_move,
            "outward_authority": decision.outward_authority,
        },
    }


def seal_counterfactual_artifact(
    proposal: DecisionProposal,
    run_dir: Path | str,
) -> dict[str, Any]:
    """Seal a proposal after all source artifacts and the anchor are final."""

    run_dir = Path(run_dir)
    parent_path = run_dir / "manifest.json"
    verification_path = run_dir / "verification" / "manifest.json"
    crossfeed_path = run_dir / "crossfeed" / "manifest.json"
    if not parent_path.is_file() or not verification_path.is_file() or not crossfeed_path.is_file():
        raise CounterfactualError(
            "counterfactual sealing requires parent, VERIFY and cross-feed manifests"
        )

    parent_problems = verify_bundle_integrity(run_dir)
    verify_problems = verify_verification_integrity(run_dir)
    crossfeed_problems = verify_crossfeed_integrity(run_dir)
    if parent_problems:
        raise CounterfactualError(f"parent replay integrity failed: {parent_problems}")
    if verify_problems:
        raise CounterfactualError(f"VERIFY integrity failed: {verify_problems}")
    if crossfeed_problems:
        raise CounterfactualError(
            f"cross-feed integrity failed: {crossfeed_problems}"
        )

    parent = load_manifest(run_dir)
    verification = load_verification_manifest(run_dir)
    crossfeed = load_crossfeed_manifest(run_dir)
    _, _, evidence, evaluation = replay_counterfactual_inputs(
        run_dir,
        policy=proposal.policy,
    )
    if _proposal_semantics(proposal) != _evaluation_semantics(evaluation):
        raise CounterfactualError(
            "live counterfactual proposal does not match deterministic replay"
        )
    if evidence.digest != proposal.evidence_digest:
        raise CounterfactualError("live proposal evidence digest mismatch")

    anchor = _anchor_stage(parent)
    anchor_completed_ms = float(anchor["completed_ms"])
    # completed_ms is rounded in Replay schema v1 while the live causal stamp
    # has microsecond resolution. A 1 ms tolerance checks consistency without
    # pretending the rounded replay timestamp can reconstruct lock ordering.
    if (
        proposal.frozen_before_anchor
        and proposal.frozen_observed_ms > anchor_completed_ms + 1.0
    ):
        raise CounterfactualError(
            "proposal claims PRE_ANCHOR but its timestamp is after anchor completion"
        )
    if (
        not proposal.frozen_before_anchor
        and proposal.frozen_observed_ms < anchor_completed_ms - 1.0
    ):
        raise CounterfactualError(
            "proposal claims POST_ANCHOR but its timestamp precedes anchor completion"
        )

    decision = attach_anchor(proposal, anchor_move=str(anchor["bestmove"]))
    core = _artifact_core(
        proposal=proposal,
        decision=decision,
        parent_sha=sha256_file(parent_path),
        verification_sha=sha256_file(verification_path),
        crossfeed_manifest=crossfeed,
        crossfeed_sha=sha256_file(crossfeed_path),
        anchor_completed_ms=anchor_completed_ms,
    )
    digest = canonical_digest(core)
    manifest = {
        **core,
        "decision_id": f"{proposal.evidence_digest[:16]}:counterfactual-v1:{digest[:16]}",
        "content_sha256": digest,
    }

    target_dir = run_dir / "decision"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "counterfactual.json"
    target.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def load_counterfactual_artifact(run_dir: Path | str) -> dict[str, Any]:
    path = Path(run_dir) / "decision" / "counterfactual.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CounterfactualError(
            f"cannot load counterfactual artifact {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise CounterfactualError("counterfactual artifact root must be an object")
    version = data.get("schema_version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != COUNTERFACTUAL_ARTIFACT_VERSION
    ):
        raise CounterfactualError(
            f"unsupported counterfactual schema_version: {version!r}"
        )
    return data


def _proposal_from_artifact(data: dict[str, Any]) -> DecisionProposal:
    from controller.decision import DecisionDisposition

    disposition = data.get("disposition") or {}
    if not isinstance(disposition, dict):
        raise CounterfactualError("proposal disposition must be an object")
    move = data.get("move")
    if move is not None and not isinstance(move, str):
        raise CounterfactualError("proposal move must be a string or null")
    source_owner = data.get("source_owner")
    if source_owner is not None and not isinstance(source_owner, str):
        raise CounterfactualError("proposal source_owner must be a string or null")
    frozen_before_anchor = data.get("frozen_before_anchor")
    if not isinstance(frozen_before_anchor, bool):
        raise CounterfactualError("proposal frozen_before_anchor must be boolean")
    try:
        return DecisionProposal(
            policy=str(data.get("policy") or ""),
            disposition=DecisionDisposition(
                code=str(disposition.get("code") or ""),
                reason=str(disposition.get("reason") or ""),
            ),
            move=move,
            source_owner=source_owner,
            evidence_digest=str(data.get("evidence_digest") or ""),
            frozen_observed_ms=float(data.get("frozen_observed_ms")),
            frozen_before_anchor=frozen_before_anchor,
        )
    except (TypeError, ValueError, DecisionError) as exc:
        raise CounterfactualError(f"invalid stored proposal: {exc}") from exc


def verify_counterfactual_integrity(run_dir: Path | str) -> list[str]:
    """Replay policy semantics and verify provenance / authority separation."""

    run_dir = Path(run_dir)
    problems: list[str] = []
    try:
        artifact = load_counterfactual_artifact(run_dir)
        parent = load_manifest(run_dir)
        verification = load_verification_manifest(run_dir)
        crossfeed = load_crossfeed_manifest(run_dir)
    except (CounterfactualError, ReplayError, VerificationError, CrossFeedError) as exc:
        return [str(exc)]

    source = artifact.get("source") or {}
    parent_path = run_dir / "manifest.json"
    verification_path = run_dir / "verification" / "manifest.json"
    crossfeed_path = run_dir / "crossfeed" / "manifest.json"

    if source.get("run_id") != parent.get("run_id"):
        problems.append("counterfactual source run_id mismatch")
    if source.get("parent_manifest_sha256") != sha256_file(parent_path):
        problems.append("counterfactual parent manifest hash mismatch")
    if source.get("verification_manifest_sha256") != sha256_file(verification_path):
        problems.append("counterfactual VERIFY manifest hash mismatch")
    if source.get("crossfeed_id") != crossfeed.get("crossfeed_id"):
        problems.append("counterfactual cross-feed id mismatch")
    if source.get("crossfeed_manifest_sha256") != sha256_file(crossfeed_path):
        problems.append("counterfactual cross-feed manifest hash mismatch")
    if source.get("crossfeed_content_sha256") != crossfeed.get("content_sha256"):
        problems.append("counterfactual cross-feed content hash mismatch")

    for problem in verify_bundle_integrity(run_dir):
        problems.append(f"parent: {problem}")
    for problem in verify_verification_integrity(run_dir):
        problems.append(f"VERIFY: {problem}")
    for problem in verify_crossfeed_integrity(run_dir):
        problems.append(f"cross-feed: {problem}")

    try:
        proposal = _proposal_from_artifact(artifact.get("proposal") or {})
        _, _, evidence, evaluation = replay_counterfactual_inputs(
            run_dir,
            policy=proposal.policy,
        )
    except (
        CounterfactualError,
        DecisionError,
        CrossFeedError,
        VerificationError,
        RefinementError,
        ReplayError,
    ) as exc:
        problems.append(f"counterfactual deterministic replay failed: {exc}")
        proposal = None
        evidence = None
        evaluation = None

    if proposal is not None and evaluation is not None and evidence is not None:
        if _proposal_semantics(proposal) != _evaluation_semantics(evaluation):
            problems.append(
                "stored proposal semantics do not match deterministic policy replay"
            )
        if proposal.evidence_digest != evidence.digest:
            problems.append("stored proposal evidence digest mismatch")

        try:
            anchor = _anchor_stage(parent)
        except CounterfactualError as exc:
            problems.append(str(exc))
        else:
            completed_ms = float(anchor["completed_ms"])
            if (
                proposal.frozen_before_anchor
                and proposal.frozen_observed_ms > completed_ms + 1.0
            ):
                problems.append("PRE_ANCHOR proposal timestamp is inconsistent")
            if (
                not proposal.frozen_before_anchor
                and proposal.frozen_observed_ms < completed_ms - 1.0
            ):
                problems.append("POST_ANCHOR proposal timestamp is inconsistent")
            decision = attach_anchor(proposal, anchor_move=str(anchor["bestmove"]))
            stored_anchor = artifact.get("anchor") or {}
            stored_counterfactual = artifact.get("counterfactual") or {}
            if stored_anchor.get("move") != decision.anchor_move:
                problems.append("stored anchor move mismatch")
            stored_completed_ms = stored_anchor.get("completed_ms")
            if (
                isinstance(stored_completed_ms, bool)
                or not isinstance(stored_completed_ms, (int, float))
                or float(stored_completed_ms) != completed_ms
            ):
                problems.append("stored anchor completion time mismatch")
            if (
                stored_counterfactual.get("proposal_matches_anchor")
                != decision.proposal_matches_anchor
            ):
                problems.append("stored proposal/anchor relation mismatch")
            if (
                stored_counterfactual.get("would_change_outward_move")
                != decision.would_change_outward_move
            ):
                problems.append("stored counterfactual change flag mismatch")
            if stored_counterfactual.get("outward_authority") != "stockfish-anchor":
                problems.append("counterfactual artifact changed outward authority")

    core = {
        key: value
        for key, value in artifact.items()
        if key not in {"decision_id", "content_sha256"}
    }
    digest = canonical_digest(core)
    if artifact.get("content_sha256") != digest:
        problems.append("counterfactual content digest mismatch")
    proposal_data = artifact.get("proposal") or {}
    evidence_digest = proposal_data.get("evidence_digest")
    if isinstance(evidence_digest, str):
        expected_id = f"{evidence_digest[:16]}:counterfactual-v1:{digest[:16]}"
        if artifact.get("decision_id") != expected_id:
            problems.append("counterfactual decision id mismatch")
    else:
        problems.append("counterfactual artifact missing proposal evidence digest")

    if artifact.get("decision_schema_version") != DECISION_SCHEMA_VERSION:
        problems.append("counterfactual decision schema version mismatch")
    if artifact.get("policy") != COUNTERFACTUAL_POLICY:
        problems.append("counterfactual policy mismatch")

    # The artifact must never contain an authorization grant.
    blob = json.dumps(artifact, sort_keys=True).lower()
    if '"authorized": true' in blob:
        problems.append("counterfactual artifact contains forbidden authorization grant")

    return problems
