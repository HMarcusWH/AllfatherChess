"""Best-effort descriptive affinity observation for J6.

This deliberately does NOT change J5 enforcement semantics. J5 remains fail-closed.
J6 may retain a transient /proc task-race as an observation fault while keeping
an otherwise valid engine measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adapters.resource.linux_affinity import LinuxAffinityError, LinuxAffinityProvider


OBSERVATION_POLICY = "bounded-observed-affinity-v1"


class ResourceLabObservationError(RuntimeError):
    """The root process itself could not be identified; the measurement is invalid."""


@dataclass(frozen=True)
class AffinityObservation:
    policy: str
    status: str
    root_pid: int
    root_start_time_ticks: int
    attempts: int
    observation: dict[str, Any] | None
    faults: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.policy != OBSERVATION_POLICY:
            raise ResourceLabObservationError("unsupported affinity observation policy")
        if self.status not in ("completed", "incomplete"):
            raise ResourceLabObservationError("affinity observation status invalid")
        if self.attempts <= 0:
            raise ResourceLabObservationError("affinity observation attempts must be positive")
        if self.status == "completed" and self.observation is None:
            raise ResourceLabObservationError("completed affinity observation lacks payload")
        if self.status == "incomplete" and not self.faults:
            raise ResourceLabObservationError("incomplete affinity observation lacks fault")
        if self.observation is not None and self.observation.get("enforced") is not False:
            raise ResourceLabObservationError("J6 observed affinity may not claim enforcement")

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AffinityObservation":
        if not isinstance(raw, dict):
            raise ResourceLabObservationError("affinity observation must be object")
        if set(raw) != {
            "policy",
            "status",
            "root_pid",
            "root_start_time_ticks",
            "attempts",
            "observation",
            "faults",
            "authority",
        }:
            raise ResourceLabObservationError(
                "affinity observation fields differ from schema"
            )
        if raw.get("authority") != {
            "resource_context": True,
            "resource_authorization": False,
            "outward_move": False,
        }:
            raise ResourceLabObservationError(
                "affinity observation authority marker is invalid"
            )
        faults = raw.get("faults")
        if not isinstance(faults, list) or any(
            not isinstance(item, str) or not item for item in faults
        ):
            raise ResourceLabObservationError(
                "affinity observation faults must be non-empty strings"
            )
        return cls(
            policy=raw.get("policy"),
            status=raw.get("status"),
            root_pid=raw.get("root_pid"),
            root_start_time_ticks=raw.get("root_start_time_ticks"),
            attempts=raw.get("attempts"),
            observation=raw.get("observation"),
            faults=tuple(faults),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "status": self.status,
            "root_pid": self.root_pid,
            "root_start_time_ticks": self.root_start_time_ticks,
            "attempts": self.attempts,
            "observation": self.observation,
            "faults": list(self.faults),
            "authority": {
                "resource_context": True,
                "resource_authorization": False,
                "outward_move": False,
            },
        }


def observe_affinity(
    provider: LinuxAffinityProvider,
    pid: int,
    *,
    max_attempts: int,
) -> AffinityObservation:
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts <= 0:
        raise ResourceLabObservationError("max_attempts must be positive integer")
    try:
        root_start = provider.process_start_time(pid)
    except LinuxAffinityError as exc:
        raise ResourceLabObservationError(
            f"root process identity unavailable for pid {pid}: {exc}"
        ) from exc

    faults: list[str] = []
    for attempt in range(1, max_attempts + 1):
        try:
            observed = provider.inspect_tree_affinity(pid)
            if observed.root_start_time_ticks != root_start:
                raise ResourceLabObservationError(
                    f"root process identity changed during affinity observation: "
                    f"{root_start}->{observed.root_start_time_ticks}"
                )
            return AffinityObservation(
                policy=OBSERVATION_POLICY,
                status="completed",
                root_pid=pid,
                root_start_time_ticks=root_start,
                attempts=attempt,
                observation=observed.as_dict(),
                faults=tuple(faults),
            )
        except LinuxAffinityError as exc:
            faults.append(f"{type(exc).__name__}: {exc}")
            try:
                current_start = provider.process_start_time(pid)
            except LinuxAffinityError as root_exc:
                raise ResourceLabObservationError(
                    f"root process disappeared during affinity observation: {root_exc}"
                ) from root_exc
            if current_start != root_start:
                raise ResourceLabObservationError(
                    f"root process identity changed during affinity observation: "
                    f"{root_start}->{current_start}"
                )

    return AffinityObservation(
        policy=OBSERVATION_POLICY,
        status="incomplete",
        root_pid=pid,
        root_start_time_ticks=root_start,
        attempts=max_attempts,
        observation=None,
        faults=tuple(faults),
    )


def process_cpu_scope(
    before: AffinityObservation,
    after: AffinityObservation,
) -> dict[str, Any]:
    """Describe whether a root-process CPU clock covers all observed engine work.

    POSIX process CPU clocks include all threads in one process but do not include
    separate child processes. An incomplete task-tree observation also prevents
    a claim that the process clock covered the whole engine execution.
    """
    if before.root_pid != after.root_pid:
        raise ResourceLabObservationError(
            "CPU-scope observations disagree on root pid"
        )
    if before.root_start_time_ticks != after.root_start_time_ticks:
        raise ResourceLabObservationError(
            "CPU-scope observations disagree on root process identity"
        )
    root_pid = before.root_pid
    observed_pids: set[int] = {root_pid}
    reasons: list[str] = []
    for label, sample in (("before", before), ("after", after)):
        if sample.status != "completed":
            reasons.append(f"{label}-affinity-observation-incomplete")
            continue
        payload = sample.observation
        if not isinstance(payload, dict):
            raise ResourceLabObservationError(
                f"{label} completed affinity observation lacks payload"
            )
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            raise ResourceLabObservationError(
                f"{label} affinity task list is invalid"
            )
        for task in tasks:
            if not isinstance(task, dict):
                raise ResourceLabObservationError(
                    f"{label} affinity task is invalid"
                )
            pid = task.get("pid")
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                raise ResourceLabObservationError(
                    f"{label} affinity task pid is invalid"
                )
            observed_pids.add(pid)
    children = sorted(pid for pid in observed_pids if pid != root_pid)
    if children:
        reasons.append("separate-child-process-observed")
    return {
        "complete": not reasons,
        "root_pid": root_pid,
        "root_start_time_ticks": before.root_start_time_ticks,
        "observed_process_ids": sorted(observed_pids),
        "child_process_ids": children,
        "reasons": reasons,
    }
