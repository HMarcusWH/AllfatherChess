"""Runtime-substrate identity for host-bound qualification evidence.

HostCapabilities describes where work can run. RuntimeSubstrate describes the
software substrate that a dynamically linked engine actually executes against.
It deliberately excludes transient load/pressure and chess-policy settings.
"""

from __future__ import annotations

import hashlib
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from controller.decision import canonical_digest
from controller.resource_profiles import (
    ORCHESTRATION_SCHEMA_VERSION,
    OrchestrationContractError,
    _mapping,
    _reject_unknown,
    _safe_id,
    _sha256,
)


RUNTIME_SUBSTRATE_VERSION = "runtime-substrate-v1"
_DEFAULT_PACKAGES = (
    "libopenblas-dev",
    "libopenblas0-pthread",
    "libstdc++6",
    "libgcc-s1",
    "libc6",
)


def _read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        result[key] = value.strip().strip('"')
    return result


def _command(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            args, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class PackageIdentity:
    name: str
    version: str

    def __post_init__(self) -> None:
        _safe_id(self.name, "package name")
        if not isinstance(self.version, str) or not self.version or "\x00" in self.version:
            raise OrchestrationContractError(
                "package version must be a non-empty NUL-free string"
            )

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PackageIdentity":
        raw = _mapping(raw, "package identity")
        _reject_unknown(raw, {"name", "version"}, "package identity")
        return cls(name=raw.get("name"), version=raw.get("version"))


@dataclass(frozen=True)
class LinkedLibraryIdentity:
    soname: str
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.soname, str) or not self.soname or "\x00" in self.soname:
            raise OrchestrationContractError(
                "linked-library soname must be a non-empty NUL-free string"
            )
        _sha256(self.sha256, f"linked library {self.soname} sha256")

    def as_dict(self) -> dict[str, Any]:
        return {"soname": self.soname, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "LinkedLibraryIdentity":
        raw = _mapping(raw, "linked library identity")
        _reject_unknown(raw, {"soname", "sha256"}, "linked library identity")
        return cls(soname=raw.get("soname"), sha256=raw.get("sha256"))


@dataclass(frozen=True)
class RuntimeSubstrate:
    version: str
    os_id: str
    os_version: str
    kernel_release: str
    libc_name: str
    libc_version: str
    packages: tuple[PackageIdentity, ...]
    linked_libraries: tuple[LinkedLibraryIdentity, ...]
    complete: bool
    faults: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.version != RUNTIME_SUBSTRATE_VERSION:
            raise OrchestrationContractError(
                f"unsupported runtime substrate version: {self.version!r}"
            )
        for name in ("os_id", "os_version", "kernel_release", "libc_name", "libc_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or "\x00" in value:
                raise OrchestrationContractError(
                    f"{name} must be a non-empty NUL-free string"
                )

        packages = tuple(self.packages)
        if not all(isinstance(item, PackageIdentity) for item in packages):
            raise OrchestrationContractError(
                "packages must contain PackageIdentity values"
            )
        package_names = [item.name for item in packages]
        if len(package_names) != len(set(package_names)):
            raise OrchestrationContractError("package names must be unique")
        object.__setattr__(
            self, "packages", tuple(sorted(packages, key=lambda item: item.name))
        )

        libraries = tuple(self.linked_libraries)
        if not all(isinstance(item, LinkedLibraryIdentity) for item in libraries):
            raise OrchestrationContractError(
                "linked_libraries must contain LinkedLibraryIdentity values"
            )
        sonames = [item.soname for item in libraries]
        if len(sonames) != len(set(sonames)):
            raise OrchestrationContractError("linked-library sonames must be unique")
        object.__setattr__(
            self,
            "linked_libraries",
            tuple(sorted(libraries, key=lambda item: item.soname)),
        )

        if not isinstance(self.complete, bool):
            raise OrchestrationContractError("runtime substrate complete must be boolean")
        expected_complete = bool(
            self.os_id != "unknown"
            and self.os_version != "unknown"
            and self.kernel_release != "unknown"
            and self.libc_name != "unknown"
            and self.libc_version != "unknown"
            and set(package_names) >= set(_DEFAULT_PACKAGES)
            and self.linked_libraries
        )
        if self.complete != expected_complete:
            raise OrchestrationContractError(
                "runtime substrate complete does not match retained evidence"
            )

        faults = tuple(self.faults)
        if any(not isinstance(item, str) or not item or "\x00" in item for item in faults):
            raise OrchestrationContractError(
                "runtime substrate faults must be non-empty strings"
            )
        object.__setattr__(self, "faults", faults)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORCHESTRATION_SCHEMA_VERSION,
            "version": self.version,
            "os_id": self.os_id,
            "os_version": self.os_version,
            "kernel_release": self.kernel_release,
            "libc_name": self.libc_name,
            "libc_version": self.libc_version,
            "packages": [item.as_dict() for item in self.packages],
            "linked_libraries": [
                item.as_dict() for item in self.linked_libraries
            ],
            "complete": self.complete,
            "faults": list(self.faults),
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
        allowed = {
            "schema_version",
            "version",
            "os_id",
            "os_version",
            "kernel_release",
            "libc_name",
            "libc_version",
            "packages",
            "linked_libraries",
            "complete",
            "faults",
            "authority",
        }
        _reject_unknown(raw, allowed, "runtime substrate")
        if raw.get("authority") != {
            "resource_context": True,
            "resource_authorization": False,
            "outward_move": False,
        }:
            raise OrchestrationContractError(
                "runtime substrate authority marker is invalid"
            )
        packages = raw.get("packages", [])
        libraries = raw.get("linked_libraries", [])
        faults = raw.get("faults", [])
        if (
            not isinstance(packages, list)
            or not isinstance(libraries, list)
            or not isinstance(faults, list)
        ):
            raise OrchestrationContractError(
                "runtime substrate collections must be arrays"
            )
        return cls(
            version=raw.get("version"),
            os_id=raw.get("os_id"),
            os_version=raw.get("os_version"),
            kernel_release=raw.get("kernel_release"),
            libc_name=raw.get("libc_name"),
            libc_version=raw.get("libc_version"),
            packages=tuple(PackageIdentity.from_dict(item) for item in packages),
            linked_libraries=tuple(
                LinkedLibraryIdentity.from_dict(item) for item in libraries
            ),
            complete=raw.get("complete"),
            faults=tuple(faults),
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())

    @property
    def runtime_substrate_id(self) -> str | None:
        if not self.complete:
            return None
        return f"runtime-substrate/{self.digest[:20]}"


_LDD_LINE = re.compile(
    r"^\s*(?P<soname>\S+)\s+=>\s+(?P<path>/\S+)\s+\(0x[0-9a-fA-F]+\)\s*$"
)


def capture_runtime_substrate(
    binary: Path,
    *,
    package_names: tuple[str, ...] = _DEFAULT_PACKAGES,
    os_release_reader: Callable[[], dict[str, str]] = _read_os_release,
    package_reader: Callable[[str], str | None] | None = None,
    ldd_reader: Callable[[Path], str | None] | None = None,
    file_hasher: Callable[[Path], str] = _sha256_file,
) -> RuntimeSubstrate:
    binary = Path(binary)
    faults: list[str] = []

    release = os_release_reader()
    os_id = release.get("ID") or "unknown"
    os_version = release.get("VERSION_ID") or "unknown"
    kernel_release = platform.release() or "unknown"
    libc_name, libc_version = platform.libc_ver()
    libc_name = libc_name or "unknown"
    libc_version = libc_version or "unknown"

    if package_reader is None:
        def package_reader(name: str) -> str | None:
            return _command("dpkg-query", "-W", "-f=${Version}", name)

    packages: list[PackageIdentity] = []
    for name in package_names:
        version = package_reader(name)
        if version:
            packages.append(PackageIdentity(name, version))
        else:
            faults.append(f"package:{name}:missing")

    if ldd_reader is None:
        def ldd_reader(path: Path) -> str | None:
            return _command("ldd", str(path))

    libraries: list[LinkedLibraryIdentity] = []
    ldd = ldd_reader(binary)
    if not ldd:
        faults.append("ldd:unavailable")
    else:
        seen: set[str] = set()
        for raw in ldd.splitlines():
            match = _LDD_LINE.match(raw)
            if match is None:
                continue
            soname = match.group("soname")
            path = Path(match.group("path"))
            if soname in seen:
                continue
            seen.add(soname)
            try:
                digest = file_hasher(path)
            except OSError as exc:
                faults.append(f"library:{soname}:OSError:{exc.errno}")
                continue
            libraries.append(LinkedLibraryIdentity(soname, digest))

    complete = bool(
        os_id != "unknown"
        and os_version != "unknown"
        and kernel_release != "unknown"
        and libc_name != "unknown"
        and libc_version != "unknown"
        and len(packages) == len(package_names)
        and libraries
    )
    return RuntimeSubstrate(
        version=RUNTIME_SUBSTRATE_VERSION,
        os_id=os_id,
        os_version=os_version,
        kernel_release=kernel_release,
        libc_name=libc_name,
        libc_version=libc_version,
        packages=tuple(packages),
        linked_libraries=tuple(libraries),
        complete=complete,
        faults=tuple(faults),
    )
