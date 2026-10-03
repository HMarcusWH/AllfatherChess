"""META-1 host/source preflight and postflight evidence."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from controller.host_capabilities import HostCapabilities, discover_host_capabilities
from tools.engine_opt.domain import candidate_bundle_identity
from tools.local_game.common import ROOT, load, require, save, sha, source_identity
from .common import RUN_DISPOSITION, campaign_disposition, policy


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_host_matches_j12(host: HostCapabilities, j12: dict) -> None:
    domain = j12.get("execution_domain")
    require(isinstance(domain, dict), "J12 report lacks execution-domain evidence")
    raw = domain.get("host_capabilities")
    require(isinstance(raw, dict), "J12 execution domain lacks HostCapabilities")
    retained = HostCapabilities.from_dict(raw)
    scope = domain.get("binding_scope")
    if scope == "exact_host_observation":
        require(
            host.digest == retained.digest,
            "META-1 live host differs from exact J12 host observation",
        )
    elif scope == "reusable_host_domain":
        require(
            host.qualification_domain_complete
            and host.qualification_domain_digest
            == retained.qualification_domain_digest,
            "META-1 live host differs from J12 reusable qualification domain",
        )
    else:
        raise RuntimeError(f"unsupported J12 execution-domain binding scope: {scope!r}")


def validate_preflight_payload(
    payload: dict,
    *,
    source: dict,
    p: dict,
    j12: dict,
    candidate_bundle: dict,
) -> HostCapabilities:
    require(payload.get("schema_version") == 1, "META-1 preflight schema drift")
    require(payload.get("profile_id") == "meta-1-v1", "META-1 preflight profile drift")
    require(payload.get("source") == source, "META-1 preflight source drift")
    require(
        payload.get("policy_sha256") == sha(ROOT / "qualification/meta-1-v1.json"),
        "META-1 preflight policy identity drift",
    )
    require(
        payload.get("runtime_config_sha256") == sha(ROOT / p["source_runtime"]),
        "META-1 preflight runtime identity drift",
    )
    require(
        payload.get("candidate_bundle") == candidate_bundle,
        "META-1 preflight candidate bundle drift",
    )
    derived = campaign_disposition(j12)
    require(
        payload.get("qualification_disposition") == derived,
        "META-1 preflight disposition differs from J12 evidence",
    )
    raw_host = payload.get("host_capabilities")
    require(isinstance(raw_host, dict), "META-1 preflight HostCapabilities missing")
    host = HostCapabilities.from_dict(raw_host)
    require(
        payload.get("host_capabilities_digest") == host.digest,
        "META-1 preflight host digest mismatch",
    )
    require(
        payload.get("qualification_domain_digest")
        == host.qualification_domain_digest,
        "META-1 preflight qualification-domain digest mismatch",
    )
    if derived == RUN_DISPOSITION:
        require(
            j12.get("authority_qualified") is True
            and j12.get("synthetic_capacity_observation") is False,
            "META-1 preflight attempted to launder synthetic J12 authority",
        )
        require(
            host.capacity_complete and host.qualification_domain_complete,
            "META-1 real campaign requires complete live host capacity/identity",
        )
        _require_host_matches_j12(host, j12)
    return host


def validate_postflight_payload(
    payload: dict,
    *,
    source: dict,
    preflight: dict,
    require_stable: bool,
) -> HostCapabilities:
    require(payload.get("schema_version") == 1, "META-1 postflight schema drift")
    require(payload.get("profile_id") == "meta-1-v1", "META-1 postflight profile drift")
    require(payload.get("source") == source, "META-1 postflight source drift")
    raw_host = payload.get("host_capabilities")
    require(isinstance(raw_host, dict), "META-1 postflight HostCapabilities missing")
    host = HostCapabilities.from_dict(raw_host)
    require(
        payload.get("host_capabilities_digest") == host.digest,
        "META-1 postflight host digest mismatch",
    )
    require(
        payload.get("qualification_domain_digest")
        == host.qualification_domain_digest,
        "META-1 postflight qualification-domain digest mismatch",
    )
    expected = preflight.get("qualification_domain_digest")
    stable = bool(expected is not None and host.qualification_domain_digest == expected)
    require(
        payload.get("qualification_domain_stable") is stable,
        "META-1 postflight stability marker is inconsistent",
    )
    if require_stable:
        require(
            host.qualification_domain_complete and stable,
            "META-1 campaign host qualification domain drifted during execution",
        )
    return host


def capture_preflight(j12_path: Path, output: Path) -> dict:
    p = policy()
    source = source_identity()
    j12 = load(j12_path)
    require(
        j12.get("source_commit") == source["commit"],
        "META-1 J12 prerequisite is stale",
    )
    disposition = campaign_disposition(j12)
    bundle = candidate_bundle_identity(
        ROOT / p["bundle_root"],
        expected_source_commit=source["commit"],
    )
    if isinstance(j12.get("candidate_bundle"), dict):
        require(
            j12["candidate_bundle"] == bundle,
            "META-1 candidate bundle differs from J12 prerequisite",
        )
    host = discover_host_capabilities()
    payload = {
        "schema_version": 1,
        "profile_id": "meta-1-v1",
        "captured_at_utc": _utc_now(),
        "source": source,
        "qualification_disposition": disposition,
        "policy_sha256": sha(ROOT / "qualification/meta-1-v1.json"),
        "runtime_config_sha256": sha(ROOT / p["source_runtime"]),
        "j12_report_sha256": sha(j12_path),
        "candidate_bundle": bundle,
        "host_capabilities": host.as_dict(),
        "host_capabilities_digest": host.digest,
        "qualification_domain_digest": host.qualification_domain_digest,
        "runner_environment": os.environ.get("ALLFATHER_RUNNER_ENVIRONMENT"),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
        "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
    }
    validate_preflight_payload(
        payload,
        source=source,
        p=p,
        j12=j12,
        candidate_bundle=bundle,
    )
    save(output, payload)
    return payload


def capture_postflight(preflight_path: Path, output: Path) -> dict:
    source = source_identity()
    pre = load(preflight_path)
    host = discover_host_capabilities()
    expected = pre.get("qualification_domain_digest")
    stable = bool(expected is not None and host.qualification_domain_digest == expected)
    payload = {
        "schema_version": 1,
        "profile_id": "meta-1-v1",
        "captured_at_utc": _utc_now(),
        "source": source,
        "preflight_sha256": sha(preflight_path),
        "host_capabilities": host.as_dict(),
        "host_capabilities_digest": host.digest,
        "qualification_domain_digest": host.qualification_domain_digest,
        "qualification_domain_stable": stable,
    }
    save(output, payload)
    validate_postflight_payload(
        payload,
        source=source,
        preflight=pre,
        require_stable=pre.get("qualification_disposition") == RUN_DISPOSITION,
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="phase", required=True)
    pre = sub.add_parser("pre")
    pre.add_argument("--j12-report", type=Path, required=True)
    pre.add_argument("--output", type=Path, required=True)
    post = sub.add_parser("post")
    post.add_argument("--preflight", type=Path, required=True)
    post.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.phase == "pre":
        payload = capture_preflight(args.j12_report.resolve(), args.output.resolve())
    else:
        payload = capture_postflight(args.preflight.resolve(), args.output.resolve())
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
