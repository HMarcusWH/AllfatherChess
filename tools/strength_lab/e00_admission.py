"""Write a non-authorizing E00 evidence candidate for external scientific review.

This is intentionally not a qualification certificate. A complete qualified
host run remains a candidate until an independent reviewer approves evidence.
"""
from __future__ import annotations
import json
from pathlib import Path
from controller.strength.integrity import strict_load
from tools.strength_lab.e00_runner import sha,write,ROOT_OUT
from tools.strength_lab.e00_verify import verify_campaign,E00Failure
from tools.strength_lab.e00_corpus import ROOT

POLICY=ROOT/"qualification/strength/e00-admission-contract-v1.json"

def review_candidate(run:Path)->dict:
    run=run.resolve(strict=True)
    if not run.is_dir() or not run.is_relative_to(ROOT_OUT.resolve()):
        raise E00Failure("E00 candidate source outside owned evidence root")
    config=strict_load(POLICY)
    if (config.get("schema_version")!=1 or
        config.get("kind")!="strength_e00_admission_policy_v1" or
        config.get("verdict")!="REVIEW_REQUIRED" or
        any(config.get(k) is not False for k in (
            "scientific_qualified","strength_qualified","equal_compute",
            "live_game_authorized","public_bot_release","release_authorized"))):
        raise E00Failure("E00 admission policy claims unsupported authority")
    verified=verify_campaign(run)
    if not (verified["phase"]=="full" and verified["status"]=="PASS" and
            verified["actual_history_count"]==512 and
            verified["fixed_node_bestmove_agreement"]==512 and
            verified["flight_reconstructed_count"]==512 and
            verified["resource_qualified"] is True and
            verified["e00_scientifically_qualified"] is False and
            verified["strength_qualified"] is False):
        raise E00Failure("E00 only full 512 strict resource PASS may request review")
    manifest=strict_load(run/"manifest.json")
    independent_file=run/"independent-verdict.json"
    if independent_file.exists():
        recorded=strict_load(independent_file)
        if recorded!=verified:
            raise E00Failure("retained independent E00 verdict disagrees with replay")
    else:
        write(independent_file,verified)
    source_ids={
        "source_commit":verified["source_commit"],
        "policy_sha256":manifest["policy_sha256"],
        "manifest_sha256":sha(run/"manifest.json"),
        "independent_verdict_sha256":sha(independent_file),
        "corpus_sha256":manifest["corpus_sha256"],
        "profile_sha256":manifest["profile_sha256"],
        "binary_sha256":manifest["binary_sha256"],
        "bundle_manifest_sha256":manifest["bundle_manifest_sha256"],
        "host_pre_sha256":manifest["host_profile"]["sha256"],
        "host_post_sha256":manifest["host_post"]["sha256"],
    }
    result={
        "schema_version":1,"kind":"strength_e00_review_candidate_v1",
        "verdict":"REVIEW_REQUIRED",
        "source":source_ids,
        "method":"fixed_nodes_50000_same_pinned_stockfish_direct_vs_s2",
        "counts":{"full_histories":512,"matching_bestmoves":512,
                  "verified_anchor_flights":512},
        "resource_evidence":verified["whole_participant_measurements"],
        "paired_metrics":verified["independent_paired_metrics"],
        "native_clock_performance_qualified":False,
        "human_review_completed":False,
        "scientific_qualified":False,
        "strength_qualified":False,
        "equal_compute":False,
        "live_game_authorized":False,
        "public_bot_release":False,
        "release_authorized":False
    }
    write(run/"e00-review-candidate.json",result)
    return result
