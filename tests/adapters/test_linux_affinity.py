#!/usr/bin/env python3
"""Tests for Linux task-tree affinity observation/enforcement."""

from __future__ import annotations

import errno
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from adapters.resource.linux_affinity import (
    LinuxAffinityError,
    LinuxAffinityProvider,
)


def stat_line(pid: int, start: int) -> str:
    fields = ["S"] + ["0"] * 18 + [str(start)] + ["0"] * 4
    return f"{pid} (worker {pid}) " + " ".join(fields) + "\n"


def write_task(root: Path, pid: int, tid: int, *, process_start: int, task_start: int, children: str = "") -> None:
    proc = root / str(pid)
    task = proc / "task" / str(tid)
    task.mkdir(parents=True, exist_ok=True)
    (proc / "stat").write_text(stat_line(pid, process_start), encoding="utf-8")
    (task / "stat").write_text(stat_line(tid, task_start), encoding="utf-8")
    (task / "children").write_text(children, encoding="utf-8")


class FakeAffinity:
    def __init__(self, mapping: dict[int, set[int]]):
        self.mapping = {key: set(value) for key, value in mapping.items()}
        self.set_calls: list[tuple[int, tuple[int, ...]]] = []
        self.fail_tid: int | None = None
        self.after_first_set = None

    def get(self, tid: int):
        if tid == 0:
            return {0, 1, 2, 3}
        if tid not in self.mapping:
            raise ProcessLookupError(errno.ESRCH, "No such process")
        return set(self.mapping[tid])

    def set(self, tid: int, cpus: set[int]):
        if tid == self.fail_tid:
            raise OSError(errno.EPERM, "Operation not permitted")
        if tid not in self.mapping:
            raise ProcessLookupError(errno.ESRCH, "No such process")
        self.mapping[tid] = set(cpus)
        self.set_calls.append((tid, tuple(sorted(cpus))))
        callback = self.after_first_set
        self.after_first_set = None
        if callback is not None:
            callback()


class LinuxAffinityProviderTests(unittest.TestCase):
    def test_tree_identity_and_child_recursion_are_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_task(root, 100, 100, process_start=10, task_start=10, children="200")
            write_task(root, 100, 101, process_start=10, task_start=11)
            write_task(root, 200, 200, process_start=20, task_start=20)
            fake = FakeAffinity({100:{0,1},101:{0,1},200:{2,3}})
            provider = LinuxAffinityProvider(
                proc_root=root,
                affinity_getter=fake.get,
                affinity_setter=fake.set,
            )
            tree = provider.inspect_tree_affinity(100)
        self.assertEqual(tree.root_start_time_ticks, 10)
        self.assertEqual(
            [(row.identity.pid, row.identity.tid) for row in tree.tasks],
            [(100,100),(100,101),(200,200)],
        )
        self.assertEqual(tree.tasks[-1].cpus, (2,3))
        self.assertFalse(tree.enforced)

    def test_affinity_application_pins_every_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_task(root,100,100,process_start=10,task_start=10,children="200")
            write_task(root,100,101,process_start=10,task_start=11)
            write_task(root,200,200,process_start=20,task_start=20)
            fake=FakeAffinity({100:{0,1,2,3},101:{0,1,2,3},200:{0,1,2,3}})
            provider=LinuxAffinityProvider(
                proc_root=root,affinity_getter=fake.get,affinity_setter=fake.set
            )
            result=provider.apply_tree_affinity(100,(2,))
        self.assertTrue(result.enforced)
        self.assertEqual({row.cpus for row in result.tasks},{(2,)})
        self.assertEqual({tid for tid,_ in fake.set_calls},{100,101,200})

    def test_child_appearing_during_first_pass_is_rediscovered_and_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_task(root,100,100,process_start=10,task_start=10)
            fake=FakeAffinity({100:{0,1,2,3}})
            provider=LinuxAffinityProvider(
                proc_root=root,affinity_getter=fake.get,affinity_setter=fake.set,max_passes=4
            )
            def spawn():
                (root/"100"/"task"/"100"/"children").write_text("200",encoding="utf-8")
                write_task(root,200,200,process_start=20,task_start=20)
                fake.mapping[200]={0,1,2,3}
            fake.after_first_set=spawn
            result=provider.apply_tree_affinity(100,(1,))
        self.assertEqual(
            {(row.identity.pid,row.identity.tid,row.cpus) for row in result.tasks},
            {(100,100,(1,)),(200,200,(1,))},
        )
        self.assertGreaterEqual(result.passes,2)

    def test_setter_failure_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_task(root,100,100,process_start=10,task_start=10)
            fake=FakeAffinity({100:{0,1}})
            fake.fail_tid=100
            provider=LinuxAffinityProvider(
                proc_root=root,affinity_getter=fake.get,affinity_setter=fake.set
            )
            with self.assertRaises(LinuxAffinityError):
                provider.apply_tree_affinity(100,(0,))

    def test_malformed_children_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_task(root,100,100,process_start=10,task_start=10,children="abc")
            fake=FakeAffinity({100:{0}})
            provider=LinuxAffinityProvider(
                proc_root=root,affinity_getter=fake.get,affinity_setter=fake.set
            )
            with self.assertRaises(LinuxAffinityError):
                provider.process_tree(100)

    def test_pid_reuse_identity_change_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            write_task(root,100,100,process_start=10,task_start=10)
            fake=FakeAffinity({100:{0}})
            provider=LinuxAffinityProvider(
                proc_root=root,affinity_getter=fake.get,affinity_setter=fake.set
            )
            original=provider.process_tree(100)
            (root/"100"/"stat").write_text(stat_line(100,99),encoding="utf-8")
            replaced=provider.process_tree(100)
        self.assertNotEqual(
            original.root_start_time_ticks,
            replaced.root_start_time_ticks,
        )

    def test_host_affinity_is_validated(self):
        fake=FakeAffinity({})
        provider=LinuxAffinityProvider(
            proc_root=Path("/definitely/not/used"),
            affinity_getter=fake.get,
            affinity_setter=fake.set,
        )
        self.assertEqual(provider.host_affinity(),(0,1,2,3))


if __name__ == "__main__":
    unittest.main()
