#!/usr/bin/env python3
"""Destructive offline tests: diagnostic ordering, provenance, and no-promotion boundary."""
from __future__ import annotations

import copy
import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.reckless_repro.analysis import (
    REPEATS, NODES, PROTOCOL_ID, DiagnosticError, analyze, schedule,
)


from tools.reckless_repro.provenance import (
    BinaryMutationError, binary_identity, elf_text, binaries, preflight_binary_hashes,
)


class RealElfIntegrityTests(unittest.TestCase):
    def test_disposable_objcopy_and_readelf_leave_real_elf_unchanged(self):
        source = shutil.which("true")
        if source is None or shutil.which("objcopy") is None or shutil.which("readelf") is None:
            self.skipTest("system ELF and binutils required")
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "fixture.elf"
            shutil.copy2(source, executable)
            before = binary_identity(executable)
            section = elf_text(executable)
            self.assertTrue(section["available"], section)
            self.assertEqual(before, binary_identity(executable))
            info = binaries({"fixture": executable})["fixture"]
            self.assertTrue(info["inspection_preserved_original"])
            self.assertEqual(before, binary_identity(executable))
            self.assertEqual(info["sha256"], before["sha256"])
            self.assertEqual(info["size_bytes"], before["size_bytes"])
            self.assertEqual(info["mode"], before["mode"])

    def test_immediate_post_build_hash_manifest_rejects_mutated_elf(self):
        source = shutil.which("true")
        if source is None:
            self.skipTest("system ELF required")
        with tempfile.TemporaryDirectory() as tmp:
            paths = {}
            for label in ("derived-a", "derived-b", "pristine"):
                executable = Path(tmp) / label
                shutil.copy2(source, executable)
                paths[label] = executable
            manifest = Path(tmp) / "binaries.sha256"
            manifest.write_text("".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path}\n"
                for path in paths.values()
            ), encoding="utf-8")
            self.assertEqual(len(preflight_binary_hashes(paths, manifest)), 3)
            paths["pristine"].write_bytes(paths["pristine"].read_bytes() + b"x")
            with self.assertRaises(BinaryMutationError):
                preflight_binary_hashes(paths, manifest)


CASES = [f"case-{x}" for x in range(8)]


def rows():
    result=[]
    for slot in schedule(CASES):
        metric={
            "wall_ms": 900.0 if slot["binary"] == "pristine" else 920.0,
            "cpu_ms": 900.0,
            "native_work_value": NODES + 1,
            "completed_before_deadline": True,
            "bestmove": "e2e4",
        }
        result.append({**slot,"metrics":metric})
    return result


class ScheduleTests(unittest.TestCase):
    def test_predeclared_balanced_abba_baab_complete(self):
        plan=schedule(CASES)
        self.assertEqual(len(plan),5*2*8*4)
        for i in range(0,len(plan),4):
            group=plan[i:i+4]
            names=[x["binary"] for x in group]
            self.assertTrue(names in (
                ["derived-a","pristine","pristine","derived-a"],
                ["pristine","derived-a","derived-a","pristine"],
                ["derived-b","pristine","pristine","derived-b"],
                ["pristine","derived-b","derived-b","pristine"],
            ))
            self.assertEqual([x["order_index"] for x in group],[0,1,2,3])
        for block in range(REPEATS):
            sub=[x for x in plan if x["repeat_index"]==block]
            self.assertEqual(len(sub),64)

    def test_valid_diagnostic_comparison_does_not_authorize_promotion(self):
        report=analyze(rows(),CASES)
        self.assertTrue(report["diagnostic_complete"])
        self.assertFalse(report["promotion_evidence"])
        self.assertEqual(report["planned"],320)
        self.assertEqual(report["observed"],320)
        for data in report["comparisons"].values():
            self.assertGreater(data["mean_log_ratio_backtransformed"],1.0)
            self.assertFalse(data["frozen_gate_applied"])
            self.assertFalse(data["qualification_claim"])
            self.assertFalse(data["bestmove_mismatches"])

    def test_missing_rows_order_changes_bad_metrics_and_deadlines_rejected(self):
        baseline=rows()
        with self.assertRaises(DiagnosticError):
            analyze(baseline[:-1],CASES)
        edits=[
            ("order",lambda x:x[0].__setitem__("binary","pristine")),
            ("block",lambda x:x[1].__setitem__("repeat_index",99)),
            ("timeout",lambda x:x[2]["metrics"].__setitem__("completed_before_deadline",False)),
            ("nodes",lambda x:x[3]["metrics"].__setitem__("native_work_value",None)),
            ("cpu",lambda x:x[4]["metrics"].__setitem__("cpu_ms",float("nan"))),
            ("move",lambda x:x[5]["metrics"].__setitem__("bestmove","")),
        ]
        for name,mutation in edits:
            with self.subTest(name=name):
                data=copy.deepcopy(baseline)
                mutation(data)
                with self.assertRaises(DiagnosticError):
                    analyze(data,CASES)

    def test_search_work_and_move_drift_are_visible_not_silently_averaged(self):
        data=rows()
        data[0]["metrics"]["bestmove"]="d2d4"
        data[1]["metrics"]["native_work_value"]=NODES+2
        result=analyze(data,CASES)
        self.assertTrue(result["comparisons"]["derived-a"]["bestmove_mismatches"])
        self.assertTrue(result["comparisons"]["derived-a"]["native_work_mismatches"])
        self.assertFalse(result["promotion_evidence"])

    def test_source_files_are_not_in_frozen_engine_opt_pattern(self):
        workflow=(ROOT/".github/workflows/reckless-repro-diagnostic.yml").read_text()
        self.assertIn("workflow_dispatch:",workflow)
        self.assertIn("tools/reckless_repro/**",workflow)
        self.assertIn("tests/reckless_repro/**",workflow)
        self.assertIn("scripts/reckless-repro.py",workflow)
        self.assertIn("cancel-in-progress: false",workflow)
        self.assertIn("actions/upload-artifact@",workflow)
        engine=(ROOT/".github/workflows/engine-optimization.yml").read_text()
        for path in ("tools/reckless_repro/**","tests/reckless_repro/**","scripts/reckless-repro.py"):
            self.assertNotIn(path,engine)
        cli=(ROOT/"scripts/reckless-repro.py").read_text()
        self.assertIn('"promotable": False',cli)
        self.assertIn('"promotion_evidence": False',cli)
        self.assertIn('lock["engines"]["reckless"]["artifacts"]["default_nnue"]["sha256"]',cli)


if __name__ == "__main__":
    unittest.main()
