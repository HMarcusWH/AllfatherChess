"""Linux /proc process accounting for the declared qualification platform.

The provider intentionally measures process facts only. It does not know about
EXPLORE/VERIFY/REFINE semantics; controller.resource_measurement binds these
snapshots to stage identities.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path


class ResourceProviderError(RuntimeError):
    """Raised when process resource evidence cannot be sampled honestly."""


@dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    start_time_ticks: int
    monotonic_ns: int
    user_cpu_ticks: int
    system_cpu_ticks: int
    rss_bytes: int | None
    vm_hwm_bytes: int | None

    @property
    def cpu_ticks(self) -> int:
        return self.user_cpu_ticks + self.system_cpu_ticks

    def as_dict(self) -> dict[str, int | None]:
        return {
            "pid": self.pid,
            "start_time_ticks": self.start_time_ticks,
            "monotonic_ns": self.monotonic_ns,
            "user_cpu_ticks": self.user_cpu_ticks,
            "system_cpu_ticks": self.system_cpu_ticks,
            "rss_bytes": self.rss_bytes,
            "vm_hwm_bytes": self.vm_hwm_bytes,
        }


@dataclass(frozen=True)
class ProcessDelta:
    pid: int
    start_time_ticks: int
    wall_ms: float
    cpu_ms: float
    start_rss_bytes: int | None
    end_rss_bytes: int | None
    vm_hwm_bytes: int | None

    def as_dict(self) -> dict[str, int | float | None]:
        return {
            "pid": self.pid,
            "start_time_ticks": self.start_time_ticks,
            "wall_ms": round(self.wall_ms, 3),
            "cpu_ms": round(self.cpu_ms, 3),
            "start_rss_bytes": self.start_rss_bytes,
            "end_rss_bytes": self.end_rss_bytes,
            "vm_hwm_bytes": self.vm_hwm_bytes,
        }


class LinuxProcProvider:
    """Sample one Linux process without adding third-party dependencies."""

    provider_id = "linux-procfs-v1"

    def __init__(self, *, proc_root: Path = Path("/proc"), clock_ticks: int | None = None) -> None:
        self.proc_root = Path(proc_root)
        try:
            ticks = os.sysconf("SC_CLK_TCK") if clock_ticks is None else clock_ticks
        except (ValueError, OSError) as exc:
            raise ResourceProviderError(f"cannot determine SC_CLK_TCK: {exc}") from exc
        if isinstance(ticks, bool) or not isinstance(ticks, int) or ticks <= 0:
            raise ResourceProviderError(f"invalid SC_CLK_TCK: {ticks!r}")
        self.clock_ticks = ticks

    @staticmethod
    def _parse_stat(text: str) -> tuple[int, int, int, int, int]:
        """Return pid, utime, stime, starttime, rss_pages.

        /proc/<pid>/stat field 2 is parenthesized and may contain spaces.
        Splitting the whole record would shift every later field. Parse the
        closing parenthesis first, then index from field 3 (state).
        """
        raw = text.strip()
        open_idx = raw.find("(")
        close_idx = raw.rfind(")")
        if open_idx <= 0 or close_idx <= open_idx:
            raise ResourceProviderError("malformed /proc stat record")
        try:
            pid = int(raw[:open_idx].strip())
        except ValueError as exc:
            raise ResourceProviderError("malformed /proc stat pid") from exc
        fields = raw[close_idx + 1 :].strip().split()
        if len(fields) <= 21:
            raise ResourceProviderError("truncated /proc stat record")
        try:
            utime = int(fields[11])
            stime = int(fields[12])
            starttime = int(fields[19])
            rss_pages = int(fields[21])
        except ValueError as exc:
            raise ResourceProviderError("non-numeric /proc stat counter") from exc
        return pid, utime, stime, starttime, rss_pages

    @staticmethod
    def _status_bytes(text: str, key: str) -> int | None:
        prefix = key + ":"
        for line in text.splitlines():
            if not line.startswith(prefix):
                continue
            parts = line[len(prefix) :].strip().split()
            if not parts:
                return None
            try:
                value = int(parts[0])
            except ValueError:
                return None
            unit = parts[1].lower() if len(parts) > 1 else "b"
            if unit == "kb":
                return value * 1024
            if unit == "mb":
                return value * 1024 * 1024
            return value
        return None

    def snapshot(self, pid: int) -> ProcessSnapshot:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ResourceProviderError(f"invalid pid: {pid!r}")
        root = self.proc_root / str(pid)
        try:
            stat_text = (root / "stat").read_text(encoding="utf-8")
            status_text = (root / "status").read_text(encoding="utf-8")
        except OSError as exc:
            raise ResourceProviderError(f"cannot sample pid {pid}: {exc}") from exc
        parsed_pid, utime, stime, starttime, rss_pages = self._parse_stat(stat_text)
        if parsed_pid != pid:
            raise ResourceProviderError(
                f"procfs pid mismatch: requested {pid}, observed {parsed_pid}"
            )
        rss = self._status_bytes(status_text, "VmRSS")
        hwm = self._status_bytes(status_text, "VmHWM")
        if rss is None:
            try:
                rss = rss_pages * int(os.sysconf("SC_PAGE_SIZE"))
            except (ValueError, OSError):
                rss = None
        return ProcessSnapshot(
            pid=pid,
            start_time_ticks=starttime,
            monotonic_ns=time.monotonic_ns(),
            user_cpu_ticks=utime,
            system_cpu_ticks=stime,
            rss_bytes=rss,
            vm_hwm_bytes=hwm,
        )

    def delta(self, start: ProcessSnapshot, end: ProcessSnapshot) -> ProcessDelta:
        if start.pid != end.pid:
            raise ResourceProviderError(
                f"pid changed across measurement: {start.pid} -> {end.pid}"
            )
        if start.start_time_ticks != end.start_time_ticks:
            raise ResourceProviderError(
                f"pid {start.pid} was reused during measurement "
                f"({start.start_time_ticks} -> {end.start_time_ticks})"
            )
        tick_delta = end.cpu_ticks - start.cpu_ticks
        wall_ns = end.monotonic_ns - start.monotonic_ns
        if tick_delta < 0 or wall_ns < 0:
            raise ResourceProviderError("process counters regressed during measurement")
        hwm_values = [
            value
            for value in (start.vm_hwm_bytes, end.vm_hwm_bytes)
            if value is not None
        ]
        return ProcessDelta(
            pid=start.pid,
            start_time_ticks=start.start_time_ticks,
            wall_ms=wall_ns / 1_000_000.0,
            cpu_ms=(tick_delta * 1000.0) / float(self.clock_ticks),
            start_rss_bytes=start.rss_bytes,
            end_rss_bytes=end.rss_bytes,
            vm_hwm_bytes=max(hwm_values) if hwm_values else None,
        )
