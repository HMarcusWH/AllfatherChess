"""S1 resource accounting: source/negative tests (not real host qualification)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from unittest import TestCase, mock

from tools.local_game.common import ROOT, load
from tools.strength_lab.participant_scope import (
    ParticipantCgroup, ResourceScopeError, command_for_proxy,
    membership_parent, read_cgroup_counters, receipt_location,
)


def fake_cgroup(path: Path, *, cpu: int = 300, peak: int = 10_000,
                current: int = 0, populated: int = 0) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "cpu.stat").write_text(
        f"usage_usec {cpu}\nuser_usec 2\nsystem_usec 3\n", encoding="ascii")
    (path / "cgroup.events").write_text(
        f"populated {populated}\nfrozen 0\n", encoding="ascii")
    (path / "memory.current").write_text(f"{current}\n", encoding="ascii")
    (path / "memory.peak").write_text(f"{peak}\n", encoding="ascii")
    (path / "cgroup.procs").write_text("", encoding="ascii")
    (path / "cgroup.kill").write_text("", encoding="ascii")


class ParticipantAccountingTests(TestCase):
    def test_exact_kernel_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "participant"
            fake_cgroup(path, cpu=98111, peak=23433, current=1234)
            self.assertEqual(read_cgroup_counters(path), {
                "cpu_usage_usec": 98111,
                "memory_peak_bytes": 23433,
                "memory_current_bytes": 1234,
                "populated": 0,
            })

    def test_bad_cgroup_counters_fail_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "participant"
            fake_cgroup(path)
            (path / "cpu.stat").write_text("usage_usec 20\nusage_usec 21\n")
            with self.assertRaises(ResourceScopeError):
                read_cgroup_counters(path)
            (path / "cpu.stat").write_text("user_usec 20\n")
            with self.assertRaises(ResourceScopeError):
                read_cgroup_counters(path)
            (path / "cpu.stat").write_text("usage_usec -2\n")
            with self.assertRaises(ResourceScopeError):
                read_cgroup_counters(path)
            (path / "cpu.stat").write_text("usage_usec 2\n")
            (path / "memory.peak").unlink()
            with self.assertRaises(ResourceScopeError):
                read_cgroup_counters(path)
            (path / "memory.peak").symlink_to(path / "memory.current")
            with self.assertRaises(ResourceScopeError):
                read_cgroup_counters(path)
            (path / "memory.peak").unlink()
            (path / "memory.peak").write_text("800\n")
            (path / "cgroup.events").write_text("populated 2\n")
            with self.assertRaises(ResourceScopeError):
                read_cgroup_counters(path)

    def test_no_hybrid_or_path_traversal_membership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cgroup.controllers").write_text("cpu memory\n")
            (root / "workers").mkdir()
            self.assertEqual(membership_parent(root, "0::/workers\n"), root / "workers")
            for bad in ("", "2:cpu:/workers\n0::/workers\n",
                        "0::/../workers\n", "0:://workers\n",
                        "0::/workers/./bad\n", "0::workers\n",
                        "0::/workers\n1:memory:/workers\n"):
                with self.subTest(bad=bad):
                    with self.assertRaises(ResourceScopeError):
                        membership_parent(root, bad)

    def test_reject_symlinked_membership(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cgroup.controllers").write_text("cpu memory")
            (root / "real").mkdir()
            (root / "alias").symlink_to(root / "real", target_is_directory=True)
            with self.assertRaises(ResourceScopeError):
                membership_parent(root, "0::/alias\n")

    def test_fresh_scope_detects_cpu_reversal_and_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "participant"
            fake_cgroup(path, cpu=0)
            scope = ParticipantCgroup(path)
            (path / "cpu.stat").write_text("usage_usec 1444\n")
            self.assertEqual(scope.counters()["cpu_usage_usec"], 1444)
            (path / "cgroup.events").write_text("populated 1\n")
            with self.assertRaises(ResourceScopeError):
                scope.remove()
            (path / "cgroup.events").write_text("populated 0\n")
            (path / "cpu.stat").write_text("usage_usec 0\n")
            self.assertEqual(scope.counters()["cpu_usage_usec"], 0)
            with mock.patch.object(Path, "rmdir", return_value=None) as remove:
                scope.remove()
                remove.assert_called_once_with()

    def test_new_scope_rejects_prior_cpu_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "participant"
            fake_cgroup(path, cpu=1)
            with self.assertRaises(ResourceScopeError):
                ParticipantCgroup(path)

    def test_scope_inode_replacement_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "participant"
            fake_cgroup(path, cpu=0)
            scope = ParticipantCgroup(path)
            path.rename(path.with_name("previous"))
            fake_cgroup(path, cpu=0)
            with self.assertRaises(ResourceScopeError):
                scope.counters()

    def test_unwritable_control_fails_before_engine_launch(self):
        from tools.strength_lab.participant_scope import _open_control_write
        with tempfile.TemporaryDirectory() as tmp:
            control = Path(tmp) / "cgroup.procs"
            control.write_text("", encoding="ascii")
            with mock.patch(
                "tools.strength_lab.participant_scope.os.open",
                side_effect=PermissionError("delegation denied"),
            ):
                with self.assertRaises(ResourceScopeError):
                    _open_control_write(control)
            control.unlink()
            control.symlink_to(Path(tmp) / "elsewhere")
            with self.assertRaises(ResourceScopeError):
                _open_control_write(control)

    def test_proxy_command_is_exact_unmodified_module(self):
        self.assertEqual(
            command_for_proxy(Path("/tmp/exact-spec.json")),
            [os.sys.executable, "-m", "tools.local_game.proxy",
             "--spec", "/tmp/exact-spec.json"],
        )

    def test_receipt_root_and_arm_are_restricted(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as tmp:
            directory = Path(tmp)
            session_root = directory / "sessions" / "allfather-g3"
            spec = {"root": str(ROOT), "sessions": str(session_root),
                    "arm": "allfather-g3"}
            location = receipt_location(spec, "receipt1")
            self.assertEqual(location, directory / "participant_resources"
                             / "allfather-g3" / "receipt1.json")
            for bad_arm in ("../somewhere", "/tmp", ".", ""):
                with self.subTest(arm=bad_arm):
                    with self.assertRaises(ResourceScopeError):
                        receipt_location({**spec, "arm": bad_arm}, "r")
            with self.assertRaises(ResourceScopeError):
                receipt_location({**spec, "sessions": "/tmp/sessions/z"}, "r")

    def test_s1_source_policy_remains_nonqualifying(self):
        policy = load(ROOT / "qualification/strength/g3-output-control-v1.json")
        self.assertFalse(policy["whole_participant_resource_qualified"])
        self.assertFalse(policy["strength_qualified"])
        self.assertFalse(policy["same_compute_claim"])


if __name__ == "__main__":
    import unittest
    unittest.main()
