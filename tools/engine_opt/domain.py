"""Canonical execution-domain evidence for ENGINE-OPT qualification.

This module consumes the immutable J2 HostCapabilities/RuntimeSubstrate
observations.  It deliberately distinguishes a reusable qualification domain
from an exact-host binding: hosted runners may expose a complete exact host
identity while ancestor cgroup facts remain unavailable, in which case the
evidence is valid only for the exact observed worker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from controller.decision import canonical_digest
from controller.host_capabilities import HostCapabilities
from controller.runtime_substrate import RuntimeSubstrate


EXECUTION_DOMAIN_VERSION = "engine-opt-execution-domain-v1"
_HEX40 = re.compile(r"^[0-9a-f]{40}$")


class ExecutionDomainError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ExecutionDomainError(message)


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_object(path: Path | str) -> dict[str, Any]:
    def reject_constant(value: str):
        raise ExecutionDomainError(f"{path}: non-finite JSON constant: {value}")

    def unique_object(pairs):
        out: dict[str, Any] = {}
        for key, value in pairs:
            require(key not in out, f"{path}: duplicate JSON key: {key}")
            out[key] = value
        return out

    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionDomainError(f"{path}: cannot load JSON: {exc}") from exc
    require(isinstance(value, dict), f"{path}: JSON root must be an object")
    return value


def _sealed(core: Mapping[str, Any]) -> dict[str, Any]:
    clean = dict(core)
    clean.pop("content_sha256", None)
    return {**clean, "content_sha256": canonical_digest(clean)}


def _validate_probe(probe: Mapping[str, Any]) -> tuple[HostCapabilities, RuntimeSubstrate, str]:
    raw_host = probe.get("host_capabilities")
    raw_runtime = probe.get("runtime_substrate")
    require(isinstance(raw_host, Mapping), "probe HostCapabilities object missing")
    require(isinstance(raw_runtime, Mapping), "probe RuntimeSubstrate object missing")
    try:
        host = HostCapabilities.from_dict(raw_host)
        runtime = RuntimeSubstrate.from_dict(raw_runtime)
    except Exception as exc:
        raise ExecutionDomainError(f"malformed J2 host evidence: {exc}") from exc

    require(
        probe.get("host_capability_id") == host.capability_id,
        "probe host capability id does not reconstruct",
    )
    require(
        probe.get("host_qualification_domain_id") == host.qualification_domain_id,
        "probe host qualification-domain id does not reconstruct",
    )
    require(
        probe.get("host_qualification_domain_digest") == host.qualification_domain_digest,
        "probe host qualification-domain digest does not reconstruct",
    )
    require(
        probe.get("runtime_substrate_id") == runtime.substrate_id,
        "probe runtime-substrate id does not reconstruct",
    )
    require(
        probe.get("runtime_substrate_digest") == runtime.digest,
        "probe runtime-substrate digest does not reconstruct",
    )
    require(runtime.complete, "runtime substrate is incomplete")

    source = probe.get("commit_sha")
    require(
        isinstance(source, str) and _HEX40.fullmatch(source) is not None,
        "probe source commit is missing or malformed",
    )
    return host, runtime, source


def execution_domain_from_probe(probe: Mapping[str, Any]) -> dict[str, Any]:
    host, runtime, source = _validate_probe(probe)
    if host.qualification_domain_complete:
        require(host.qualification_domain_id is not None, "complete host domain lacks id")
        require(host.qualification_domain_digest is not None, "complete host domain lacks digest")
        scope = "reusable_host_domain"
        host_binding_digest = host.qualification_domain_digest
    else:
        require(
            host.qualification_domain_id is None
            and host.qualification_domain_digest is None,
            "incomplete host domain may not carry a fabricated identity",
        )
        scope = "exact_host_observation"
        host_binding_digest = host.digest

    material = {
        "schema_version": 1,
        "version": EXECUTION_DOMAIN_VERSION,
        "binding_scope": scope,
        "host_binding_digest": host_binding_digest,
        "runtime_substrate_digest": runtime.digest,
    }
    digest = canonical_digest(material)
    core = {
        "schema_version": 1,
        "version": EXECUTION_DOMAIN_VERSION,
        "binding_scope": scope,
        "source_commit": source,
        "host_capabilities": host.as_dict(),
        "host_capability_id": host.capability_id,
        "host_capability_digest": host.digest,
        "host_qualification_domain_complete": host.qualification_domain_complete,
        "host_qualification_domain_id": host.qualification_domain_id,
        "host_qualification_domain_digest": host.qualification_domain_digest,
        "runtime_substrate": runtime.as_dict(),
        "runtime_substrate_complete": runtime.complete,
        "runtime_substrate_id": runtime.substrate_id,
        "runtime_substrate_digest": runtime.digest,
        "execution_domain_id": f"exec-domain/{digest[:20]}",
        "execution_domain_digest": digest,
        "generic_host_portability_eligible": scope == "reusable_host_domain",
        "claim_boundary": {
            "resource_context": True,
            "resource_authorization": False,
            "outward_move": False,
            "generic_host_portability": scope == "reusable_host_domain",
        },
    }
    return _sealed(core)


def validate_execution_domain(
    raw: Mapping[str, Any],
    *,
    expected_source_commit: str | None = None,
) -> dict[str, Any]:
    require(isinstance(raw, Mapping), "execution-domain evidence missing")
    require(raw.get("schema_version") == 1, "unsupported execution-domain schema")
    require(raw.get("version") == EXECUTION_DOMAIN_VERSION, "unsupported execution-domain version")

    allowed = {
        "schema_version",
        "version",
        "binding_scope",
        "source_commit",
        "host_capabilities",
        "host_capability_id",
        "host_capability_digest",
        "host_qualification_domain_complete",
        "host_qualification_domain_id",
        "host_qualification_domain_digest",
        "runtime_substrate",
        "runtime_substrate_complete",
        "runtime_substrate_id",
        "runtime_substrate_digest",
        "execution_domain_id",
        "execution_domain_digest",
        "generic_host_portability_eligible",
        "claim_boundary",
        "content_sha256",
    }
    require(set(raw) == allowed, "execution-domain fields differ from frozen schema")

    content_sha = raw.get("content_sha256")
    require(isinstance(content_sha, str) and re.fullmatch(r"[0-9a-f]{64}", content_sha) is not None, "execution-domain seal missing")
    core = dict(raw)
    core.pop("content_sha256", None)
    require(canonical_digest(core) == content_sha, "execution-domain seal does not verify")

    source = raw.get("source_commit")
    require(
        isinstance(source, str) and _HEX40.fullmatch(source) is not None,
        "execution-domain source commit is malformed",
    )
    if expected_source_commit is not None:
        require(source == expected_source_commit, "execution-domain source is not exact head")

    raw_host = raw.get("host_capabilities")
    raw_runtime = raw.get("runtime_substrate")
    require(isinstance(raw_host, Mapping), "retained HostCapabilities missing")
    require(isinstance(raw_runtime, Mapping), "retained RuntimeSubstrate missing")
    try:
        host = HostCapabilities.from_dict(raw_host)
        runtime = RuntimeSubstrate.from_dict(raw_runtime)
    except Exception as exc:
        raise ExecutionDomainError(f"retained execution-domain evidence is malformed: {exc}") from exc

    require(raw.get("host_capability_id") == host.capability_id, "host capability id mismatch")
    require(raw.get("host_capability_digest") == host.digest, "host capability digest mismatch")
    require(
        raw.get("host_qualification_domain_complete") == host.qualification_domain_complete,
        "host qualification-domain completeness mismatch",
    )
    require(
        raw.get("host_qualification_domain_id") == host.qualification_domain_id,
        "host qualification-domain id mismatch",
    )
    require(
        raw.get("host_qualification_domain_digest") == host.qualification_domain_digest,
        "host qualification-domain digest mismatch",
    )
    require(runtime.complete, "runtime substrate is incomplete")
    require(raw.get("runtime_substrate_complete") is True, "runtime-substrate completeness mismatch")
    require(raw.get("runtime_substrate_id") == runtime.substrate_id, "runtime-substrate id mismatch")
    require(raw.get("runtime_substrate_digest") == runtime.digest, "runtime-substrate digest mismatch")

    scope = raw.get("binding_scope")
    if host.qualification_domain_complete:
        require(scope == "reusable_host_domain", "complete host domain must use reusable scope")
        require(host.qualification_domain_digest is not None, "complete host domain lacks digest")
        host_binding_digest = host.qualification_domain_digest
    else:
        require(scope == "exact_host_observation", "incomplete host domain must use exact-host scope")
        require(
            host.qualification_domain_id is None and host.qualification_domain_digest is None,
            "incomplete host domain carries fabricated reusable identity",
        )
        host_binding_digest = host.digest

    material = {
        "schema_version": 1,
        "version": EXECUTION_DOMAIN_VERSION,
        "binding_scope": scope,
        "host_binding_digest": host_binding_digest,
        "runtime_substrate_digest": runtime.digest,
    }
    digest = canonical_digest(material)
    require(raw.get("execution_domain_digest") == digest, "execution-domain digest mismatch")
    require(raw.get("execution_domain_id") == f"exec-domain/{digest[:20]}", "execution-domain id mismatch")
    require(
        raw.get("generic_host_portability_eligible") is (scope == "reusable_host_domain"),
        "generic-host portability marker contradicts binding scope",
    )
    require(
        raw.get("claim_boundary")
        == {
            "resource_context": True,
            "resource_authorization": False,
            "outward_move": False,
            "generic_host_portability": scope == "reusable_host_domain",
        },
        "execution-domain authority/claim marker is invalid",
    )
    return dict(raw)


def load_execution_domain(
    path: Path | str,
    *,
    expected_source_commit: str | None = None,
) -> dict[str, Any]:
    return validate_execution_domain(
        _json_object(path),
        expected_source_commit=expected_source_commit,
    )


def require_same_execution_domain(
    named_domains: Mapping[str, Mapping[str, Any] | None],
    *,
    expected_source_commit: str | None = None,
) -> dict[str, Any]:
    require(bool(named_domains), "no execution-domain evidence supplied")
    verified: list[tuple[str, dict[str, Any]]] = []
    for name, raw in named_domains.items():
        require(isinstance(raw, Mapping), f"{name}: execution-domain evidence missing")
        verified.append(
            (
                name,
                validate_execution_domain(
                    raw,
                    expected_source_commit=expected_source_commit,
                ),
            )
        )
    digests = {row["execution_domain_digest"] for _, row in verified}
    require(len(digests) == 1, "claim-bearing evidence spans multiple execution domains")
    first = verified[0][1]
    return {
        "execution_domain_id": first["execution_domain_id"],
        "execution_domain_digest": first["execution_domain_digest"],
        "binding_scope": first["binding_scope"],
        "generic_host_portability_eligible": first["generic_host_portability_eligible"],
        "host_capability_id": first["host_capability_id"],
        "host_qualification_domain_id": first["host_qualification_domain_id"],
        "runtime_substrate_id": first["runtime_substrate_id"],
        "members": [
            {
                "name": name,
                "execution_domain_id": row["execution_domain_id"],
                "host_capability_id": row["host_capability_id"],
                "runtime_substrate_id": row["runtime_substrate_id"],
            }
            for name, row in verified
        ],
    }


def candidate_bundle_identity(
    bundle_root: Path | str,
    *,
    expected_source_commit: str | None = None,
) -> dict[str, Any]:
    root = Path(bundle_root)
    manifest_path = root / "build-manifest.json"
    manifest = _json_object(manifest_path)
    source = manifest.get("source_commit")
    if expected_source_commit is not None:
        require(source == expected_source_commit, "candidate bundle source is not exact head")
    artifacts = manifest.get("artifacts")
    require(isinstance(artifacts, Mapping), "candidate bundle artifacts missing")
    result: dict[str, dict[str, Any]] = {}
    for category in ("engines", "networks"):
        rows = artifacts.get(category)
        require(isinstance(rows, Mapping), f"candidate bundle {category} identities missing")
        require(set(rows) == {"stockfish", "reckless", "lc0"}, f"candidate bundle {category} set is incomplete")
        out: dict[str, Any] = {}
        for family in ("stockfish", "reckless", "lc0"):
            row = rows[family]
            require(isinstance(row, Mapping), f"{family} {category} identity malformed")
            relative = row.get("path")
            require(isinstance(relative, str) and relative, f"{family} {category} path missing")
            path = root / relative
            require(path.is_file(), f"{family} {category} artifact missing")
            observed_sha = sha256_file(path)
            observed_size = path.stat().st_size
            require(row.get("sha256") == observed_sha, f"{family} {category} SHA mismatch")
            require(row.get("size") == observed_size, f"{family} {category} size mismatch")
            out[family] = {"path": relative, "sha256": observed_sha, "size": observed_size}
        result[category] = out
    return {
        "source_commit": source,
        "source_tree": manifest.get("source_tree"),
        "build_manifest_sha256": sha256_file(manifest_path),
        "artifacts": result,
    }


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture")
    capture.add_argument("--probe", type=Path, required=True)
    capture.add_argument("--output", type=Path, required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--input", type=Path, required=True)
    validate.add_argument("--expected-source-commit")

    args = parser.parse_args()
    if args.command == "capture":
        payload = execution_domain_from_probe(_json_object(args.probe))
        _write(args.output, payload)
        print(
            json.dumps(
                {
                    "execution_domain_id": payload["execution_domain_id"],
                    "binding_scope": payload["binding_scope"],
                    "generic_host_portability_eligible": payload["generic_host_portability_eligible"],
                    "output": str(args.output),
                },
                sort_keys=True,
            )
        )
        return 0

    payload = load_execution_domain(
        args.input,
        expected_source_commit=args.expected_source_commit,
    )
    print(
        json.dumps(
            {
                "execution_domain_id": payload["execution_domain_id"],
                "binding_scope": payload["binding_scope"],
                "valid": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
