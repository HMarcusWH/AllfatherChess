"""Measured physical resource accounting for one Allfather run.

Reservations answer whether work may start. Measurements answer what already
started work physically consumed. The two are intentionally independent: a
measurement can invalidate a final envelope claim, but it can never authorize a
search that the reservation layer denied.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adapters.resource import (
    LinuxProcProvider,
    ProcessSnapshot,
    ResourceProviderError,
)


RESOURCE_SCHEMA_VERSION = 1
SUPPORTED_RESOURCE_PROVIDERS = ("linux-procfs-v1",)


class ResourceMeasurementError(RuntimeError):
    """Raised when the measurement contract is configured dishonestly."""


@dataclass(frozen=True)
class ResourceMeasurementSettings:
    enabled: bool
    provider: str
    require_cpu_for_claim: bool
    require_gpu_for_claim: bool
    record_memory: bool

    @classmethod
    def from_config(
        cls,
        raw: dict[str, object] | None,
        *,
        mode: str,
    ) -> "ResourceMeasurementSettings":
        if raw is None:
            return cls(
                enabled=False,
                provider="linux-procfs-v1",
                require_cpu_for_claim=False,
                require_gpu_for_claim=False,
                record_memory=False,
            )
        unknown = sorted(
            set(raw)
            - {
                "enabled",
                "provider",
                "require_cpu_for_claim",
                "require_gpu_for_claim",
                "record_memory",
            }
        )
        if unknown:
            raise ResourceMeasurementError(
                f"resource_measurement contains unsupported keys: {unknown}"
            )
        enabled = raw.get("enabled")
        if not isinstance(enabled, bool):
            raise ResourceMeasurementError("resource_measurement.enabled must be a boolean")
        provider = raw.get("provider", "linux-procfs-v1")
        if not isinstance(provider, str) or provider not in SUPPORTED_RESOURCE_PROVIDERS:
            raise ResourceMeasurementError(
                "resource_measurement.provider must be one of "
                f"{list(SUPPORTED_RESOURCE_PROVIDERS)}, got {provider!r}"
            )

        def flag(name: str, default: bool) -> bool:
            value = raw.get(name, default)
            if not isinstance(value, bool):
                raise ResourceMeasurementError(
                    f"resource_measurement.{name} must be a boolean"
                )
            return value

        require_cpu = flag("require_cpu_for_claim", mode == "active" and enabled)
        require_gpu = flag("require_gpu_for_claim", False)
        record_memory = flag("record_memory", enabled)
        if not enabled and (require_cpu or require_gpu or record_memory):
            raise ResourceMeasurementError(
                "disabled resource measurement cannot require CPU/GPU evidence or memory recording"
            )
        return cls(
            enabled=enabled,
            provider=provider,
            require_cpu_for_claim=require_cpu,
            require_gpu_for_claim=require_gpu,
            record_memory=record_memory,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "require_cpu_for_claim": self.require_cpu_for_claim,
            "require_gpu_for_claim": self.require_gpu_for_claim,
            "record_memory": self.record_memory,
        }


@dataclass(frozen=True)
class StageResourceMeasurement:
    key: str
    instance: str
    phase: str
    pid: int | None
    complete: bool
    cpu_ms: float | None
    wall_ms: float | None
    start_rss_bytes: int | None
    end_rss_bytes: int | None
    vm_hwm_bytes: int | None
    reason: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "instance": self.instance,
            "phase": self.phase,
            "pid": self.pid,
            "complete": self.complete,
            "cpu_ms": None if self.cpu_ms is None else round(self.cpu_ms, 3),
            "wall_ms": None if self.wall_ms is None else round(self.wall_ms, 3),
            "start_rss_bytes": self.start_rss_bytes,
            "end_rss_bytes": self.end_rss_bytes,
            "vm_hwm_bytes": self.vm_hwm_bytes,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class _ActiveStage:
    key: str
    instance: str
    phase: str
    start: ProcessSnapshot


class ResourceMeasurementRun:
    """Own measured process resource evidence for one replay run."""

    def __init__(
        self,
        *,
        run_id: str,
        settings: ResourceMeasurementSettings,
        provider: LinuxProcProvider | None = None,
        controller_cpu_started_ns: int | None = None,
    ) -> None:
        self.run_id = run_id
        self.settings = settings
        self._lock = threading.RLock()
        self._provider_error: str | None = None
        self._provider: LinuxProcProvider | None = None
        if settings.enabled:
            try:
                self._provider = provider or LinuxProcProvider()
            except Exception as exc:
                self._provider_error = f"{type(exc).__name__}: {exc}"
        self._active: dict[str, _ActiveStage] = {}
        self._active_instance: dict[str, str] = {}
        self._measurements: dict[str, StageResourceMeasurement] = {}
        self._process_starts: dict[str, ProcessSnapshot] = {}
        self._process_totals: dict[str, dict[str, object]] = {}
        self._controller_cpu_started_ns = (
            time.process_time_ns()
            if controller_cpu_started_ns is None
            else int(controller_cpu_started_ns)
        )
        self._sealed: dict[str, object] | None = None

    @property
    def provider_id(self) -> str:
        return self.settings.provider

    def register_process(self, *, instance: str, pid: int | None) -> None:
        """Take one run-level baseline so IPC/idle gaps cannot disappear."""
        if not self.settings.enabled:
            return
        with self._lock:
            if instance in self._process_starts or instance in self._process_totals:
                return
            if self._provider is None or pid is None:
                self._process_totals[instance] = {
                    "instance": instance,
                    "pid": pid,
                    "complete": False,
                    "cpu_ms": None,
                    "wall_ms": None,
                    "reason": self._provider_error or "backend pid unavailable at run baseline",
                }
                return
            try:
                self._process_starts[instance] = self._provider.snapshot(pid)
            except Exception as exc:
                self._process_totals[instance] = {
                    "instance": instance,
                    "pid": pid,
                    "complete": False,
                    "cpu_ms": None,
                    "wall_ms": None,
                    "reason": f"run baseline sample failed: {type(exc).__name__}: {exc}",
                }

    def _finalize_process_totals(self) -> None:
        if not self.settings.enabled or self._provider is None:
            return
        for instance, start in list(self._process_starts.items()):
            try:
                end = self._provider.snapshot(start.pid)
                delta = self._provider.delta(start, end)
                self._process_totals[instance] = {
                    "instance": instance,
                    "pid": start.pid,
                    "complete": True,
                    "cpu_ms": round(delta.cpu_ms, 3),
                    "wall_ms": round(delta.wall_ms, 3),
                    "start_rss_bytes": (
                        delta.start_rss_bytes if self.settings.record_memory else None
                    ),
                    "end_rss_bytes": (
                        delta.end_rss_bytes if self.settings.record_memory else None
                    ),
                    "vm_hwm_bytes": (
                        delta.vm_hwm_bytes if self.settings.record_memory else None
                    ),
                    "reason": None,
                }
            except Exception as exc:
                self._process_totals[instance] = {
                    "instance": instance,
                    "pid": start.pid,
                    "complete": False,
                    "cpu_ms": None,
                    "wall_ms": None,
                    "start_rss_bytes": (
                        start.rss_bytes if self.settings.record_memory else None
                    ),
                    "end_rss_bytes": None,
                    "vm_hwm_bytes": (
                        start.vm_hwm_bytes if self.settings.record_memory else None
                    ),
                    "reason": f"run terminal sample failed: {type(exc).__name__}: {exc}",
                }
            self._process_starts.pop(instance, None)

    def begin_stage(self, *, key: str, instance: str, phase: str, pid: int | None) -> None:
        if not self.settings.enabled:
            return
        with self._lock:
            if self._sealed is not None:
                raise ResourceMeasurementError("cannot begin a stage after resource report sealing")
            if key in self._active or key in self._measurements:
                raise ResourceMeasurementError(f"resource stage key already exists: {key}")
            other = self._active_instance.get(instance)
            if other is not None:
                raise ResourceMeasurementError(
                    f"instance {instance!r} already has active measured stage {other!r}"
                )
            if self._provider is None:
                self._measurements[key] = StageResourceMeasurement(
                    key=key,
                    instance=instance,
                    phase=phase,
                    pid=pid,
                    complete=False,
                    cpu_ms=None,
                    wall_ms=None,
                    start_rss_bytes=None,
                    end_rss_bytes=None,
                    vm_hwm_bytes=None,
                    reason=self._provider_error or "resource provider unavailable",
                )
                return
            if pid is None:
                self._measurements[key] = StageResourceMeasurement(
                    key=key,
                    instance=instance,
                    phase=phase,
                    pid=None,
                    complete=False,
                    cpu_ms=None,
                    wall_ms=None,
                    start_rss_bytes=None,
                    end_rss_bytes=None,
                    vm_hwm_bytes=None,
                    reason="backend pid unavailable at dispatch",
                )
                return
            try:
                start = self._provider.snapshot(pid)
            except Exception as exc:
                self._measurements[key] = StageResourceMeasurement(
                    key=key,
                    instance=instance,
                    phase=phase,
                    pid=pid,
                    complete=False,
                    cpu_ms=None,
                    wall_ms=None,
                    start_rss_bytes=None,
                    end_rss_bytes=None,
                    vm_hwm_bytes=None,
                    reason=f"start sample failed: {type(exc).__name__}: {exc}",
                )
                return
            self._active[key] = _ActiveStage(
                key=key,
                instance=instance,
                phase=phase,
                start=start,
            )
            self._active_instance[instance] = key

    def finish_stage(self, key: str) -> StageResourceMeasurement | None:
        if not self.settings.enabled:
            return None
        with self._lock:
            existing = self._measurements.get(key)
            if existing is not None:
                return existing
            active = self._active.pop(key, None)
            if active is None:
                return None
            self._active_instance.pop(active.instance, None)
            assert self._provider is not None
            try:
                end = self._provider.snapshot(active.start.pid)
                delta = self._provider.delta(active.start, end)
                measurement = StageResourceMeasurement(
                    key=key,
                    instance=active.instance,
                    phase=active.phase,
                    pid=active.start.pid,
                    complete=True,
                    cpu_ms=delta.cpu_ms,
                    wall_ms=delta.wall_ms,
                    start_rss_bytes=delta.start_rss_bytes if self.settings.record_memory else None,
                    end_rss_bytes=delta.end_rss_bytes if self.settings.record_memory else None,
                    vm_hwm_bytes=delta.vm_hwm_bytes if self.settings.record_memory else None,
                    reason=None,
                )
            except Exception as exc:
                measurement = StageResourceMeasurement(
                    key=key,
                    instance=active.instance,
                    phase=active.phase,
                    pid=active.start.pid,
                    complete=False,
                    cpu_ms=None,
                    wall_ms=None,
                    start_rss_bytes=(
                        active.start.rss_bytes if self.settings.record_memory else None
                    ),
                    end_rss_bytes=None,
                    vm_hwm_bytes=(
                        active.start.vm_hwm_bytes if self.settings.record_memory else None
                    ),
                    reason=f"terminal sample failed: {type(exc).__name__}: {exc}",
                )
            self._measurements[key] = measurement
            return measurement

    def abandon_stage(self, key: str, *, reason: str) -> StageResourceMeasurement | None:
        if not self.settings.enabled:
            return None
        with self._lock:
            existing = self._measurements.get(key)
            if existing is not None:
                return existing
            active = self._active.pop(key, None)
            if active is None:
                return None
            self._active_instance.pop(active.instance, None)
            measurement = StageResourceMeasurement(
                key=key,
                instance=active.instance,
                phase=active.phase,
                pid=active.start.pid,
                complete=False,
                cpu_ms=None,
                wall_ms=None,
                start_rss_bytes=(
                    active.start.rss_bytes if self.settings.record_memory else None
                ),
                end_rss_bytes=None,
                vm_hwm_bytes=(
                    active.start.vm_hwm_bytes if self.settings.record_memory else None
                ),
                reason=reason,
            )
            self._measurements[key] = measurement
            return measurement

    def measurement(self, key: str) -> StageResourceMeasurement | None:
        with self._lock:
            return self._measurements.get(key)

    def finish_or_measurement(self, key: str) -> StageResourceMeasurement | None:
        measured = self.measurement(key)
        return measured if measured is not None else self.finish_stage(key)

    def controller_cpu_ms(self) -> float:
        delta = time.process_time_ns() - self._controller_cpu_started_ns
        return max(0.0, delta / 1_000_000.0)

    def _coverage(self) -> dict[str, object]:
        measurements = list(self._measurements.values())
        process_totals = list(self._process_totals.values())
        cpu_complete = (
            self.settings.enabled
            and self._provider is not None
            and not self._active
            and not self._process_starts
            and bool(measurements)
            and bool(process_totals)
            and all(item.complete and item.cpu_ms is not None for item in measurements)
            and all(
                item.get("complete") is True and item.get("cpu_ms") is not None
                for item in process_totals
            )
        )
        gpu_required = self.settings.require_gpu_for_claim
        # No GPU provider exists in M14-B. A GPU-requiring profile must fail
        # closed instead of treating utilization estimates as device time.
        gpu_complete = False
        return {
            "cpu": {
                "required": self.settings.require_cpu_for_claim,
                "complete": cpu_complete,
            },
            "gpu": {
                "required": gpu_required,
                "complete": gpu_complete,
                "provider": None,
            },
            "memory": {
                "recorded": self.settings.record_memory,
                "semantics": (
                    "endpoint RSS plus process-lifetime VmHWM; VmHWM is not a stage-local peak"
                    if self.settings.record_memory
                    else "disabled"
                ),
            },
        }

    def _report_payload(self) -> dict[str, object]:
        controller_cpu = self.controller_cpu_ms()
        measurements = [
            item.as_dict()
            for item in sorted(self._measurements.values(), key=lambda item: item.key)
        ]
        stage_engine_cpu = sum(
            float(item.cpu_ms)
            for item in self._measurements.values()
            if item.complete and item.cpu_ms is not None
        )
        measured_engine_cpu = sum(
            float(item["cpu_ms"])
            for item in self._process_totals.values()
            if item.get("complete") is True and isinstance(item.get("cpu_ms"), (int, float))
        )
        coverage = self._coverage()
        cpu_ok = bool(coverage["cpu"]["complete"]) if self.settings.require_cpu_for_claim else True
        gpu_ok = bool(coverage["gpu"]["complete"]) if self.settings.require_gpu_for_claim else True
        return {
            "schema_version": RESOURCE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "provider": self.provider_id if self.settings.enabled else None,
            "settings": self.settings.as_dict(),
            "provider_error": self._provider_error,
            "coverage": coverage,
            "controller": {
                "source": "time.process_time_ns",
                "cpu_ms": round(controller_cpu, 3),
                "scope": (
                    "controller process CPU from replay-run creation through the "
                    "resource-certificate sampling boundary; artifact serialization "
                    "after the terminal sample is outside the sample by construction"
                ),
            },
            "engine_cpu_ms": round(measured_engine_cpu, 3),
            "stage_engine_cpu_ms": round(stage_engine_cpu, 3),
            "physical_cpu_ms": round(measured_engine_cpu + controller_cpu, 3),
            "processes": {
                name: dict(value)
                for name, value in sorted(self._process_totals.items())
            },
            "stages": measurements,
            "qualified": bool(self.settings.enabled and cpu_ok and gpu_ok),
        }

    @staticmethod
    def _canonical_bytes(payload: dict[str, object]) -> bytes:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")

    @staticmethod
    def _atomic_write(path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            text=True,
        )
        tmp = Path(temporary)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def seal(self, path: Path) -> dict[str, object]:
        with self._lock:
            if self._sealed is not None:
                return dict(self._sealed)
            # Any stage still active at certificate time is incomplete evidence.
            # Do not take a late endpoint and pretend it was the stage boundary.
            for key in list(self._active):
                self.abandon_stage(
                    key,
                    reason="resource report sealed before a terminal stage sample was observed",
                )
            self._finalize_process_totals()
            payload = self._report_payload()
            digest = hashlib.sha256(self._canonical_bytes(payload)).hexdigest()
            payload["report_id"] = f"resource-{digest[:16]}"
            rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
            self._atomic_write(Path(path), rendered)
            file_sha = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            summary = {
                "path": Path(path).name,
                "sha256": file_sha,
                "report_id": payload["report_id"],
                "provider": payload["provider"],
                "coverage": payload["coverage"],
                "qualified": payload["qualified"],
                "physical_cpu_ms": payload["physical_cpu_ms"],
                "engine_cpu_ms": payload["engine_cpu_ms"],
                "controller_cpu_ms": payload["controller"]["cpu_ms"],
            }
            self._sealed = summary
            return dict(summary)
