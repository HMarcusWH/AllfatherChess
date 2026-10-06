"""Archive-authenticated compatibility for pre-marker replay resource partitions.

New TimePlans carry an explicit partition-policy marker. An absent marker never
selects legacy arithmetic by itself: the exact manifest and TimePlan must occur
inside one SHA-bound source-controlled historical archive scope.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from collections.abc import Iterator, Mapping

from controller.budget import ResourceEnvelope

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "qualification/replay-history-sources.json"

ABSOLUTE_CONTROLLER_V2 = "absolute-controller-v2"
LEGACY_PROPORTIONAL_V1 = "legacy-unversioned-proportional-v1"
LEGACY_ABSOLUTE_V1 = "legacy-unversioned-absolute-v1"
HISTORICAL_POLICIES = (LEGACY_PROPORTIONAL_V1, LEGACY_ABSOLUTE_V1)

_MANIFESTS: ContextVar[Mapping[str, str]] = ContextVar(
    "historical_manifests", default={}
)
_PLANS: ContextVar[Mapping[str, str]] = ContextVar(
    "historical_time_plans", default={}
)


def canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def policy_for_manifest(manifest: Mapping) -> str:
    raw = manifest.get("time_plan")
    if not isinstance(raw, Mapping):
        raise ValueError("missing TimePlan")
    marker = raw.get("resource_partition_policy")
    if marker == ABSOLUTE_CONTROLLER_V2:
        return ABSOLUTE_CONTROLLER_V2
    if marker is not None:
        raise ValueError("unsupported resource-partition marker")
    policy = _MANIFESTS.get().get(canonical_digest(manifest))
    if policy not in HISTORICAL_POLICIES:
        raise ValueError(
            "unversioned TimePlan requires an authenticated historical archive"
        )
    return policy


def policy_for_time_plan(raw: Mapping) -> str:
    marker = raw.get("resource_partition_policy")
    if marker == ABSOLUTE_CONTROLLER_V2:
        return ABSOLUTE_CONTROLLER_V2
    if marker is not None:
        raise ValueError("unsupported resource-partition marker")
    policy = _PLANS.get().get(canonical_digest(raw))
    if policy not in HISTORICAL_POLICIES:
        raise ValueError(
            "unversioned TimePlan is not in the authenticated historical archive"
        )
    return policy


def historical_envelope(
    baseline: ResourceEnvelope,
    *,
    cpu_ms: float,
    wall_ms: float,
    policy: str,
    adaptive: bool = False,
) -> ResourceEnvelope:
    """Reconstruct the old arithmetic only after archive authentication."""
    if policy not in HISTORICAL_POLICIES:
        raise ValueError("not a registered historical partition policy")
    cpu = min(float(cpu_ms), float(baseline.cpu_ms))
    wall = min(float(wall_ms), float(baseline.wall_ms))
    if adaptive:
        controller = (
            0.0
            if baseline.cpu_ms == 0
            else baseline.controller_overhead_reserve_ms * cpu / baseline.cpu_ms
        )
    elif policy == LEGACY_PROPORTIONAL_V1:
        controller = (
            0.0
            if baseline.cpu_ms == 0
            else cpu * baseline.controller_overhead_reserve_ms / baseline.cpu_ms
        )
    else:
        capacity = max(
            0.0,
            cpu
            - cpu
            * (
                baseline.verification_reserve_fraction
                + baseline.refinement_reserve_fraction
            ),
        )
        controller = min(
            baseline.controller_overhead_reserve_ms,
            capacity,
        )
    return replace(
        baseline,
        wall_ms=wall,
        cpu_ms=cpu,
        # Historical TimePlan bounded_for_move() serialized integer 0,
        # while the old adaptive J8 clamp serialized 0.0. plan_id hashes
        # canonical JSON, so preserve that exact historical representation.
        gpu_ms=(0.0 if adaptive else 0),
        controller_overhead_reserve_ms=controller,
    )


def _load_registry() -> dict:
    raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise ValueError("unsupported replay history registry")
    if not isinstance(raw.get("sources"), list):
        raise ValueError("replay history sources must be an array")
    return raw


@contextmanager
def historical_replay_scope(archive: Path) -> Iterator[dict]:
    """Authenticate one historical archive and admit only its exact documents."""
    archive = Path(archive)
    registry = _load_registry()
    digest = file_sha256(archive)
    matches = [
        row
        for row in registry["sources"]
        if row.get("artifact_sha256") == digest
    ]
    if len(matches) != 1:
        raise ValueError("historical archive SHA is not uniquely registered")
    source = matches[0]
    policy = source.get("partition_policy")
    if policy not in HISTORICAL_POLICIES:
        raise ValueError("unsupported historical source policy")

    manifests: dict[str, str] = {}
    plans: dict[str, str] = {}
    with zipfile.ZipFile(archive) as handle:
        names = handle.namelist()
        if len(set(names)) != len(names):
            raise ValueError("duplicate archive members")
        total = 0
        for info in handle.infolist():
            name = info.filename
            rel = PurePosixPath(name)
            if (
                rel.is_absolute()
                or ".." in rel.parts
                or "\\" in name
                or "\x00" in name
            ):
                raise ValueError("unsafe archive member")
            total += int(info.file_size)
            if total > 2 * 1024**3:
                raise ValueError("historical archive exceeds size limit")

        anchor = json.loads(handle.read(source["source_manifest"]))
        observed = (anchor.get("source") or {}).get(
            "commit",
            anchor.get("source_commit"),
        )
        if observed != source.get("source_commit"):
            raise ValueError("historical campaign source identity mismatch")

        prefix = str(source["campaign_path"]).rstrip("/") + "/"
        for name in names:
            if (
                not name.startswith(prefix)
                or not name.endswith("/manifest.json")
            ):
                continue
            manifest = json.loads(handle.read(name))
            raw = manifest.get("time_plan")
            if not isinstance(raw, dict):
                continue
            if "resource_partition_policy" in raw:
                raise ValueError(
                    "registered pre-marker archive unexpectedly has a marker"
                )
            manifests[canonical_digest(manifest)] = policy
            plans[canonical_digest(raw)] = policy

    if not manifests:
        raise ValueError("registered archive has no clocked replay manifests")

    manifest_token = _MANIFESTS.set(MappingProxyType(manifests))
    plan_token = _PLANS.set(MappingProxyType(plans))
    try:
        yield {
            "source_commit": source["source_commit"],
            "artifact_sha256": digest,
            "partition_policy": policy,
            "manifests": len(manifests),
            "runtime_authority": False,
            "resource_authorization": False,
            "outward_move": False,
        }
    finally:
        _PLANS.reset(plan_token)
        _MANIFESTS.reset(manifest_token)


@contextmanager
def archive_replay_scope(archive: Path) -> Iterator[dict | None]:
    """Use historical semantics only for a registered archive.

    Unknown archives are still processed normally; any unversioned TimePlan in
    them remains invalid rather than silently selecting old arithmetic.
    """
    registry = _load_registry()
    digest = file_sha256(Path(archive))
    if any(
        row.get("artifact_sha256") == digest
        for row in registry["sources"]
    ):
        with historical_replay_scope(Path(archive)) as binding:
            yield binding
        return

    manifest_token = _MANIFESTS.set(MappingProxyType({}))
    plan_token = _PLANS.set(MappingProxyType({}))
    try:
        yield None
    finally:
        _PLANS.reset(plan_token)
        _MANIFESTS.reset(manifest_token)
