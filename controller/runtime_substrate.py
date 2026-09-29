"""Immutable runtime-substrate identity for M14-J qualification evidence.

HostCapabilities describes the machine/resource domain. RuntimeSubstrate binds
software/runtime facts that can materially alter timing or low-level numerical
execution without changing engine source: kernel/OS/libc, Python, the runner
image, OpenBLAS package identity, and procfs CPU-accounting resolution.

It is observation only. It grants neither resource nor move authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from controller.decision import canonical_digest
from controller.resource_profiles import (
    ORCHESTRATION_SCHEMA_VERSION,
    OrchestrationContractError,
    _mapping,
    _positive_int,
    _reject_unknown,
)


RUNTIME_SUBSTRATE_VERSION = "runtime-substrate-v1"


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise OrchestrationContractError(
            f"{label} must be a non-empty NUL-free string"
        )
    return value


def _optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, label)


@dataclass(frozen=True)
class RuntimeSubstrate:
    version: str
    os_id: str
    os_version_id: str
    kernel_release: str
    architecture: str
    libc_name: str
    libc_version: str
    python_version: str
    runner_image_os: str | None
    runner_image_version: str | None
    openblas_package: str | None
    clock_ticks_per_second: int
    complete: bool

    def __post_init__(self) -> None:
        if self.version != RUNTIME_SUBSTRATE_VERSION:
            raise OrchestrationContractError(
                f"unsupported runtime substrate version: {self.version!r}"
            )
        for name in (
            "os_id",
            "os_version_id",
            "kernel_release",
            "architecture",
            "libc_name",
            "libc_version",
            "python_version",
        ):
            object.__setattr__(
                self, name, _required_string(getattr(self, name), name)
            )
        for name in (
            "runner_image_os",
            "runner_image_version",
            "openblas_package",
        ):
            object.__setattr__(
                self, name, _optional_string(getattr(self, name), name)
            )
        _positive_int(
            self.clock_ticks_per_second, "clock_ticks_per_second"
        )
        if not isinstance(self.complete, bool):
            raise OrchestrationContractError("complete must be boolean")
        expected_complete = bool(
            self.runner_image_os
            and self.runner_image_version
            and self.openblas_package
        )
        if self.complete != expected_complete:
            raise OrchestrationContractError(
                "runtime substrate complete flag contradicts observations"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "version": self.version,
            "os_id": self.os_id,
            "os_version_id": self.os_version_id,
            "kernel_release": self.kernel_release,
            "architecture": self.architecture,
            "libc_name": self.libc_name,
            "libc_version": self.libc_version,
            "python_version": self.python_version,
            "runner_image_os": self.runner_image_os,
            "runner_image_version": self.runner_image_version,
            "openblas_package": self.openblas_package,
            "clock_ticks_per_second": self.clock_ticks_per_second,
            "complete": self.complete,
            "authority": {
                "resource_context": True,
                "resource_authorization": False,
                "outward_move": False,
            },
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RuntimeSubstrate":
        raw = _mapping(raw, "runtime substrate")
        if raw.get("schema_version") != ORCHESTRATION_SCHEMA_VERSION:
            raise OrchestrationContractError(
                "unsupported runtime substrate schema_version"
            )
        _reject_unknown(
            raw,
            {
                "schema_version",
                "version",
                "os_id",
                "os_version_id",
                "kernel_release",
                "architecture",
                "libc_name",
                "libc_version",
                "python_version",
                "runner_image_os",
                "runner_image_version",
                "openblas_package",
                "clock_ticks_per_second",
                "complete",
                "authority",
            },
            "runtime substrate",
        )
        if raw.get("authority") != {
            "resource_context": True,
            "resource_authorization": False,
            "outward_move": False,
        }:
            raise OrchestrationContractError(
                "runtime substrate authority marker is invalid"
            )
        return cls(
            version=raw.get("version"),
            os_id=raw.get("os_id"),
            os_version_id=raw.get("os_version_id"),
            kernel_release=raw.get("kernel_release"),
            architecture=raw.get("architecture"),
            libc_name=raw.get("libc_name"),
            libc_version=raw.get("libc_version"),
            python_version=raw.get("python_version"),
            runner_image_os=raw.get("runner_image_os"),
            runner_image_version=raw.get("runner_image_version"),
            openblas_package=raw.get("openblas_package"),
            clock_ticks_per_second=raw.get("clock_ticks_per_second"),
            complete=raw.get("complete"),
        )

    @classmethod
    def from_observation(
        cls,
        *,
        os_id: str,
        os_version_id: str,
        kernel_release: str,
        architecture: str,
        libc_name: str,
        libc_version: str,
        python_version: str,
        runner_image_os: str | None,
        runner_image_version: str | None,
        openblas_package: str | None,
        clock_ticks_per_second: int,
    ) -> "RuntimeSubstrate":
        return cls(
            version=RUNTIME_SUBSTRATE_VERSION,
            os_id=os_id,
            os_version_id=os_version_id,
            kernel_release=kernel_release,
            architecture=architecture,
            libc_name=libc_name,
            libc_version=libc_version,
            python_version=python_version,
            runner_image_os=runner_image_os,
            runner_image_version=runner_image_version,
            openblas_package=openblas_package,
            clock_ticks_per_second=clock_ticks_per_second,
            complete=bool(
                runner_image_os
                and runner_image_version
                and openblas_package
            ),
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())

    @property
    def substrate_id(self) -> str:
        return f"runtime-substrate/{self.digest[:20]}"
