"""Execution-domain validation shared by ENGINE-OPT qualification."""

from __future__ import annotations

from typing import Mapping, Any


class ExecutionDomainError(ValueError):
    pass


def require_same_execution_domain(
    named_domains: Mapping[str, Mapping[str, Any] | None],
) -> dict[str, Any]:
    if not named_domains:
        raise ExecutionDomainError("no execution-domain evidence supplied")

    rows: list[tuple[str, str, str, str | None]] = []
    for name, domain in named_domains.items():
        if not isinstance(domain, Mapping):
            raise ExecutionDomainError(f"{name}: execution-domain evidence missing")
        if domain.get("complete") is not True:
            raise ExecutionDomainError(f"{name}: execution-domain evidence incomplete")
        host_id = domain.get("host_qualification_domain_id")
        runtime_id = domain.get("runtime_substrate_id")
        capability_id = domain.get("host_capability_id")
        if not isinstance(host_id, str) or not host_id:
            raise ExecutionDomainError(
                f"{name}: host qualification domain id missing"
            )
        if not isinstance(runtime_id, str) or not runtime_id:
            raise ExecutionDomainError(
                f"{name}: runtime substrate id missing"
            )
        if capability_id is not None and (
            not isinstance(capability_id, str) or not capability_id
        ):
            raise ExecutionDomainError(
                f"{name}: malformed host capability id"
            )
        rows.append((name, host_id, runtime_id, capability_id))

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
