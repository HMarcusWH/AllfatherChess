"""LC0 real-inference qualification contracts.

This module validates configuration and provenance for strength-facing LC0
experiments.  It does not assert playing strength.  A qualified profile means
that the exact LC0 source, network bytes, backend, runtime options and observed
hardware/software environment are bound into a report.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from adapters.telemetry import SUPPORTED_SCORE_TYPES


LOCK_SCHEMA_VERSION = 1
PROFILE_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_BACKENDS = {"", "random", "<none>"}


class StrengthProfileError(RuntimeError):
    """Raised when a strength-facing LC0 profile is not fully explicit."""


def canonical_digest(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise StrengthProfileError(f"payload is not canonical JSON: {exc}") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_json(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StrengthProfileError(f"cannot load {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise StrengthProfileError(f"{path}: root must be an object")
    return data


def validate_lock(data: dict[str, Any], *, require_frozen: bool = False) -> None:
    if data.get("schema_version") != LOCK_SCHEMA_VERSION:
        raise StrengthProfileError("LC0 strength lock schema_version must be 1")
    profile_id = data.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id:
        raise StrengthProfileError("LC0 strength lock profile_id must be non-empty")
    status = data.get("qualification_status")
    if status not in {"candidate", "frozen"}:
        raise StrengthProfileError(
            "LC0 strength lock qualification_status must be candidate or frozen"
        )
    if require_frozen and status != "frozen":
        raise StrengthProfileError("LC0 strength lock is not frozen")

    engine = data.get("engine")
    if not isinstance(engine, dict) or engine.get("family") != "lc0":
        raise StrengthProfileError("LC0 strength lock engine.family must be lc0")
    for field in ("vendor_commit", "vendor_tree"):
        value = engine.get(field)
        if not isinstance(value, str) or HEX40.fullmatch(value) is None:
            raise StrengthProfileError(f"engine.{field} must be lowercase 40-hex")

    network = data.get("network")
    if not isinstance(network, dict):
        raise StrengthProfileError("LC0 strength lock network must be an object")
    training_id = network.get("training_id")
    if isinstance(training_id, bool) or not isinstance(training_id, int) or training_id <= 0:
        raise StrengthProfileError("network.training_id must be a positive integer")
    filename = network.get("filename")
    if (
        not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
    ):
        raise StrengthProfileError("network.filename must be a basename")
    parsed = urlparse(str(network.get("url") or ""))
    if parsed.scheme != "https" or not parsed.netloc:
        raise StrengthProfileError("network.url must be an absolute https URL")
    lookup_digest = network.get("training_sha256")
    if not isinstance(lookup_digest, str) or HEX64.fullmatch(lookup_digest) is None:
        raise StrengthProfileError(
            "network.training_sha256 must be lowercase 64-hex"
        )
    query_sha = parse_qs(parsed.query).get("sha", [])
    if query_sha != [lookup_digest]:
        raise StrengthProfileError(
            "network.url sha query must equal network.training_sha256 exactly"
        )
    digest = network.get("sha256")
    if not isinstance(digest, str) or HEX64.fullmatch(digest) is None:
        raise StrengthProfileError("network.sha256 must be lowercase 64-hex")
    size = network.get("expected_size_bytes")
    if status == "frozen":
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise StrengthProfileError(
                "frozen LC0 strength lock requires positive expected_size_bytes"
            )
    elif size is not None and (
        isinstance(size, bool) or not isinstance(size, int) or size <= 0
    ):
        raise StrengthProfileError(
            "candidate expected_size_bytes must be null or positive integer"
        )


def validate_vendor_binding(
    lock: dict[str, Any],
    vendor_lock: dict[str, Any],
) -> None:
    """Require the qualification source identity to equal the monorepo vendor lock."""

    validate_lock(lock)
    engines = vendor_lock.get("engines")
    if not isinstance(engines, dict) or not isinstance(engines.get("lc0"), dict):
        raise StrengthProfileError("vendor lock does not contain an LC0 engine entry")
    lc0 = engines["lc0"]
    engine = lock["engine"]
    if lc0.get("commit") != engine.get("vendor_commit"):
        raise StrengthProfileError(
            "LC0 strength lock vendor_commit does not match vendor.lock.json"
        )
    if lc0.get("tree") != engine.get("vendor_tree"):
        raise StrengthProfileError(
            "LC0 strength lock vendor_tree does not match vendor.lock.json"
        )


def validate_profile(data: dict[str, Any]) -> None:
    if data.get("schema_version") != PROFILE_SCHEMA_VERSION:
        raise StrengthProfileError("LC0 strength profile schema_version must be 1")
    if not isinstance(data.get("profile_id"), str) or not data["profile_id"]:
        raise StrengthProfileError("LC0 strength profile profile_id must be non-empty")
    if data.get("qualification_tier") != "real-inference-reference":
        raise StrengthProfileError(
            "qualification_tier must be real-inference-reference in profile v1"
        )
    if not isinstance(data.get("strength_campaign_eligible"), bool):
        raise StrengthProfileError("strength_campaign_eligible must be boolean")

    hardware = data.get("hardware_policy")
    if not isinstance(hardware, dict):
        raise StrengthProfileError("hardware_policy must be an object")
    for field in ("runner_class", "os", "architecture", "accelerator_kind"):
        if not isinstance(hardware.get(field), str) or not hardware[field]:
            raise StrengthProfileError(f"hardware_policy.{field} must be non-empty")
    if hardware.get("cpu_model") != "record-and-bind":
        raise StrengthProfileError("profile v1 requires cpu_model=record-and-bind")
    if hardware.get("memory") != "record-and-bind":
        raise StrengthProfileError("profile v1 requires memory=record-and-bind")

    build = data.get("build")
    if not isinstance(build, dict):
        raise StrengthProfileError("build must be an object")
    backend = build.get("backend")
    if not isinstance(backend, str) or backend in FORBIDDEN_BACKENDS:
        raise StrengthProfileError("build.backend must name a real inference backend")
    options = build.get("meson_options")
    if not isinstance(options, list) or not all(
        isinstance(item, str) and item for item in options
    ):
        raise StrengthProfileError("build.meson_options must be non-empty strings")
    if not any(item == "-Dbuild_backends=true" for item in options):
        raise StrengthProfileError("strength build must enable neural backends")
    packages = build.get("required_packages")
    if not isinstance(packages, list) or not all(
        isinstance(item, str) and item for item in packages
    ):
        raise StrengthProfileError("build.required_packages must be string array")

    runtime = data.get("runtime")
    if not isinstance(runtime, dict):
        raise StrengthProfileError("runtime must be an object")
    if runtime.get("Backend") != backend:
        raise StrengthProfileError("runtime.Backend must equal build.backend")
    if not isinstance(runtime.get("BackendOptions"), str):
        raise StrengthProfileError("runtime.BackendOptions must be explicit string")
    score_type = runtime.get("ScoreType")
    if score_type not in SUPPORTED_SCORE_TYPES:
        raise StrengthProfileError(
            f"runtime.ScoreType must be one of {sorted(SUPPORTED_SCORE_TYPES)}"
        )
    for field in ("NNCacheSize", "TaskWorkers"):
        value = runtime.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise StrengthProfileError(f"runtime.{field} must be non-negative integer")
    for field in ("MinibatchSize", "MaxConcurrentSearchers", "Threads", "MultiPV"):
        value = runtime.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise StrengthProfileError(f"runtime.{field} must be positive integer")

    warmup = data.get("warmup")
    if not isinstance(warmup, dict):
        raise StrengthProfileError("warmup must be an object")
    if warmup.get("policy") != "one-fixed-node-search":
        raise StrengthProfileError(
            "profile v1 warmup.policy must be one-fixed-node-search"
        )
    nodes = warmup.get("nodes")
    if isinstance(nodes, bool) or not isinstance(nodes, int) or nodes <= 0:
        raise StrengthProfileError("warmup.nodes must be positive integer")


def validate_runtime_config(
    config: dict[str, Any],
    lock: dict[str, Any],
    profile: dict[str, Any],
) -> None:
    """Validate the JSON config without requiring built binaries or network bytes."""

    validate_lock(lock)
    validate_profile(profile)
    if lock.get("profile_id") != profile.get("profile_id"):
        raise StrengthProfileError("lock/profile profile_id mismatch")
    if config.get("schema_version") != 2 or config.get("mode") != "shadow":
        raise StrengthProfileError("strength runtime must be schema v2 shadow mode")
    shadow = config.get("shadow")
    instances = config.get("instances")
    if not isinstance(shadow, dict) or not isinstance(instances, dict):
        raise StrengthProfileError("strength runtime requires shadow and instances")
    mapping = shadow.get("instance_by_owner")
    if not isinstance(mapping, dict) or not isinstance(mapping.get("lc0"), str):
        raise StrengthProfileError("strength runtime must map LC0 shadow owner")
    instance_name = mapping["lc0"]
    instance = instances.get(instance_name)
    if not isinstance(instance, dict) or instance.get("family") != "lc0":
        raise StrengthProfileError("LC0 shadow instance is missing")
    if instance.get("role") != "shadow":
        raise StrengthProfileError("LC0 strength instance must remain shadow role")

    options = instance.get("options")
    if not isinstance(options, dict):
        raise StrengthProfileError("LC0 strength options must be an object")
    runtime = profile["runtime"]
    for name, expected in runtime.items():
        if options.get(name) != expected:
            raise StrengthProfileError(
                f"LC0 option {name}={options.get(name)!r} does not match "
                f"qualified profile value {expected!r}"
            )
    network = lock["network"]
    expected_weights = f"build/artifacts/lc0/{network['filename']}"
    if options.get("WeightsFile") != expected_weights:
        raise StrengthProfileError(
            f"WeightsFile must be {expected_weights!r}, got "
            f"{options.get('WeightsFile')!r}"
        )
    if options.get("Backend") in FORBIDDEN_BACKENDS:
        raise StrengthProfileError("strength runtime may not use random/empty backend")
    if shadow.get("lc0_score_type") != options.get("ScoreType"):
        raise StrengthProfileError(
            "shadow.lc0_score_type must equal LC0 ScoreType option exactly"
        )


def verify_network_file(
    path: Path | str,
    lock: dict[str, Any],
    *,
    require_frozen: bool = True,
) -> dict[str, Any]:
    validate_lock(lock, require_frozen=require_frozen)
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise StrengthProfileError(f"network file is not readable: {path}: {exc}") from exc
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    network = lock["network"]
    expected_size = network.get("expected_size_bytes")
    if expected_size is not None and size != expected_size:
        raise StrengthProfileError(
            f"network size mismatch: expected {expected_size}, got {size}"
        )
    if digest != network["sha256"]:
        raise StrengthProfileError(
            f"network sha256 mismatch: expected {network['sha256']}, got {digest}"
        )
    return {
        "path": str(path.resolve()),
        "size": size,
        "sha256": digest,
        "filename": path.name,
    }


@dataclass(frozen=True)
class QualificationReport:
    profile_id: str
    commit_sha: str
    binary: dict[str, Any]
    network: dict[str, Any]
    requested_backend: str
    observed_backend: str
    runtime_options: dict[str, Any]
    hardware: dict[str, Any]
    warmup_bestmove: str
    qualification_bestmove: str
    telemetry_score_semantics: str

    def as_dict(self) -> dict[str, Any]:
        core = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "commit_sha": self.commit_sha,
            "binary": self.binary,
            "network": self.network,
            "requested_backend": self.requested_backend,
            "observed_backend": self.observed_backend,
            "runtime_options": self.runtime_options,
            "hardware": self.hardware,
            "warmup_bestmove": self.warmup_bestmove,
            "qualification_bestmove": self.qualification_bestmove,
            "telemetry_score_semantics": self.telemetry_score_semantics,
            "claim": (
                "Real-network, real-backend LC0 inference qualification only. "
                "This report does not establish Elo, move superiority or "
                "equal-resource strength."
            ),
        }
        return {
            **core,
            "report_id": f"lc0q-{canonical_digest(core)[:16]}",
        }
