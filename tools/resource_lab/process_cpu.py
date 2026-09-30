"""High-resolution per-process CPU clock evidence for the J6 laboratory."""

from __future__ import annotations

import ctypes
import math
import os
import time
from dataclasses import dataclass
from typing import Callable


PROCESS_CPU_METHOD = "posix-process-cpu-clock-v1"


class ProcessCpuClockError(RuntimeError):
    pass


def _libc_process_clock_id(pid: int) -> int:
    """Return the POSIX process CPU clock id for *pid* via libc.

    Python exposes thread CPU clock helpers but not clock_getcpuclockid(3) on
    all supported versions. J6 needs the clock of the engine process, not the
    controller process or one Python thread, so resolve the POSIX API directly.
    """
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        function = libc.clock_getcpuclockid
    except (OSError, AttributeError) as exc:
        raise ProcessCpuClockError(
            f"libc clock_getcpuclockid is unavailable: {exc}"
        ) from exc
    function.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    function.restype = ctypes.c_int
    clock_id = ctypes.c_int()
    result = int(function(int(pid), ctypes.byref(clock_id)))
    if result != 0:
        raise ProcessCpuClockError(
            f"clock_getcpuclockid({pid}) failed: {os.strerror(result)} ({result})"
        )
    return int(clock_id.value)


@dataclass(frozen=True)
class ProcessCpuClockEvidence:
    method: str
    pid: int
    resolution_ns: int
    before_ns: int
    after_ns: int

    def __post_init__(self) -> None:
        if self.method != PROCESS_CPU_METHOD:
            raise ProcessCpuClockError("unsupported process CPU clock method")
        if isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid <= 0:
            raise ProcessCpuClockError("process CPU clock pid must be positive integer")
        for name in ("resolution_ns", "before_ns", "after_ns"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ProcessCpuClockError(f"{name} must be a non-negative integer")
        if self.resolution_ns <= 0:
            raise ProcessCpuClockError("process CPU clock resolution must be positive")
        if self.after_ns < self.before_ns:
            raise ProcessCpuClockError("process CPU clock regressed")

    @property
    def delta_ns(self) -> int:
        return self.after_ns - self.before_ns

    def as_dict(self) -> dict[str, int | str]:
        return {
            "method": self.method,
            "pid": self.pid,
            "resolution_ns": self.resolution_ns,
            "before_ns": self.before_ns,
            "after_ns": self.after_ns,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "ProcessCpuClockEvidence":
        if not isinstance(raw, dict):
            raise ProcessCpuClockError("process CPU clock evidence must be object")
        if set(raw) != {"method", "pid", "resolution_ns", "before_ns", "after_ns"}:
            raise ProcessCpuClockError("process CPU clock evidence fields differ from schema")
        return cls(
            method=raw.get("method"),
            pid=raw.get("pid"),
            resolution_ns=raw.get("resolution_ns"),
            before_ns=raw.get("before_ns"),
            after_ns=raw.get("after_ns"),
        )


class ProcessCpuClock:
    """Sample CLOCK_PROCESS_CPUTIME_ID for one already-running process."""

    def __init__(
        self,
        pid: int,
        *,
        max_resolution_ns: int,
        clock_id_factory: Callable[[int], int] | None = None,
        clock_gettime_ns: Callable[[int], int] | None = None,
        clock_getres: Callable[[int], float] | None = None,
    ) -> None:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ProcessCpuClockError("pid must be a positive integer")
        if (
            isinstance(max_resolution_ns, bool)
            or not isinstance(max_resolution_ns, int)
            or max_resolution_ns <= 0
        ):
            raise ProcessCpuClockError("max_resolution_ns must be a positive integer")
        id_factory = clock_id_factory or _libc_process_clock_id
        self._gettime = clock_gettime_ns or time.clock_gettime_ns
        getres = clock_getres or time.clock_getres
        try:
            self.clock_id = id_factory(pid)
            resolution_seconds = float(getres(self.clock_id))
        except (OSError, ValueError, AttributeError, ProcessCpuClockError) as exc:
            raise ProcessCpuClockError(
                f"cannot initialize process CPU clock for pid {pid}: {exc}"
            ) from exc
        if not math.isfinite(resolution_seconds) or resolution_seconds <= 0:
            raise ProcessCpuClockError("process CPU clock resolution is invalid")
        self.pid = pid
        self.resolution_ns = max(1, int(math.ceil(resolution_seconds * 1_000_000_000.0)))
        self.max_resolution_ns = max_resolution_ns
        if self.resolution_ns > max_resolution_ns:
            raise ProcessCpuClockError(
                f"process CPU clock resolution {self.resolution_ns}ns exceeds "
                f"lab maximum {max_resolution_ns}ns"
            )

    def sample_ns(self) -> int:
        try:
            value = self._gettime(self.clock_id)
        except (OSError, ValueError) as exc:
            raise ProcessCpuClockError(
                f"cannot sample process CPU clock for pid {self.pid}: {exc}"
            ) from exc
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ProcessCpuClockError("process CPU clock sample is invalid")
        return value

    def evidence(self, before_ns: int, after_ns: int) -> ProcessCpuClockEvidence:
        return ProcessCpuClockEvidence(
            method=PROCESS_CPU_METHOD,
            pid=self.pid,
            resolution_ns=self.resolution_ns,
            before_ns=before_ns,
            after_ns=after_ns,
        )
