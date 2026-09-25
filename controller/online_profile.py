"""ONLINE-2 real-network, CPU-only online qualification contracts.

ONLINE-2 composes the ONLINE-1 clock/deadline lifecycle with the independently
qualified LC0 BLAS/network reference without widening outward move authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from controller.strength_profile import (
    StrengthProfileError,
    validate_lock,
    validate_profile,
    validate_vendor_binding,
)

ONLINE_PROFILE_SCHEMA_VERSION = 1
BUNDLE_MANIFEST_SCHEMA_VERSION = 1
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
EXPECTED_INSTANCES = {
    "stockfish-anchor",
    "stockfish-shadow",
    "reckless-shadow",
    "lc0-shadow",
}


class OnlineProfileError(RuntimeError):
    """Raised when ONLINE-2 identity/resource claims are not explicit."""


def load_json(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OnlineProfileError(f"cannot load {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise OnlineProfileError(f"{path}: root must be an object")
    return data


def sha256_file(path: Path | str) -> str:
    path = Path(path)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        raise OnlineProfileError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _require_string(data: dict[str, Any], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise OnlineProfileError(f"{label}.{key} must be a non-empty string")
    return value


def _require_exact(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise OnlineProfileError(f"{label} mismatch: expected {expected!r}, got {actual!r}")


def validate_policy(policy: dict[str, Any], strength_profile: dict[str, Any]) -> None:
    if policy.get("schema_version") != ONLINE_PROFILE_SCHEMA_VERSION:
        raise OnlineProfileError("online CPU profile schema_version must be 1")
    if policy.get("qualification_tier") != "online-cpu-reference":
        raise OnlineProfileError("qualification_tier must be online-cpu-reference")
    _require_string(policy, "profile_id", "online profile")
    if policy.get("strength_campaign_eligible") is not False:
        raise OnlineProfileError("ONLINE-2 reference may not be strength-campaign eligible")
    if policy.get("bundle_root") != "build/online-cpu-reference":
        raise OnlineProfileError("ONLINE-2 bundle_root must be build/online-cpu-reference")
    if policy.get("runtime_config") != "config/allfather.online.cpu-reference.json":
        raise OnlineProfileError("ONLINE-2 runtime_config path mismatch")

    platform = policy.get("platform")
    if not isinstance(platform, dict):
        raise OnlineProfileError("online profile platform must be an object")
    for field in ("os", "architecture", "reference_os"):
        _require_string(platform, field, "platform")
    if platform["os"] != "linux" or platform["architecture"] != "x86_64":
        raise OnlineProfileError("ONLINE-2 v1 supports Linux x86_64 CPU hosts only")
    if platform["reference_os"] != "ubuntu-24.04":
        raise OnlineProfileError("ONLINE-2 reference OS must be ubuntu-24.04")

    builds = policy.get("builds")
    if not isinstance(builds, dict) or set(builds) != {"stockfish", "reckless", "lc0"}:
        raise OnlineProfileError("online profile builds must declare stockfish/reckless/lc0")
    stockfish, reckless, lc0 = builds["stockfish"], builds["reckless"], builds["lc0"]
    if not all(isinstance(item, dict) for item in (stockfish, reckless, lc0)):
        raise OnlineProfileError("online build declarations must be objects")
    if stockfish.get("arch") != "x86-64":
        raise OnlineProfileError("Stockfish ONLINE-2 build must use ARCH=x86-64")
    if reckless.get("target") != "x86_64-unknown-linux-gnu":
        raise OnlineProfileError("Reckless ONLINE-2 target triple mismatch")
    if reckless.get("target_cpu") != "x86-64":
        raise OnlineProfileError("Reckless ONLINE-2 target_cpu must be explicit x86-64")
    if reckless.get("rustflags_source") != "CARGO_ENCODED_RUSTFLAGS":
        raise OnlineProfileError("Reckless portable build must use CARGO_ENCODED_RUSTFLAGS")
    if lc0.get("backend") != "blas":
        raise OnlineProfileError("LC0 ONLINE-2 backend must be blas")
    if lc0.get("strength_profile_id") != strength_profile.get("profile_id"):
        raise OnlineProfileError("ONLINE-2 LC0 strength profile binding mismatch")

    expected_environment = {
        "lc0-shadow": {
            "OPENBLAS_NUM_THREADS": "1",
            "GOTO_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
        }
    }
    _require_exact(policy.get("process_environment"), expected_environment, "process_environment")
    if policy.get("clock_policy", {}).get("policy") != "clock_envelope_v1":
        raise OnlineProfileError("ONLINE-2 must retain clock_envelope_v1")
    if policy.get("budget", {}).get("gpu_ms") != 0:
        raise OnlineProfileError("ONLINE-2 v1 is CPU-only")
    routing = policy.get("routing_policy")
    if not isinstance(routing, dict):
        raise OnlineProfileError("routing_policy must be an object")
    if routing.get("policy") != "conservative_v1" or routing.get("calibration") is not None:
        raise OnlineProfileError("ONLINE-2 must retain fail-closed conservative_v1 routing")
    expected_resource = {
        "enabled": True,
        "provider": "linux-procfs-v1",
        "require_cpu_for_claim": True,
        "require_gpu_for_claim": False,
        "record_memory": True,
    }
    _require_exact(policy.get("resource_measurement"), expected_resource, "resource_measurement")


def validate_runtime_config(
    config: dict[str, Any],
    lock: dict[str, Any],
    strength_profile: dict[str, Any],
    policy: dict[str, Any],
    vendor: dict[str, Any],
) -> None:
    try:
        validate_lock(lock, require_frozen=True)
        validate_profile(strength_profile)
        validate_vendor_binding(lock, vendor)
    except StrengthProfileError as exc:
        raise OnlineProfileError(str(exc)) from exc
    validate_policy(policy, strength_profile)

    if config.get("schema_version") != 2 or config.get("mode") != "active":
        raise OnlineProfileError("ONLINE-2 runtime must be schema v2 active mode")
    if config.get("anchor") != "stockfish-anchor":
        raise OnlineProfileError("ONLINE-2 anchor must be stockfish-anchor")
    for forbidden in ("verification", "refinement", "crossfeed", "counterfactual", "hybrid_authority"):
        if forbidden in config:
            raise OnlineProfileError(f"ONLINE-2 must not enable {forbidden}")

    _require_exact(config.get("budget"), policy["budget"], "budget")
    _require_exact(config.get("routing"), policy["routing_policy"], "routing")
    _require_exact(config.get("resource_measurement"), policy["resource_measurement"], "resource_measurement")
    _require_exact(config.get("online_time"), policy["clock_policy"], "online_time")

    shadow = config.get("shadow")
    if not isinstance(shadow, dict):
        raise OnlineProfileError("ONLINE-2 runtime requires shadow settings")
    if shadow.get("owners") != ["stockfish", "reckless", "lc0"]:
        raise OnlineProfileError("ONLINE-2 shadow owners changed")
    if shadow.get("instance_by_owner") != {
        "stockfish": "stockfish-shadow",
        "reckless": "reckless-shadow",
        "lc0": "lc0-shadow",
    }:
        raise OnlineProfileError("ONLINE-2 owner/instance mapping changed")
    if shadow.get("oracle") != "stockfish-shadow":
        raise OnlineProfileError("ONLINE-2 legal-root oracle must remain stockfish-shadow")
    if shadow.get("partition") != "root_index_modulo":
        raise OnlineProfileError("ONLINE-2 partition method changed")
    if shadow.get("lc0_score_type") != strength_profile["runtime"]["ScoreType"]:
        raise OnlineProfileError("ONLINE-2 LC0 score semantics differ from real-inference profile")

    instances = config.get("instances")
    if not isinstance(instances, dict) or set(instances) != EXPECTED_INSTANCES:
        raise OnlineProfileError("ONLINE-2 must declare exactly four engine instances")
    bundle_root = policy["bundle_root"]
    expected_binaries = {
        "stockfish-anchor": f"{bundle_root}/{policy['builds']['stockfish']['artifact']}",
        "stockfish-shadow": f"{bundle_root}/{policy['builds']['stockfish']['artifact']}",
        "reckless-shadow": f"{bundle_root}/{policy['builds']['reckless']['artifact']}",
        "lc0-shadow": f"{bundle_root}/{policy['builds']['lc0']['artifact']}",
    }
    for name, expected_binary in expected_binaries.items():
        raw = instances[name]
        if not isinstance(raw, dict):
            raise OnlineProfileError(f"ONLINE-2 instance {name} must be an object")
        if raw.get("binary") != expected_binary:
            raise OnlineProfileError(f"ONLINE-2 instance {name} binary is not isolated bundle artifact")
        if "fallback_glob" in raw:
            raise OnlineProfileError(f"ONLINE-2 instance {name} may not use fallback_glob")
        _require_exact(
            raw.get("environment", {}),
            policy["process_environment"].get(name, {}),
            f"instances.{name}.environment",
        )

    for name in ("stockfish-anchor", "stockfish-shadow"):
        options = instances[name].get("options")
        if not isinstance(options, dict):
            raise OnlineProfileError(f"{name} options missing")
        if options.get("Threads") != 1 or options.get("Hash") != 16:
            raise OnlineProfileError(f"{name} must remain one-thread Hash=16")
        if options.get("UCI_Chess960") is not False:
            raise OnlineProfileError(f"{name} startup Chess960 must remain false")
    if instances["stockfish-anchor"]["options"].get("MultiPV") != 1:
        raise OnlineProfileError("stockfish-anchor MultiPV must be 1")
    if instances["stockfish-shadow"]["options"].get("MultiPV") != 3:
        raise OnlineProfileError("stockfish-shadow MultiPV must be 3")

    expected_reckless = {
        "Threads": 1, "Hash": 16, "MultiPV": 3,
        "Minimal": False, "UCI_Chess960": False,
    }
    _require_exact(instances["reckless-shadow"].get("options"), expected_reckless, "reckless-shadow.options")

    lc0 = instances["lc0-shadow"].get("options")
    if not isinstance(lc0, dict):
        raise OnlineProfileError("lc0-shadow options missing")
    for name, expected in strength_profile["runtime"].items():
        if lc0.get(name) != expected:
            raise OnlineProfileError(
                f"ONLINE-2 LC0 option {name}={lc0.get(name)!r} differs from qualified value {expected!r}"
            )
    expected_weights = f"{bundle_root}/{policy['builds']['lc0']['network_artifact']}"
    if lc0.get("WeightsFile") != expected_weights:
        raise OnlineProfileError(f"ONLINE-2 LC0 WeightsFile must be {expected_weights!r}")
    if lc0.get("UCI_Chess960") is not False:
        raise OnlineProfileError("ONLINE-2 LC0 startup Chess960 must remain false")


def validate_bundle_manifest(
    manifest: dict[str, Any],
    policy: dict[str, Any],
    vendor: dict[str, Any],
    lc0_lock: dict[str, Any],
) -> None:
    if manifest.get("schema_version") != BUNDLE_MANIFEST_SCHEMA_VERSION:
        raise OnlineProfileError("online build manifest schema_version must be 1")
    if manifest.get("profile_id") != policy.get("profile_id"):
        raise OnlineProfileError("online build manifest profile_id mismatch")
    source = manifest.get("source_commit")
    if not isinstance(source, str) or _HEX40.fullmatch(source) is None:
        raise OnlineProfileError("online build manifest source_commit must be 40-hex")
    _require_exact(manifest.get("builds"), policy.get("builds"), "build manifest builds")
    _require_exact(manifest.get("runtime_environment"), policy.get("process_environment"), "build manifest runtime_environment")
    expected_vendor = {
        family: {
            "commit": vendor["engines"][family]["commit"],
            "tree": vendor["engines"][family]["tree"],
        }
        for family in ("stockfish", "reckless", "lc0")
    }
    _require_exact(manifest.get("vendor"), expected_vendor, "build manifest vendor")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise OnlineProfileError("online build manifest artifacts must be an object")
    engines, networks = artifacts.get("engines"), artifacts.get("networks")
    if not isinstance(engines, dict) or not isinstance(networks, dict):
        raise OnlineProfileError("online build manifest engine/network artifacts missing")
    for family in ("stockfish", "reckless", "lc0"):
        record = engines.get(family)
        if not isinstance(record, dict) or record.get("path") != policy["builds"][family]["artifact"]:
            raise OnlineProfileError(f"{family} build artifact path mismatch")
        if not isinstance(record.get("size"), int) or record["size"] <= 0:
            raise OnlineProfileError(f"{family} build artifact size invalid")
        if not isinstance(record.get("sha256"), str) or _HEX64.fullmatch(record["sha256"]) is None:
            raise OnlineProfileError(f"{family} build artifact sha256 invalid")

    expected_networks = {
        "stockfish": vendor["engines"]["stockfish"]["artifacts"]["default_nnue"],
        "reckless": vendor["engines"]["reckless"]["artifacts"]["default_nnue"],
        "lc0": lc0_lock["network"],
    }
    for family, expected in expected_networks.items():
        record = networks.get(family)
        if not isinstance(record, dict) or record.get("path") != policy["builds"][family]["network_artifact"]:
            raise OnlineProfileError(f"{family} network artifact path mismatch")
        if record.get("sha256") != expected["sha256"]:
            raise OnlineProfileError(f"{family} network sha256 differs from frozen lock")
        expected_size = expected.get("size", expected.get("expected_size_bytes"))
        if record.get("size") != expected_size:
            raise OnlineProfileError(f"{family} network size differs from frozen lock")
    if manifest["builds"]["reckless"].get("target_cpu") != "x86-64":
        raise OnlineProfileError("online build manifest may not contain native Reckless codegen")


def verify_bundle_files(root: Path | str, manifest: dict[str, Any]) -> dict[str, dict[str, dict[str, object]]]:
    root = Path(root).resolve()
    verified: dict[str, dict[str, dict[str, object]]] = {"engines": {}, "networks": {}}
    artifacts = manifest.get("artifacts") or {}
    for group in ("engines", "networks"):
        records = artifacts.get(group)
        if not isinstance(records, dict):
            raise OnlineProfileError(f"bundle manifest {group} records missing")
        for name, record in records.items():
            if not isinstance(record, dict):
                raise OnlineProfileError(f"bundle artifact {group}.{name} malformed")
            rel = record.get("path")
            if not isinstance(rel, str) or not rel:
                raise OnlineProfileError(f"bundle artifact {group}.{name} path missing")
            rel_path = Path(rel)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                raise OnlineProfileError(f"bundle artifact {group}.{name} escapes bundle root")
            path = (root / rel_path).resolve()
            if root not in path.parents:
                raise OnlineProfileError(f"bundle artifact {group}.{name} escapes bundle root")
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise OnlineProfileError(f"bundle artifact missing: {path}: {exc}") from exc
            digest = sha256_file(path)
            if size != record.get("size") or digest != record.get("sha256"):
                raise OnlineProfileError(f"bundle artifact {group}.{name} byte identity mismatch")
            verified[group][name] = {"path": str(path), "size": size, "sha256": digest}
    return verified
