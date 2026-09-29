"""Execution-domain validation shared by ENGINE-OPT qualification."""

from __future__ import annotations

from typing import Any, Mapping

from controller.host_capabilities import HostCapabilities
from controller.runtime_substrate import RuntimeSubstrate


class ExecutionDomainError(ValueError):
    pass


def _verified_domain(name: str, domain: Mapping[str, Any] | None):
    if not isinstance(domain, Mapping):
        raise ExecutionDomainError(f"{name}: execution-domain evidence missing")
    if domain.get("complete") is not True:
        raise ExecutionDomainError(
            f"{name}: execution-domain evidence incomplete"
        )

    raw_host = domain.get("host_capabilities")
    raw_runtime = domain.get("runtime_substrate")
    if not isinstance(raw_host, Mapping):
        raise ExecutionDomainError(
            f"{name}: retained HostCapabilities object missing"
        )
    if not isinstance(raw_runtime, Mapping):
        raise ExecutionDomainError(
            f"{name}: retained RuntimeSubstrate object missing"
        )

    try:
        host = HostCapabilities.from_dict(raw_host)
        runtime = RuntimeSubstrate.from_dict(raw_runtime)
    except Exception as exc:
        raise ExecutionDomainError(
            f"{name}: malformed retained execution-domain evidence: {exc}"
        ) from exc

    if domain.get("host_capability_id") != host.capability_id:
        raise ExecutionDomainError(
            f"{name}: host capability id does not match retained facts"
        )
    if (
        domain.get("host_qualification_domain_id")
        != host.qualification_domain_id
    ):
        raise ExecutionDomainError(
            f"{name}: host qualification domain id does not match retained facts"
        )
    if domain.get("runtime_substrate_id") != runtime.runtime_substrate_id:
        raise ExecutionDomainError(
            f"{name}: runtime substrate id does not match retained facts"
        )
    if host.qualification_domain_id is None:
        raise ExecutionDomainError(
            f"{name}: host qualification domain is incomplete"
        )
    if runtime.runtime_substrate_id is None:
        raise ExecutionDomainError(
            f"{name}: runtime substrate is incomplete"
        )
    return host, runtime


def require_same_execution_domain(
    named_domains: Mapping[str, Mapping[str, Any] | None],
) -> dict[str, Any]:
    if not named_domains:
        raise ExecutionDomainError("no execution-domain evidence supplied")

    rows = []
    for name, domain in named_domains.items():
        host, runtime = _verified_domain(name, domain)
        rows.append(
            (
                name,
                host.qualification_domain_id,
                runtime.runtime_substrate_id,
                host.capability_id,
            )
        )

    host_ids = {row[1] for row in rows}
    runtime_ids = {row[2] for row in rows}
    if len(host_ids) != 1:
        raise ExecutionDomainError(
            "claim-bearing runtime evidence spans multiple host qualification domains"
        )
    if len(runtime_ids) != 1:
        raise ExecutionDomainError(
            "claim-bearing runtime evidence spans multiple runtime substrates"
        )

    return {
        "host_qualification_domain_id": rows[0][1],
        "runtime_substrate_id": rows[0][2],
        "members": [
            {
                "name": name,
                "host_qualification_domain_id": host_id,
                "runtime_substrate_id": runtime_id,
                "host_capability_id": capability_id,
            }
            for name, host_id, runtime_id, capability_id in rows
        ],
    }
