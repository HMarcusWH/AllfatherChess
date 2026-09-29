"""Linux process-tree CPU affinity inspection and enforcement for M14-J J5.

J2 host discovery is intentionally read-only.  This adapter owns the separate
J5 mutation surface: task-tree discovery plus sched_getaffinity/setaffinity.
It never chooses an Allfather profile and carries no resource or move authority.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


class LinuxAffinityError(RuntimeError):
    """Raised when Linux placement cannot be observed or enforced honestly."""


def _cpu_tuple(value: Iterable[int], label: str) -> tuple[int, ...]:
    try:
        cpus = tuple(sorted(value))
    except TypeError as exc:
        raise LinuxAffinityError(f"{label}: CPU set is not iterable") from exc
    if (
        not cpus
        or any(isinstance(cpu, bool) or not isinstance(cpu, int) or cpu < 0 for cpu in cpus)
        or len(cpus) != len(set(cpus))
    ):
        raise LinuxAffinityError(
            f"{label}: CPUs must be unique non-negative integers"
        )
    return cpus


@dataclass(frozen=True)
class TaskIdentity:
    pid: int
    process_start_time_ticks: int
    tid: int
    task_start_time_ticks: int

    def as_dict(self) -> dict[str, int]:
        return {
            "pid": self.pid,
            "process_start_time_ticks": self.process_start_time_ticks,
            "tid": self.tid,
            "task_start_time_ticks": self.task_start_time_ticks,
        }


@dataclass(frozen=True)
class ProcessTree:
    root_pid: int
    root_start_time_ticks: int
    tasks: tuple[TaskIdentity, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "root_pid": self.root_pid,
            "root_start_time_ticks": self.root_start_time_ticks,
            "tasks": [task.as_dict() for task in self.tasks],
        }


@dataclass(frozen=True)
class TaskAffinityObservation:
    identity: TaskIdentity
    cpus: tuple[int, ...]

    def as_dict(self) -> dict[str, object]:
        return {**self.identity.as_dict(), "cpus": list(self.cpus)}


@dataclass(frozen=True)
class ProcessTreeAffinity:
    root_pid: int
    root_start_time_ticks: int
    tasks: tuple[TaskAffinityObservation, ...]
    passes: int
    enforced: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "root_pid": self.root_pid,
            "root_start_time_ticks": self.root_start_time_ticks,
            "passes": self.passes,
            "enforced": self.enforced,
            "tasks": [task.as_dict() for task in self.tasks],
        }


class LinuxAffinityProvider:
    """Inspect or mutate Linux CPU affinity for a complete descendant task tree."""

    provider_id = "linux-affinity-v1"

    def __init__(
        self,
        *,
        proc_root: Path = Path("/proc"),
        affinity_getter: Callable[[int], object] | None = None,
        affinity_setter: Callable[[int, set[int]], object] | None = None,
        max_tasks: int = 4096,
        max_passes: int = 4,
    ) -> None:
        self.proc_root = Path(proc_root)
        self._get = affinity_getter or os.sched_getaffinity
        self._set = affinity_setter or os.sched_setaffinity
        if isinstance(max_tasks, bool) or not isinstance(max_tasks, int) or max_tasks <= 0:
            raise LinuxAffinityError("max_tasks must be a positive integer")
        if isinstance(max_passes, bool) or not isinstance(max_passes, int) or max_passes <= 0:
            raise LinuxAffinityError("max_passes must be a positive integer")
        self.max_tasks = max_tasks
        self.max_passes = max_passes

    @staticmethod
    def _validate_pid(value: int, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise LinuxAffinityError(f"{label} must be a positive integer")
        return value

    @staticmethod
    def _parse_start_time(text: str, label: str) -> int:
        raw = text.strip()
        open_idx = raw.find("(")
        close_idx = raw.rfind(")")
        if open_idx <= 0 or close_idx <= open_idx:
            raise LinuxAffinityError(f"{label}: malformed stat record")
        fields = raw[close_idx + 1 :].strip().split()
        if len(fields) <= 19:
            raise LinuxAffinityError(f"{label}: truncated stat record")
        try:
            start = int(fields[19])
        except ValueError as exc:
            raise LinuxAffinityError(f"{label}: non-numeric start time") from exc
        if start < 0:
            raise LinuxAffinityError(f"{label}: negative start time")
        return start

    def _read_start_time(self, path: Path, label: str) -> int:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LinuxAffinityError(f"{label}: cannot read stat: {exc}") from exc
        return self._parse_start_time(text, label)

    def process_start_time(self, pid: int) -> int:
        pid = self._validate_pid(pid, "pid")
        return self._read_start_time(
            self.proc_root / str(pid) / "stat",
            f"pid {pid}",
        )

    def task_start_time(self, pid: int, tid: int) -> int:
        pid = self._validate_pid(pid, "pid")
        tid = self._validate_pid(tid, "tid")
        return self._read_start_time(
            self.proc_root / str(pid) / "task" / str(tid) / "stat",
            f"pid {pid} tid {tid}",
        )

    def task_ids(self, pid: int) -> tuple[int, ...]:
        pid = self._validate_pid(pid, "pid")
        root = self.proc_root / str(pid) / "task"
        try:
            entries = list(root.iterdir())
        except OSError as exc:
            raise LinuxAffinityError(f"pid {pid}: cannot enumerate tasks: {exc}") from exc
        tids = sorted(
            int(entry.name)
            for entry in entries
            if entry.name.isdigit() and entry.is_dir()
        )
        if not tids:
            raise LinuxAffinityError(f"pid {pid}: process has no observable tasks")
        if len(tids) > self.max_tasks:
            raise LinuxAffinityError(f"pid {pid}: task count exceeds bounded limit")
        return tuple(tids)

    def child_pids(self, pid: int) -> tuple[int, ...]:
        pid = self._validate_pid(pid, "pid")
        children: set[int] = set()
        for tid in self.task_ids(pid):
            path = self.proc_root / str(pid) / "task" / str(tid) / "children"
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise LinuxAffinityError(
                    f"pid {pid} tid {tid}: cannot read children: {exc}"
                ) from exc
            for token in text.split():
                if not token.isdigit() or int(token) <= 0:
                    raise LinuxAffinityError(
                        f"pid {pid} tid {tid}: malformed child pid {token!r}"
                    )
                child = int(token)
                if child == pid:
                    raise LinuxAffinityError(f"pid {pid}: self-cycle in children")
                children.add(child)
        return tuple(sorted(children))

    def process_tree(self, root_pid: int) -> ProcessTree:
        root_pid = self._validate_pid(root_pid, "root_pid")
        queue = [root_pid]
        seen: set[int] = set()
        tasks: list[TaskIdentity] = []
        root_start: int | None = None

        while queue:
            pid = queue.pop(0)
            if pid in seen:
                continue
            if len(seen) >= self.max_tasks:
                raise LinuxAffinityError("process tree exceeds bounded process limit")
            start_before = self.process_start_time(pid)
            if pid == root_pid:
                root_start = start_before
            tids = self.task_ids(pid)
            if len(tasks) + len(tids) > self.max_tasks:
                raise LinuxAffinityError("process tree exceeds bounded task limit")
            for tid in tids:
                tasks.append(
                    TaskIdentity(
                        pid=pid,
                        process_start_time_ticks=start_before,
                        tid=tid,
                        task_start_time_ticks=self.task_start_time(pid, tid),
                    )
                )
            children = self.child_pids(pid)
            start_after = self.process_start_time(pid)
            if start_after != start_before:
                raise LinuxAffinityError(
                    f"pid {pid}: process identity changed during tree discovery"
                )
            seen.add(pid)
            for child in children:
                if child not in seen and child not in queue:
                    queue.append(child)

        if root_start is None:
            raise LinuxAffinityError("root process identity was not observed")
        ordered = tuple(sorted(tasks, key=lambda item: (item.pid, item.tid)))
        return ProcessTree(root_pid, root_start, ordered)

    def host_affinity(self) -> tuple[int, ...]:
        try:
            raw = self._get(0)
        except OSError as exc:
            raise LinuxAffinityError(f"cannot read host/process affinity: {exc}") from exc
        return _cpu_tuple(raw, "host affinity")

    def task_affinity(self, tid: int) -> tuple[int, ...]:
        tid = self._validate_pid(tid, "tid")
        try:
            raw = self._get(tid)
        except OSError as exc:
            raise LinuxAffinityError(f"tid {tid}: cannot read affinity: {exc}") from exc
        return _cpu_tuple(raw, f"tid {tid} affinity")

    def set_task_affinity(self, tid: int, cpus: Iterable[int]) -> None:
        tid = self._validate_pid(tid, "tid")
        normalized = _cpu_tuple(cpus, f"tid {tid} requested affinity")
        try:
            self._set(tid, set(normalized))
        except OSError as exc:
            raise LinuxAffinityError(f"tid {tid}: cannot set affinity: {exc}") from exc

    def inspect_tree_affinity(self, root_pid: int) -> ProcessTreeAffinity:
        tree = self.process_tree(root_pid)
        rows: list[TaskAffinityObservation] = []
        for task in tree.tasks:
            rows.append(
                TaskAffinityObservation(
                    identity=task,
                    cpus=self.task_affinity(task.tid),
                )
            )
        return ProcessTreeAffinity(
            root_pid=tree.root_pid,
            root_start_time_ticks=tree.root_start_time_ticks,
            tasks=tuple(rows),
            passes=1,
            enforced=False,
        )

    def apply_tree_affinity(
        self,
        root_pid: int,
        cpus: Iterable[int],
    ) -> ProcessTreeAffinity:
        requested = _cpu_tuple(cpus, "requested tree affinity")
        last_fault: str | None = None
        for pass_index in range(1, self.max_passes + 1):
            try:
                tree = self.process_tree(root_pid)
                before_ids = {
                    (
                        task.pid,
                        task.process_start_time_ticks,
                        task.tid,
                        task.task_start_time_ticks,
                    )
                    for task in tree.tasks
                }
                for task in tree.tasks:
                    try:
                        self.set_task_affinity(task.tid, requested)
                    except LinuxAffinityError as exc:
                        if "No such process" in str(exc):
                            last_fault = str(exc)
                            break
                        raise
                else:
                    observed = self.inspect_tree_affinity(root_pid)
                    after_ids = {
                        (
                            task.identity.pid,
                            task.identity.process_start_time_ticks,
                            task.identity.tid,
                            task.identity.task_start_time_ticks,
                        )
                        for task in observed.tasks
                    }
                    all_match = all(task.cpus == requested for task in observed.tasks)
                    if before_ids == after_ids and all_match:
                        return ProcessTreeAffinity(
                            root_pid=observed.root_pid,
                            root_start_time_ticks=observed.root_start_time_ticks,
                            tasks=observed.tasks,
                            passes=pass_index,
                            enforced=True,
                        )
                    last_fault = "task tree changed or affinity mismatch after application"
                    continue
            except LinuxAffinityError as exc:
                last_fault = str(exc)
                if "No such process" not in last_fault:
                    raise
        raise LinuxAffinityError(
            "process tree did not stabilize within bounded affinity passes"
            + ("" if last_fault is None else f": {last_fault}")
        )
