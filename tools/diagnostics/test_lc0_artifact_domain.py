#!/usr/bin/env python3
"""Synthetic contract tests for the LC0 artifact/domain comparator."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from controller.decision import canonical_digest
from controller.host_capabilities import (
    HOST_CAPABILITIES_VERSION,
    HostCapabilities,
    NumaNodeObservation,
)
from controller.runtime_substrate import RuntimeSubstrate

from tools.diagnostics.compare_lc0_artifact_domain import (
    DiagnosticError,
    EXPECTED_A,
    EXPECTED_B,
    SURFACE_PATHS,
    _core_digest,
    analyze_matrix_documents,
    classify_diagnosis,
    extract_lc0_identity,
    index_matrix,
    load_sealed_json,
    validate_host_report,
    validate_surface_proof,
    write_sealed_json,
)


PROFILES = [
    "v1-current-cold",
    "b7-p8-c256k-warm64",
    "other-profile",
]
CASES = [f"case-{idx}" for idx in range(8)]


def incomplete_but_valid_host_report() -> dict:
    flags = ("avx", "avx2", "fpu", "sse", "sse2")
    host = HostCapabilities(
        version=HOST_CAPABILITIES_VERSION,
        provider_id="linux-host-v2",
        platform="linux",
        architecture="x86_64",
        os_visible_logical_cpus=4,
        affinity_cpus=(0, 1, 2, 3),
        cgroup_cpuset_effective=(0, 1, 2, 3),
        allowed_cpus=(0, 1, 2, 3),
        cpu_vendor_id="AuthenticAMD",
        cpu_family=25,
        cpu_model=1,
        cpu_stepping=1,
        cpu_model_name="AMD test",
        cpu_microcode="0xffffffff",
        cpu_flags_intersection=flags,
        cpu_feature_digest=canonical_digest(list(flags)),
        cpu_identity_complete=True,
        cpu_quota_status="unknown",
        cpu_quota_equivalents=None,
        cpu_quota_observations=(),
        physical_core_count=2,
        smt_width=2,
        topology_complete=True,
        numa_nodes=(NumaNodeObservation(0, (0, 1, 2, 3)),),
        numa_complete=True,
        physical_memory_bytes=16 * 1024**3,
        cgroup_memory_status="unknown",
        cgroup_memory_limit_bytes=None,
        effective_memory_limit_bytes=16 * 1024**3,
        memory_limit_observations=(),
        accelerator_detection_complete=False,
        accelerators=(),
        capacity_complete=False,
        qualification_domain_complete=False,
        faults=("cpu.max:root:OSError:2", "memory.max:root:OSError:2"),
    )
    runtime = RuntimeSubstrate.from_observation(
        os_id="ubuntu",
        os_version_id="24.04",
        kernel_release="6.17.0-test",
        architecture="x86_64",
        libc_name="glibc",
        libc_version="2.39",
        python_version="3.12.3",
        runner_image_os="ubuntu24",
        runner_image_version="20260920.314.1",
        openblas_package="libopenblas-dev=0.3.26",
        clock_ticks_per_second=100,
    )
    return {
        "host_capabilities": host.as_dict(),
        "host_capability_id": host.capability_id,
        "host_qualification_domain_id": host.qualification_domain_id,
        "host_qualification_domain_digest": host.qualification_domain_digest,
        "runtime_substrate": runtime.as_dict(),
        "runtime_substrate_id": runtime.substrate_id,
        "runtime_substrate_digest": runtime.digest,
        "resource_measurement": {"clock_ticks_per_second": 100},
    }


def matrix(
    *,
    source: str,
    binary_hash: str,
    weights_hash: str = "f" * 64,
    bestmove_overrides: dict[tuple[str, int, str], str] | None = None,
    native_overrides: dict[tuple[str, int, str], int] | None = None,
    build_id: str | None = None,
) -> dict:
    bestmove_overrides = bestmove_overrides or {}
    native_overrides = native_overrides or {}
    short = build_id if build_id is not None else source[:7]
    rows = []
    repeat_summaries = {}
    for profile in PROFILES:
        repeat_count = 3 if profile in set(PROFILES[:2]) else 1
        summaries = []
        for repeat in range(repeat_count):
            bestmoves = []
            native = []
            for pos, case_id in enumerate(CASES):
                key = (profile, repeat, case_id)
                move = bestmove_overrides.get(
                    key,
                    ["e2e4", "d2d4", "g1f3", "c2c4"][pos % 4],
                )
                work = native_overrides.get(key, 16 + pos)
                bestmoves.append(move)
                native.append(work)
                rows.append(
                    {
                        "profile": profile,
                        "repeat_index": repeat,
                        "case_id": case_id,
                        "metrics": {
                            "bestmove": move,
                            "native_work_value": work,
                            "native_work_semantics": "lc0.uci_nodes",
                            "wall_ms": 1.0 + pos,
                            "cpu_ms": 1.0 + pos,
                            "rss_kib_end": 100.0,
                            "completed_before_deadline": True,
                        },
                        "transcript": [
                            ">> uci",
                            f"<< id name Lc0 v0.31.0+git.{short}",
                            "<< uciok",
                        ],
                    }
                )
            summaries.append(
                {
                    "cases": 8,
                    "bestmoves": bestmoves,
                    "native_work_values": native,
                    "median_wall_ms": 4.5,
                    "max_wall_ms": 8.0,
                    "median_cpu_ms": 4.5,
                    "max_cpu_ms": 8.0,
                }
            )
        repeat_summaries[profile] = summaries
    return {
        "schema_version": 1,
        "kind": "lc0-cpu-runtime-matrix",
        "source": {"commit": source, "tree": "1" * 40},
        "host": {},
        "binary": {"path": "lc0", "sha256": binary_hash},
        "weights": {"path": "791556.pb.gz", "sha256": weights_hash},
        "nodes": 16,
        "deadline_ms": 3500.0,
        "profiles": list(PROFILES),
        "rows": rows,
        "summaries": {},
        "repeat_summaries": repeat_summaries,
        "confirmation": {
            "selected_profile": PROFILES[1],
            "baseline_profile": PROFILES[0],
            "repeats": 3,
        },
        "errors": [],
    }


class ComparatorTests(unittest.TestCase):
    def test_behaviorally_equivalent(self):
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        c = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "b" * 64}
        )
        self.assertEqual(result["diagnosis"], "BEHAVIORALLY_EQUIVALENT")
        self.assertFalse(result["signals"]["a_vs_b_bestmove_drift"])
        self.assertTrue(result["signals"]["b_vs_c_binary_bytes_equal"])

    def test_artifact_correlated_drift(self):
        change = {
            (PROFILES[1], repeat, CASES[0]): "h2h3"
            for repeat in range(3)
        }
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64, bestmove_overrides=change)
        c = matrix(source=EXPECTED_B, binary_hash="b" * 64, bestmove_overrides=change)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "b" * 64}
        )
        self.assertEqual(result["diagnosis"], "ARTIFACT_CORRELATED_DRIFT")
        self.assertFalse(result["cross_build"]["a_vs_b"]["selected"]["bestmove_equivalent"])
        self.assertTrue(result["cross_build"]["b_vs_c"]["selected"]["bestmove_equivalent"])

    def test_build_bytes_can_differ_without_behavior_drift(self):
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        c = matrix(source=EXPECTED_B, binary_hash="c" * 64)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "c" * 64}
        )
        self.assertEqual(
            result["diagnosis"],
            "BUILD_BYTE_NONREPRODUCIBLE_BEHAVIOR_STABLE",
        )

    def test_build_nonreproducible_behavior(self):
        change = {(PROFILES[2], 0, CASES[1]): "a2a3"}
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        c = matrix(source=EXPECTED_B, binary_hash="c" * 64, bestmove_overrides=change)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "c" * 64}
        )
        self.assertEqual(result["diagnosis"], "BUILD_NONREPRODUCIBLE")
        self.assertTrue(result["signals"]["b_vs_c_bestmove_drift"])

    def test_within_binary_instability_wins_classification(self):
        change = {(PROFILES[1], 1, CASES[0]): "h2h3"}
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64, bestmove_overrides=change)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        c = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "b" * 64}
        )
        self.assertEqual(result["diagnosis"], "WITHIN_BINARY_INSTABILITY")
        self.assertFalse(
            result["within_repeatability"]["a"]["selected"]["bestmove_stable"]
        )

    def test_native_work_drift_is_separate_from_bestmove_drift(self):
        native = {
            (PROFILES[1], repeat, CASES[0]): 99
            for repeat in range(3)
        }
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64, native_overrides=native)
        c = matrix(source=EXPECTED_B, binary_hash="b" * 64, native_overrides=native)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "b" * 64}
        )
        self.assertFalse(result["signals"]["a_vs_b_bestmove_drift"])
        self.assertTrue(result["signals"]["a_vs_b_native_work_drift"])

    def test_selected_and_baseline_are_reported_separately(self):
        change = {
            (PROFILES[1], repeat, CASES[0]): "h2h3"
            for repeat in range(3)
        }
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64, bestmove_overrides=change)
        c = matrix(source=EXPECTED_B, binary_hash="b" * 64, bestmove_overrides=change)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "b" * 64}
        )
        self.assertTrue(result["cross_build"]["a_vs_b"]["baseline"]["bestmove_equivalent"])
        self.assertFalse(result["cross_build"]["a_vs_b"]["selected"]["bestmove_equivalent"])

    def test_baseline_drift_is_visible(self):
        change = {
            (PROFILES[0], repeat, CASES[0]): "h2h3"
            for repeat in range(3)
        }
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64, bestmove_overrides=change)
        c = matrix(source=EXPECTED_B, binary_hash="b" * 64, bestmove_overrides=change)
        result = analyze_matrix_documents(
            a, b, c, binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "b" * 64}
        )
        self.assertFalse(result["cross_build"]["a_vs_b"]["baseline"]["bestmove_equivalent"])

    def test_missing_repeat_fails_closed(self):
        doc = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        doc["rows"] = [
            row
            for row in doc["rows"]
            if not (row["profile"] == PROFILES[1] and row["repeat_index"] == 2)
        ]
        with self.assertRaises(DiagnosticError):
            index_matrix(doc)

    def test_missing_case_fails_closed(self):
        doc = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        doc["rows"] = [
            row
            for row in doc["rows"]
            if not (
                row["profile"] == PROFILES[0]
                and row["repeat_index"] == 1
                and row["case_id"] == CASES[-1]
            )
        ]
        with self.assertRaises(DiagnosticError):
            index_matrix(doc)

    def test_case_order_mismatch_fails_closed(self):
        doc = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        indexes = [
            idx
            for idx, row in enumerate(doc["rows"])
            if row["profile"] == PROFILES[1] and row["repeat_index"] == 1
        ]
        left, right = indexes[0], indexes[1]
        doc["rows"][left], doc["rows"][right] = doc["rows"][right], doc["rows"][left]
        with self.assertRaises(DiagnosticError):
            index_matrix(doc)

    def test_profile_set_mismatch_fails_closed(self):
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        c = matrix(source=EXPECTED_B, binary_hash="b" * 64)
        c["profiles"][-1] = "different"
        with self.assertRaises(DiagnosticError):
            analyze_matrix_documents(
                a, b, c,
                binary_hashes={"a": "a" * 64, "b": "b" * 64, "c": "b" * 64},
            )

    def test_network_mismatch_is_visible_to_caller_contract(self):
        a = matrix(source=EXPECTED_A, binary_hash="a" * 64, weights_hash="a" * 64)
        b = matrix(source=EXPECTED_B, binary_hash="b" * 64, weights_hash="b" * 64)
        self.assertNotEqual(a["weights"]["sha256"], b["weights"]["sha256"])

    def test_surface_mismatch_fails_closed(self):
        proof = {
            "kind": "lc0-artifact-domain-source-equivalence",
            "a": {"commit": EXPECTED_A},
            "b": {"commit": EXPECTED_B},
            "paths": [
                {
                    "path": path,
                    "a_object": "1" * 40,
                    "b_object": "1" * 40,
                    "equal": True,
                }
                for path in SURFACE_PATHS
            ],
            "equivalent": True,
        }
        proof["paths"][0]["b_object"] = "2" * 40
        proof["paths"][0]["equal"] = False
        proof["equivalent"] = False
        with self.assertRaises(DiagnosticError):
            validate_surface_proof(proof)

    def test_tampered_sealed_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sealed.json"
            write_sealed_json(path, {"schema_version": 1, "value": 1})
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["value"] = 2
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(DiagnosticError):
                load_sealed_json(path, label="tampered")

    def test_write_seal_matches_independent_digest(self):
        payload = {"schema_version": 1, "value": ["x", 2]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sealed.json"
            write_sealed_json(path, payload)
            raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["content_sha256"], _core_digest(raw))

    def test_malformed_host_report_fails_closed(self):
        with self.assertRaises(DiagnosticError):
            validate_host_report(
                {
                    "host_capabilities": {},
                    "runtime_substrate": {},
                    "resource_measurement": {"clock_ticks_per_second": 100},
                }
            )

    def test_valid_incomplete_domain_is_preserved_not_fabricated(self):
        report = incomplete_but_valid_host_report()
        result = validate_host_report(report)
        self.assertFalse(result["host_capacity_complete"])
        self.assertFalse(result["host_qualification_domain_complete"])
        self.assertIsNone(result["host_qualification_domain_id"])
        self.assertIsNone(result["host_qualification_domain_digest"])
        self.assertTrue(result["runtime_substrate_complete"])
        self.assertIn("cpu.max:root:OSError:2", result["host_faults"])

    def test_actual_lc0_git_build_identifier_is_accepted(self):
        doc = matrix(source=EXPECTED_A, binary_hash="a" * 64)
        identity = extract_lc0_identity(doc, expected_commit=EXPECTED_A)
        self.assertEqual(identity["git_build_id"], EXPECTED_A[:7])
        self.assertEqual(
            identity["uci_name"],
            f"Lc0 v0.31.0+git.{EXPECTED_A[:7]}",
        )

    def test_malformed_lc0_build_identifier_fails_closed(self):
        doc = matrix(
            source=EXPECTED_A,
            binary_hash="a" * 64,
            build_id="not-a-git-id",
        )
        with self.assertRaises(DiagnosticError):
            extract_lc0_identity(doc, expected_commit=EXPECTED_A)

    def test_classifier_table(self):
        self.assertEqual(
            classify_diagnosis(
                within_unstable=False,
                b_c_bytes_equal=True,
                a_b_behavior_equal=True,
                b_c_behavior_equal=True,
            ),
            "BEHAVIORALLY_EQUIVALENT",
        )
        self.assertEqual(
            classify_diagnosis(
                within_unstable=False,
                b_c_bytes_equal=True,
                a_b_behavior_equal=False,
                b_c_behavior_equal=True,
            ),
            "ARTIFACT_CORRELATED_DRIFT",
        )



class NegativeDiagnosticReportTests(unittest.TestCase):
    def test_historical_missing_bestmove_is_sealed_and_remains_negative(self):
        from tools.diagnostics.compare_lc0_artifact_domain import (
            write_execution_error_report, sha256_file,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = {
                "a": matrix(source=EXPECTED_A, binary_hash="a" * 64),
                "b": matrix(source=EXPECTED_B, binary_hash="b" * 64),
                "c": matrix(source=EXPECTED_B, binary_hash="c" * 64),
            }
            failed = {
                "profile": PROFILES[0], "case_id": CASES[-1], "repeat_index": 0,
                "error": "UCI response wait expired without bestmove",
            }
            inputs["c"]["errors"] = [failed]
            paths = {}
            for name, doc in inputs.items():
                path = root / f"matrix-{name}.json"
                write_sealed_json(path, doc)
                paths[name] = path
                inputs[name] = load_sealed_json(path, label=name)
            output = root / "negative.json"
            changed = write_execution_error_report(
                output, inputs, paths,
                {"a": "a" * 64, "b": "b" * 64, "c": "c" * 64},
                inputs["a"]["weights"]["sha256"],
                expected_a=EXPECTED_A, expected_b=EXPECTED_B,
                proof_sha="d" * 64,
            )
            self.assertTrue(changed)
            report = load_sealed_json(output, label="negative")
            self.assertFalse(report["passed"])
            self.assertFalse(report["diagnostic_complete"])
            self.assertFalse(report["claim_boundary"]["profile_promotion"])
            self.assertEqual(report["matrices"]["c"]["errors"], [failed])
            self.assertEqual(report["matrices"]["c"]["matrix_file_sha256"],
                             sha256_file(paths["c"]))
            self.assertEqual(report["error_matrices"], ["c"])

    def test_clean_matrices_do_not_produce_negative_report(self):
        from tools.diagnostics.compare_lc0_artifact_domain import write_execution_error_report
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = {
                "a": matrix(source=EXPECTED_A, binary_hash="a" * 64),
                "b": matrix(source=EXPECTED_B, binary_hash="b" * 64),
                "c": matrix(source=EXPECTED_B, binary_hash="c" * 64),
            }
            paths = {name: root / f"{name}.json" for name in inputs}
            output = root / "negative.json"
            self.assertFalse(write_execution_error_report(
                output, inputs, paths,
                {"a": "a" * 64, "b": "b" * 64, "c": "c" * 64},
                "f" * 64,
                expected_a=EXPECTED_A, expected_b=EXPECTED_B,
                proof_sha="d" * 64,
            ))
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
