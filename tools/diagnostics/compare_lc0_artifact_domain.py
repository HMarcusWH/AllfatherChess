#!/usr/bin/env python3
"""Compare same-host LC0 A/B/C matrices without promoting any profile.

PR #48 uses this module for one bounded diagnostic question:

* A is the exact PR #44 qualified head.
* B is the exact post-J2 main head.
* C is a second clean rebuild of B on the same worker.

The executable chess/profile workload remains owned by the unchanged
scripts/engine-opt-matrix.py at A and B.  This module only proves the declared
source surface is equivalent and compares the resulting sealed reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.host_capabilities import HostCapabilities
from controller.runtime_substrate import RuntimeSubstrate


EXPECTED_A = "085420843b95f3f2dd206fc1c66bf642cbd49b6d"
EXPECTED_B = "5d566943a331d7d62fc02e8923c3ad9e4d7a0f2f"
EXPECTED_SELECTED = "b7-p8-c256k-warm64"
EXPECTED_BASELINE = "v1-current-cold"
EXPECTED_CASES = 8
EXPECTED_REPEATS = 3

SURFACE_PATHS = (
    "engines/lc0",
    "engines/stockfish",
    "engines/reckless",
    "tools/engine_opt",
    "scripts/build-lc0-strength.sh",
    "scripts/build-online-engine-opt-v2.sh",
    "scripts/engine-opt-matrix.py",
    "scripts/qualify-engine-opt-evidence.py",
    "qualification/lc0-strength.lock.json",
    "qualification/lc0-strength-profile.json",
    "qualification/engine-opt-v2.json",
    "qualification/engine-opt-v2-selection.json",
    "qualification/engine-opt-v2-evidence.json",
    "config/allfather.online-engine-opt-v2.json",
    "config/allfather.online-hybrid-v2.validation.json",
    "tests/fixtures/engine_opt",
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HOST_DOMAIN = re.compile(r"^host-domain/[0-9a-f]{20}$")
_RUNTIME_ID = re.compile(r"^runtime-substrate/[0-9a-f]{20}$")
_BUILD_ID = re.compile(r"\+git\.([0-9a-f]{7,40})\b")


class DiagnosticError(RuntimeError):
    """Raised when diagnostic evidence is malformed or causally ambiguous."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DiagnosticError(message)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _core_digest(payload: dict[str, Any]) -> str:
    core = dict(payload)
    core.pop("content_sha256", None)
    encoded = json.dumps(
        core,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_sealed_json(path: Path, payload: dict[str, Any]) -> None:
    core = dict(payload)
    core.pop("content_sha256", None)
    sealed = {**core, "content_sha256": _core_digest(core)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sealed, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiagnosticError(f"{label}: cannot load JSON: {exc}") from exc
    require(isinstance(value, dict), f"{label}: root must be an object")
    return value


def load_sealed_json(path: Path, *, label: str) -> dict[str, Any]:
    value = load_json(path, label=label)
    digest = value.get("content_sha256")
    require(
        isinstance(digest, str) and _HEX64.fullmatch(digest) is not None,
        f"{label}: missing or malformed content_sha256",
    )
    require(
        digest == _core_digest(value),
        f"{label}: content_sha256 does not verify",
    )
    return value


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "output", "")
        raise DiagnosticError(
            f"git -C {root} {' '.join(args)} failed: {detail or exc}"
        ) from exc


def build_surface_proof(
    source_a: Path,
    source_b: Path,
    *,
    expected_a: str = EXPECTED_A,
    expected_b: str = EXPECTED_B,
) -> dict[str, Any]:
    commit_a = _git(source_a, "rev-parse", "HEAD")
    commit_b = _git(source_b, "rev-parse", "HEAD")
    require(commit_a == expected_a, f"A checkout drift: {commit_a} != {expected_a}")
    require(commit_b == expected_b, f"B checkout drift: {commit_b} != {expected_b}")

    rows: list[dict[str, Any]] = []
    for path in SURFACE_PATHS:
        left = _git(source_a, "rev-parse", f"HEAD:{path}")
        right = _git(source_b, "rev-parse", f"HEAD:{path}")
        rows.append(
            {
                "path": path,
                "a_object": left,
                "b_object": right,
                "equal": left == right,
            }
        )

    return {
        "schema_version": 1,
        "kind": "lc0-artifact-domain-source-equivalence",
        "a": {
            "commit": commit_a,
            "tree": _git(source_a, "rev-parse", "HEAD^{tree}"),
        },
        "b": {
            "commit": commit_b,
            "tree": _git(source_b, "rev-parse", "HEAD^{tree}"),
        },
        "paths": rows,
        "equivalent": all(bool(row["equal"]) for row in rows),
        "claim_boundary": {
            "diagnostic_only": True,
            "profile_promotion": False,
            "strength": False,
            "elo": False,
        },
    }


def validate_surface_proof(
    proof: dict[str, Any],
    *,
    expected_a: str = EXPECTED_A,
    expected_b: str = EXPECTED_B,
) -> None:
    require(
        proof.get("kind") == "lc0-artifact-domain-source-equivalence",
        "surface proof kind mismatch",
    )
    require((proof.get("a") or {}).get("commit") == expected_a, "surface A mismatch")
    require((proof.get("b") or {}).get("commit") == expected_b, "surface B mismatch")
    rows = proof.get("paths")
    require(isinstance(rows, list) and rows, "surface proof has no paths")
    require(
        [row.get("path") for row in rows] == list(SURFACE_PATHS),
        "surface proof path set/order mismatch",
    )
    for row in rows:
        require(row.get("equal") is True, f"surface mismatch: {row.get('path')}")
        require(
            isinstance(row.get("a_object"), str)
            and isinstance(row.get("b_object"), str)
            and row["a_object"] == row["b_object"],
            f"surface object mismatch: {row.get('path')}",
        )
    require(proof.get("equivalent") is True, "surface proof is not equivalent")


def _matrix_profiles(doc: dict[str, Any]) -> list[str]:
    profiles = doc.get("profiles")
    require(isinstance(profiles, list) and profiles, "matrix profiles missing")
    require(
        all(isinstance(item, str) and item for item in profiles),
        "matrix profiles must be non-empty strings",
    )
    require(len(profiles) == len(set(profiles)), "matrix profiles contain duplicates")
    return list(profiles)


def index_matrix(doc: dict[str, Any]) -> dict[str, Any]:
    """Validate row/repeat structure and return deterministic vector indexes."""
    profiles = _matrix_profiles(doc)
    confirmation = doc.get("confirmation")
    require(isinstance(confirmation, dict), "matrix confirmation missing")
    selected = confirmation.get("selected_profile")
    baseline = confirmation.get("baseline_profile")
    repeats = confirmation.get("repeats")
    require(selected == EXPECTED_SELECTED, "selected profile drift")
    require(baseline == EXPECTED_BASELINE, "baseline profile drift")
    require(repeats == EXPECTED_REPEATS, "confirmation repeat count drift")
    require(selected in profiles and baseline in profiles, "confirmation profile absent")

    errors = doc.get("errors")
    require(errors == [], "matrix contains execution errors")

    rows = doc.get("rows")
    require(isinstance(rows, list) and rows, "matrix rows missing")

    keyed: dict[tuple[str, int, str], dict[str, Any]] = {}
    orders: dict[tuple[str, int], list[str]] = {}
    for row in rows:
        require(isinstance(row, dict), "matrix row must be an object")
        profile = row.get("profile")
        repeat = row.get("repeat_index")
        case_id = row.get("case_id")
        metrics = row.get("metrics")
        require(profile in profiles, f"unknown row profile: {profile!r}")
        require(type(repeat) is int and repeat >= 0, "invalid repeat_index")
        require(isinstance(case_id, str) and case_id, "invalid case_id")
        require(isinstance(metrics, dict), "row metrics missing")
        key = (profile, repeat, case_id)
        require(key not in keyed, f"duplicate matrix row: {key}")
        keyed[key] = row
        orders.setdefault((profile, repeat), []).append(case_id)

    reference_order = orders.get((profiles[0], 0))
    require(reference_order is not None, "first profile repeat 0 missing")
    require(
        len(reference_order) == EXPECTED_CASES,
        f"frozen corpus case count drift: {len(reference_order)}",
    )
    require(
        len(reference_order) == len(set(reference_order)),
        "frozen corpus contains duplicate case ids",
    )

    for profile in profiles:
        expected = EXPECTED_REPEATS if profile in {selected, baseline} else 1
        seen_repeats = sorted(
            repeat for (p, repeat) in orders if p == profile
        )
        require(
            seen_repeats == list(range(expected)),
            f"{profile}: repeat coverage mismatch {seen_repeats}",
        )
        for repeat in seen_repeats:
            order = orders[(profile, repeat)]
            require(
                order == reference_order,
                f"{profile}/repeat-{repeat}: case order/coverage mismatch",
            )

    return {
        "profiles": profiles,
        "selected": selected,
        "baseline": baseline,
        "repeats": repeats,
        "case_order": list(reference_order),
        "keyed": keyed,
    }


def _vectors(index: dict[str, Any], profile: str, repeat: int) -> dict[str, Any]:
    bestmoves: list[Any] = []
    native: list[Any] = []
    for case_id in index["case_order"]:
        row = index["keyed"][(profile, repeat, case_id)]
        metrics = row["metrics"]
        bestmoves.append(metrics.get("bestmove"))
        native.append(metrics.get("native_work_value"))
    return {
        "bestmoves": bestmoves,
        "native_work_values": native,
    }


def _repeatability(index: dict[str, Any], profile: str) -> dict[str, Any]:
    vectors = [
        _vectors(index, profile, repeat)
        for repeat in range(index["repeats"])
    ]
    best = [item["bestmoves"] for item in vectors]
    native = [item["native_work_values"] for item in vectors]
    return {
        "profile": profile,
        "repeat_count": len(vectors),
        "bestmove_vectors": best,
        "native_work_vectors": native,
        "bestmove_stable": all(item == best[0] for item in best[1:]),
        "native_work_stable": all(item == native[0] for item in native[1:]),
    }


def extract_lc0_identity(
    doc: dict[str, Any],
    *,
    expected_commit: str,
) -> dict[str, str]:
    names: set[str] = set()
    for row in doc.get("rows", []):
        transcript = row.get("transcript")
        require(isinstance(transcript, list), "matrix transcript missing")
        for line in transcript:
            if isinstance(line, str) and line.startswith("<< id name "):
                names.add(line[len("<< id name ") :].strip())
    require(len(names) == 1, f"expected one LC0 UCI identity, got {sorted(names)}")
    name = next(iter(names))
    match = _BUILD_ID.search(name)
    require(match is not None, f"LC0 UCI identity lacks git build id: {name!r}")
    short = match.group(1)
    require(
        expected_commit.startswith(short),
        f"LC0 build id {short} is not derived from {expected_commit}",
    )
    return {"uci_name": name, "git_build_id": short}


def validate_matrix_document(
    doc: dict[str, Any],
    *,
    label: str,
    expected_source: str,
    binary_sha256: str,
    weights_sha256: str,
) -> dict[str, Any]:
    require(doc.get("schema_version") == 1, f"{label}: schema drift")
    require(doc.get("kind") == "lc0-cpu-runtime-matrix", f"{label}: kind drift")
    require((doc.get("source") or {}).get("commit") == expected_source, f"{label}: source drift")
    require((doc.get("binary") or {}).get("sha256") == binary_sha256, f"{label}: binary hash drift")
    require((doc.get("weights") or {}).get("sha256") == weights_sha256, f"{label}: network hash drift")
    require(doc.get("nodes") == 16, f"{label}: node request drift")
    require(float(doc.get("deadline_ms")) == 3500.0, f"{label}: deadline drift")
    index = index_matrix(doc)
    identity = extract_lc0_identity(doc, expected_commit=expected_source)
    return {"index": index, "identity": identity}


def _cross(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    require(left["profiles"] == right["profiles"], "matrix profile sets differ")
    require(left["case_order"] == right["case_order"], "matrix case order differs")
    by_profile: dict[str, dict[str, bool]] = {}
    for profile in left["profiles"]:
        a = _vectors(left, profile, 0)
        b = _vectors(right, profile, 0)
        by_profile[profile] = {
            "bestmove_equivalent": a["bestmoves"] == b["bestmoves"],
            "native_work_equivalent": (
                a["native_work_values"] == b["native_work_values"]
            ),
        }
    return {
        "all_profile_first_repeat_bestmove_equivalent": all(
            item["bestmove_equivalent"] for item in by_profile.values()
        ),
        "all_profile_first_repeat_native_work_equivalent": all(
            item["native_work_equivalent"] for item in by_profile.values()
        ),
        "baseline": by_profile[left["baseline"]],
        "selected": by_profile[left["selected"]],
        "by_profile": by_profile,
    }


def classify_diagnosis(
    *,
    within_unstable: bool,
    b_c_bytes_equal: bool,
    a_b_behavior_equal: bool,
    b_c_behavior_equal: bool,
) -> str:
    if within_unstable:
        return "WITHIN_BINARY_INSTABILITY"
    if not b_c_bytes_equal:
        if b_c_behavior_equal:
            return "BUILD_BYTE_NONREPRODUCIBLE_BEHAVIOR_STABLE"
        return "BUILD_NONREPRODUCIBLE"
    if not a_b_behavior_equal and b_c_behavior_equal:
        return "ARTIFACT_CORRELATED_DRIFT"
    if a_b_behavior_equal and b_c_behavior_equal:
        return "BEHAVIORALLY_EQUIVALENT"
    return "MULTIPLE_EFFECTS"


def analyze_matrix_documents(
    a: dict[str, Any],
    b: dict[str, Any],
    c: dict[str, Any],
    *,
    binary_hashes: dict[str, str],
) -> dict[str, Any]:
    ia = index_matrix(a)
    ib = index_matrix(b)
    ic = index_matrix(c)
    require(ia["profiles"] == ib["profiles"] == ic["profiles"], "A/B/C profile set mismatch")
    require(ia["case_order"] == ib["case_order"] == ic["case_order"], "A/B/C case order mismatch")

    repeatability = {
        "a": {
            "baseline": _repeatability(ia, ia["baseline"]),
            "selected": _repeatability(ia, ia["selected"]),
        },
        "b": {
            "baseline": _repeatability(ib, ib["baseline"]),
            "selected": _repeatability(ib, ib["selected"]),
        },
        "c": {
            "baseline": _repeatability(ic, ic["baseline"]),
            "selected": _repeatability(ic, ic["selected"]),
        },
    }
    cross_ab = _cross(ia, ib)
    cross_bc = _cross(ib, ic)

    selected_vs_baseline: dict[str, Any] = {}
    for label, index in (("a", ia), ("b", ib), ("c", ic)):
        baseline = _vectors(index, index["baseline"], 0)
        selected = _vectors(index, index["selected"], 0)
        selected_vs_baseline[label] = {
            "baseline_bestmoves": baseline["bestmoves"],
            "selected_bestmoves": selected["bestmoves"],
            "bestmove_equivalent": baseline["bestmoves"] == selected["bestmoves"],
            "baseline_native_work": baseline["native_work_values"],
            "selected_native_work": selected["native_work_values"],
            "native_work_equivalent": (
                baseline["native_work_values"]
                == selected["native_work_values"]
            ),
        }

    within_unstable = any(
        not profile_result[field]
        for build in repeatability.values()
        for profile_result in build.values()
        for field in ("bestmove_stable", "native_work_stable")
    )
    a_b_behavior = cross_ab["all_profile_first_repeat_bestmove_equivalent"]
    b_c_behavior = cross_bc["all_profile_first_repeat_bestmove_equivalent"]
    b_c_bytes_equal = binary_hashes["b"] == binary_hashes["c"]

    return {
        "within_repeatability": repeatability,
        "cross_build": {"a_vs_b": cross_ab, "b_vs_c": cross_bc},
        "selected_vs_baseline": selected_vs_baseline,
        "diagnosis": classify_diagnosis(
            within_unstable=within_unstable,
            b_c_bytes_equal=b_c_bytes_equal,
            a_b_behavior_equal=a_b_behavior,
            b_c_behavior_equal=b_c_behavior,
        ),
        "signals": {
            "within_binary_instability": within_unstable,
            "a_vs_b_bestmove_drift": not a_b_behavior,
            "b_vs_c_bestmove_drift": not b_c_behavior,
            "a_vs_b_native_work_drift": not cross_ab[
                "all_profile_first_repeat_native_work_equivalent"
            ],
            "b_vs_c_native_work_drift": not cross_bc[
                "all_profile_first_repeat_native_work_equivalent"
            ],
            "b_vs_c_binary_bytes_equal": b_c_bytes_equal,
        },
    }


def validate_host_report(raw: dict[str, Any]) -> dict[str, Any]:
    host_raw = raw.get("host_capabilities")
    runtime_raw = raw.get("runtime_substrate")
    require(isinstance(host_raw, dict), "host_capabilities missing")
    require(isinstance(runtime_raw, dict), "runtime_substrate missing")

    try:
        host = HostCapabilities.from_dict(host_raw)
        runtime = RuntimeSubstrate.from_dict(runtime_raw)
    except Exception as exc:
        raise DiagnosticError(f"host/substrate reconstruction failed: {exc}") from exc

    # This PR is a same-worker diagnostic, not a reusable-host qualification.
    # A hosted runner may expose enough exact capability evidence to identify the
    # concrete worker while still leaving the reusable qualification domain
    # incomplete (for example when ancestor cgroup limit files are unavailable).
    # Preserve that incompleteness as evidence instead of fabricating a domain or
    # blocking the artifact-vs-domain experiment.  PR #49 remains responsible
    # for repairing reusable execution-domain qualification.
    require(runtime.complete, "runtime substrate incomplete")

    require(
        raw.get("host_capability_id") == host.capability_id,
        "host capability id does not independently reconstruct",
    )
    require(
        raw.get("host_qualification_domain_id") == host.qualification_domain_id,
        "host domain id does not independently reconstruct",
    )
    require(
        raw.get("host_qualification_domain_digest")
        == host.qualification_domain_digest,
        "host domain digest does not independently reconstruct",
    )
    require(
        raw.get("runtime_substrate_id") == runtime.substrate_id,
        "runtime substrate id does not independently reconstruct",
    )
    require(
        raw.get("runtime_substrate_digest") == runtime.digest,
        "runtime substrate digest does not independently reconstruct",
    )

    if host.qualification_domain_complete:
        require(
            host.qualification_domain_id is not None,
            "complete host qualification domain lacks id",
        )
        require(
            host.qualification_domain_digest is not None,
            "complete host qualification domain lacks digest",
        )
        require(
            _HOST_DOMAIN.fullmatch(host.qualification_domain_id) is not None,
            "host domain id shape invalid",
        )
        require(
            _HEX64.fullmatch(host.qualification_domain_digest) is not None,
            "host domain digest shape invalid",
        )
    else:
        require(
            host.qualification_domain_id is None
            and host.qualification_domain_digest is None,
            "incomplete host domain may not carry a fabricated identity",
        )

    require(
        _RUNTIME_ID.fullmatch(runtime.substrate_id) is not None,
        "runtime substrate id shape invalid",
    )
    require(
        _HEX64.fullmatch(runtime.digest) is not None,
        "runtime substrate digest shape invalid",
    )

    measurement = raw.get("resource_measurement")
    require(isinstance(measurement, dict), "resource measurement missing")
    ticks = measurement.get("clock_ticks_per_second")
    require(type(ticks) is int and ticks > 0, "SC_CLK_TCK missing/invalid")

    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "host_capability_id": host.capability_id,
        "host_capacity_complete": host.capacity_complete,
        "host_qualification_domain_complete": host.qualification_domain_complete,
        "host_qualification_domain_id": host.qualification_domain_id,
        "host_qualification_domain_digest": host.qualification_domain_digest,
        "host_faults": list(host.faults),
        "runtime_substrate_complete": runtime.complete,
        "runtime_substrate_id": runtime.substrate_id,
        "runtime_substrate_digest": runtime.digest,
        "clock_ticks_per_second": ticks,
        "host_report_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _artifact_record(path: Path, identity: dict[str, str]) -> dict[str, Any]:
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        **identity,
    }


def command_prove_surface(args: argparse.Namespace) -> int:
    proof = build_surface_proof(
        args.source_a,
        args.source_b,
        expected_a=args.expected_a,
        expected_b=args.expected_b,
    )
    write_sealed_json(args.output, proof)
    print(
        json.dumps(
            {
                "equivalent": proof["equivalent"],
                "a": proof["a"]["commit"],
                "b": proof["b"]["commit"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if proof["equivalent"] else 1


def write_execution_error_report(
    output: Path,
    matrices: dict[str, dict[str, Any]],
    matrix_paths: dict[str, Path],
    binary_hashes: dict[str, str],
    weights_hash: str,
    *,
    expected_a: str,
    expected_b: str,
    proof_sha: str,
) -> bool:
    """Preserve valid sealed matrix execution errors without pretending equivalence."""
    errors_by_matrix: dict[str, list[dict[str, Any]]] = {}
    for key, matrix in matrices.items():
        issues = matrix.get("errors")
        require(isinstance(issues, list), f"matrix {key}: errors must be a list")
        if issues:
            require(all(isinstance(issue, dict) for issue in issues),
                    f"matrix {key}: error record is malformed")
            errors_by_matrix[key] = issues
    if not errors_by_matrix:
        return False

    identities: dict[str, Any] = {}
    for key, matrix in matrices.items():
        expected_source = expected_a if key == "a" else expected_b
        require(matrix.get("schema_version") == 1
                and matrix.get("kind") == "lc0-cpu-runtime-matrix",
                f"matrix {key}: schema drift on negative path")
        require((matrix.get("source") or {}).get("commit") == expected_source,
                f"matrix {key}: source identity drift on negative path")
        require((matrix.get("binary") or {}).get("sha256") == binary_hashes[key],
                f"matrix {key}: binary identity drift on negative path")
        require((matrix.get("weights") or {}).get("sha256") == weights_hash,
                f"matrix {key}: network identity drift on negative path")
        require(matrix.get("nodes") == 16
                and float(matrix.get("deadline_ms")) == 3500.0,
                f"matrix {key}: frozen workload drift on negative path")
        identities[key] = {
            "source_commit": expected_source,
            "binary_sha256": binary_hashes[key],
            "matrix_file_sha256": sha256_file(matrix_paths[key]),
            "matrix_content_sha256": matrix["content_sha256"],
            "matrix_rows_recorded": len(matrix.get("rows") or []),
            "errors": matrix.get("errors"),
        }

    write_sealed_json(output, {
        "schema_version": 1,
        "kind": "lc0-artifact-domain-negative-diagnostic",
        "diagnostic_complete": False,
        "passed": False,
        "qualification_disposition": "RETAINED_NEGATIVE_EXECUTION_EVIDENCE",
        "reason": "one or more historical LC0 matrices contain execution errors",
        "source_equivalence_proof_sha256": proof_sha,
        "network_sha256": weights_hash,
        "matrices": identities,
        "error_matrices": sorted(errors_by_matrix),
        "claim_boundary": {
            "diagnostic_only": True, "profile_promotion": False,
            "strength": False, "elo": False, "equal_compute": False,
            "generic_host_portability": False,
        },
    })
    return True


def command_compare(args: argparse.Namespace) -> int:
    proof = load_sealed_json(args.surface_proof, label="surface proof")
    validate_surface_proof(
        proof,
        expected_a=args.expected_a,
        expected_b=args.expected_b,
    )

    host_raw = load_json(args.host_report, label="host report")
    environment = validate_host_report(host_raw)

    matrices = {
        "a": load_sealed_json(args.matrix_a, label="matrix A"),
        "b": load_sealed_json(args.matrix_b, label="matrix B"),
        "c": load_sealed_json(args.matrix_c, label="matrix C"),
    }
    binaries = {"a": args.binary_a, "b": args.binary_b, "c": args.binary_c}
    binary_hashes = {label: sha256_file(path) for label, path in binaries.items()}
    weights_hash = sha256_file(args.weights)

    if write_execution_error_report(
        args.output, matrices,
        {"a": args.matrix_a, "b": args.matrix_b, "c": args.matrix_c},
        binary_hashes, weights_hash,
        expected_a=args.expected_a, expected_b=args.expected_b,
        proof_sha=proof["content_sha256"],
    ):
        print("diagnostic evidence error: matrix contains execution errors", file=sys.stderr)
        return 2

    validated = {
        "a": validate_matrix_document(
            matrices["a"],
            label="matrix A",
            expected_source=args.expected_a,
            binary_sha256=binary_hashes["a"],
            weights_sha256=weights_hash,
        ),
        "b": validate_matrix_document(
            matrices["b"],
            label="matrix B",
            expected_source=args.expected_b,
            binary_sha256=binary_hashes["b"],
            weights_sha256=weights_hash,
        ),
        "c": validate_matrix_document(
            matrices["c"],
            label="matrix C",
            expected_source=args.expected_b,
            binary_sha256=binary_hashes["c"],
            weights_sha256=weights_hash,
        ),
    }

    analysis = analyze_matrix_documents(
        matrices["a"],
        matrices["b"],
        matrices["c"],
        binary_hashes=binary_hashes,
    )

    payload = {
        "schema_version": 1,
        "kind": "lc0-artifact-domain-diagnostic",
        "experiment": {
            "a": {
                "role": "exact-pr44-qualified-head",
                "source_commit": args.expected_a,
            },
            "b": {
                "role": "exact-post-j2-main",
                "source_commit": args.expected_b,
            },
            "c": {
                "role": "fresh-rebuild-of-b-same-worker",
                "source_commit": args.expected_b,
            },
            "same_worker_required": True,
            "matrix_workload_reimplemented": False,
        },
        "source_equivalence": {
            "proof_sha256": proof["content_sha256"],
            "equivalent": proof["equivalent"],
            "paths": proof["paths"],
        },
        "execution_domain": environment,
        "network": {
            "path": str(args.weights),
            "size_bytes": args.weights.stat().st_size,
            "sha256": weights_hash,
        },
        "artifacts": {
            label: _artifact_record(binaries[label], validated[label]["identity"])
            for label in ("a", "b", "c")
        },
        **analysis,
        "performance": {
            label: matrices[label].get("repeat_summaries")
            for label in ("a", "b", "c")
        },
        "claim_boundary": {
            "diagnostic_only": True,
            "profile_promotion": False,
            "strength": False,
            "elo": False,
            "equal_compute": False,
            "generic_host_portability": False,
        },
    }
    write_sealed_json(args.output, payload)
    print(
        json.dumps(
            {
                "diagnosis": payload["diagnosis"],
                "a_binary": binary_hashes["a"],
                "b_binary": binary_hashes["b"],
                "c_binary": binary_hashes["c"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    proof = sub.add_parser("prove-surface")
    proof.add_argument("--source-a", type=Path, required=True)
    proof.add_argument("--source-b", type=Path, required=True)
    proof.add_argument("--expected-a", default=EXPECTED_A)
    proof.add_argument("--expected-b", default=EXPECTED_B)
    proof.add_argument("--output", type=Path, required=True)
    proof.set_defaults(func=command_prove_surface)

    compare = sub.add_parser("compare")
    compare.add_argument("--surface-proof", type=Path, required=True)
    compare.add_argument("--host-report", type=Path, required=True)
    compare.add_argument("--matrix-a", type=Path, required=True)
    compare.add_argument("--matrix-b", type=Path, required=True)
    compare.add_argument("--matrix-c", type=Path, required=True)
    compare.add_argument("--binary-a", type=Path, required=True)
    compare.add_argument("--binary-b", type=Path, required=True)
    compare.add_argument("--binary-c", type=Path, required=True)
    compare.add_argument("--weights", type=Path, required=True)
    compare.add_argument("--expected-a", default=EXPECTED_A)
    compare.add_argument("--expected-b", default=EXPECTED_B)
    compare.add_argument("--output", type=Path, required=True)
    compare.set_defaults(func=command_compare)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except DiagnosticError as exc:
        print(f"diagnostic evidence error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
