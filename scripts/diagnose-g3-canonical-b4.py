#!/usr/bin/env python3
"""Bounded source-locked G3 23-case triage against canonical b4; NEVER promotion evidence."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.engine_opt.domain import candidate_bundle_identity, load_execution_domain

POLICY = "qualification/online-hybrid-v2-candidate.json"
LEGACY = "qualification/online-hybrid-v2.json"
SELECTION = "qualification/engine-opt-v2-selection.json"
REFERENCE = "config/allfather.online-engine-opt-v2.json"
CANONICAL = "config/allfather.online-hybrid-v2.validation.json"
TRIAGE = "config/allfather.online-hybrid-v2.g3-triage.json"
REPLAY = "build/replays-g3-canonical-b4-triage"
RESULT = "build/test-results/g3-canonical-b4-triage"


class TriageError(RuntimeError):
    pass


def require(value: bool, why: str) -> None:
    if not value:
        raise TriageError(why)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_at(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"invalid JSON object: {path}")
    return value


def verify_overlay(canonical: dict, triage: dict, policy: dict,
                   legacy: dict, selection: dict) -> None:
    expected = copy.deepcopy(canonical)
    require(expected["shadow"]["replay_root"] != REPLAY, "canonical replay already triage")
    expected["shadow"]["replay_root"] = REPLAY
    require(triage == expected, "triage runtime changed more than shadow.replay_root")
    require(((selection.get("selected") or {}).get("lc0") or {}).get("matrix_profile")
            == "b4-p0-c256k-cold", "not canonical b4 selection")
    require(policy.get("runtime_config") == "config/allfather.online-hybrid-v2.candidate.validation.json",
            "candidate-policy declared runtime drift")
    require(policy.get("reference_runtime") == "config/allfather.online-engine-opt-v2.candidate.json",
            "candidate-policy declared reference drift")
    meta = policy.get("candidate_witnesses") or {}
    require(meta.get("legacy_policy_path") == LEGACY, "unapproved legacy policy migration")
    require(meta.get("legacy_case_count") == 7 and meta.get("discovery_case_count") == 16,
            "frozen witness counts drift")
    cases = policy.get("positive_cases") or []
    require(len(cases) == 23, "expected exactly 23 frozen diagnostic positions")
    actual_legacy = [{k: v for k, v in case.items() if k != "source_set"}
                     for case in cases if case.get("source_set") == "legacy_v1"]
    require(actual_legacy == legacy.get("positive_cases") and len(actual_legacy) == 7,
            "immutable seven-case legacy baseline drift")
    require(policy.get("positive_requirement") == legacy.get("positive_requirement"),
            "frozen non-anchor G3 authority requirement drift")
    require(sum(case.get("source_set") == "discovery_local1_v1" for case in cases) == 16,
            "historical discovery corpus drift")


def write_sealed(path: Path, body: dict) -> None:
    core = dict(body)
    core.pop("content_sha256", None)
    encoded = json.dumps(core, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    core["content_sha256"] = hashlib.sha256(encoded).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(core, sort_keys=True, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def main() -> int:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--execution-domain", type=Path)
    args.add_argument("--expected-head")
    args.add_argument("--static", action="store_true")
    options = args.parse_args()
    paths = [POLICY, LEGACY, SELECTION, REFERENCE, CANONICAL, TRIAGE,
             "qualification/online-hybrid-v2-candidate-witnesses.json",
             "qualification/g3-candidate-discovery-source.json"]
    contracts = {name: digest(ROOT / name) for name in paths}
    policy = json_at(ROOT / POLICY)
    legacy = json_at(ROOT / LEGACY)
    canonical = json_at(ROOT / CANONICAL)
    triage = json_at(ROOT / TRIAGE)
    selection = json_at(ROOT / SELECTION)
    verify_overlay(canonical, triage, policy, legacy, selection)
    if options.static:
        print(json.dumps({"static_contract": True, "diagnostic_only": True,
                          "contract_hashes": contracts}, sort_keys=True))
        return 0
    require(options.execution_domain is not None and options.expected_head is not None,
            "execution domain and expected head required")
    head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                                   text=True).strip()
    require(head == options.expected_head, "diagnostic is not on exact Git HEAD")
    domain = load_execution_domain(options.execution_domain.resolve())
    require(domain["source_commit"] == head, "execution-domain source drift")
    bundle = candidate_bundle_identity(ROOT / "build/online-engine-opt-v2",
                                       expected_source_commit=head)
    out = ROOT / RESULT
    raw_root = out / "raw-qualifier"
    replay_root = ROOT / REPLAY
    require(not replay_root.exists(), "triage replay path must be initially empty")
    require(not raw_root.exists(), "triage qualifier output path must be initially empty")
    env = os.environ.copy()
    env.update({
        "ALLFATHER_G3_POLICY": POLICY,
        "ALLFATHER_G3_SELECTION": SELECTION,
        "ALLFATHER_G3_REFERENCE": REFERENCE,
        "ALLFATHER_G3_CONFIG": TRIAGE,
        "ALLFATHER_G3_RESULT": str(raw_root),
        "ALLFATHER_EXECUTION_DOMAIN_PATH": str(options.execution_domain.resolve()),
    })
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/qualify-online-hybrid-v2.py"),
                           "--record-disposition"], cwd=ROOT, env=env,
                          capture_output=True, text=True, check=False)
    out.mkdir(parents=True, exist_ok=True)
    (out / "qualifier.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (out / "qualifier.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    raw_path = raw_root / "report.json"
    raw = json_at(raw_path) if raw_path.is_file() else {}
    actual = raw.get("cases") or []
    complete = (proc.returncode == 0 and raw.get("evidence_valid") is True
                and raw.get("source_commit") == head and raw.get("execution_domain") == domain
                and raw.get("candidate_bundle") == bundle
                and len(actual) <= 23
                and len({c.get("case") for c in actual}) == len(actual))
    found = bool(raw.get("authority_qualified") is True and raw.get("positive_case"))
    report = {
        "schema_version": 1,
        "kind": "g3-canonical-b4-diagnostic",
        "source_commit": head,
        "diagnostic_complete": bool(complete),
        "diagnostic_positive_witness": found if complete else False,
        "cases_exercised": len(actual),
        "first_positive_stops_run": True,
        "qualifier_returncode": proc.returncode,
        "raw_qualifier_report_sha256": digest(raw_path) if raw_path.is_file() else None,
        "contracts": contracts,
        "candidate_bundle": bundle,
        "execution_domain": domain,
        "runtime_override": {
            "policy_declares_candidate_files": True,
            "reference_actually_used": REFERENCE,
            "selection_actually_used": SELECTION,
            "runtime_actually_used": TRIAGE,
            "only_allowed_runtime_difference": "shadow.replay_root",
        },
        "qualification_disposition": ("DIAGNOSTIC_POSITIVE_NOT_PROMOTION"
                                      if complete and found else
                                      "DIAGNOSTIC_NEGATIVE_NOT_PROMOTION" if complete else
                                      "DIAGNOSTIC_INCOMPLETE"),
        "promotion_evidence": False,
        "promotable": False,
        "canonical_policy_changed": False,
        "claim_boundary": {"canonical_b4_promotion": False,
                           "strength": False, "elo": False,
                           "equal_compute": False, "deployment": False},
    }
    write_sealed(out / "report.json", report)
    print(json.dumps({"disposition": report["qualification_disposition"],
                      "cases_exercised": len(actual)}, sort_keys=True))
    return 0 if complete else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (TriageError, OSError, ValueError, KeyError) as exc:
        out = ROOT / RESULT
        write_sealed(out / "report.json", {
            "schema_version": 1, "kind": "g3-canonical-b4-diagnostic",
            "diagnostic_complete": False, "promotion_evidence": False,
            "promotable": False, "canonical_policy_changed": False,
            "qualification_disposition": "DIAGNOSTIC_INCOMPLETE",
            "error": f"{type(exc).__name__}: {exc}",
        })
        print(f"G3 canonical-b4 triage failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
