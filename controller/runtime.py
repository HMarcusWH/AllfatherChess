"""Transactional managed-engine runtime for the Generation 1 UCI shell.

The runtime owns process identity, authority separation, synchronized chess
state, and the legal-root oracle. It deliberately owns no routing policy, no
residual computation, and no replay analysis.
"""

from __future__ import annotations

import glob
import hashlib
import json
import math
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from adapters.process import UciProcess, UciProcessError
from adapters.telemetry import SUPPORTED_SCORE_TYPES
from common.search_request import SearchRequestError, parse_position_command
from controller.resource_measurement import ResourceMeasurementError, ResourceMeasurementSettings
from controller.online_time import ClockSearch, OnlineTimeSettings, OnlineTimeError
from adapters.process.deferred_observer import DeferredObserver


class RuntimeError(RuntimeError):
    """Raised when the managed backend runtime cannot preserve its contract."""


_PERFT_ROOT_RE = re.compile(r"^([a-h][1-8][a-h][1-8][qrbn]?):\s+(\d+)$")
_PERFT_TOTAL_RE = re.compile(r"^Nodes searched:\s+(\d+)$")

SOLVER_FAMILIES = ("stockfish", "reckless", "lc0")

#: ``anchor`` holds outward decision authority. ``managed`` instances are
#: synchronized authority-critical backends from the legacy anchor profile.
#: ``shadow`` instances are restricted observational workers whose death is
#: evidence rather than an authority failure.
INSTANCE_ROLES = ("anchor", "managed", "shadow")

AUTHORITY_ROLES = ("anchor", "managed")

EXECUTION_MODES = ("anchor", "shadow", "active")

#: telemetry v1 ``controller.execution_mode`` value for each runtime mode.
TELEMETRY_EXECUTION_MODE = {
    "anchor": "baseline",
    "shadow": "shadow",
    "active": "active",
}


@dataclass(frozen=True)
class BackendSpec:
    """One managed engine instance.

    ``name`` is the process-role identity (for example ``stockfish-shadow``).
    ``family`` is the solver-family identity (for example ``stockfish``) and
    carries the score/work semantics. The two must never be conflated.
    """

    name: str
    family: str
    role: str
    binary: Path
    cwd: Path
    args: tuple[str, ...]
    environment: dict[str, str]
    options: dict[str, object]


@dataclass(frozen=True)
class ShadowSettings:
    """Shadow/active execution settings.

    ``owners`` are the *solver families* that own ledger shards. The unrestricted
    anchor is intentionally absent: it is not an exploration owner.
    """

    owners: tuple[str, ...]
    instance_by_owner: dict[str, str]
    oracle: str
    replay_root: Path
    partition: str
    dispatch_limit: dict[str, object]
    lc0_score_type: str
    oracle_timeout_s: float
    drain_timeout_s: float
    #: Hard cap on one shadow stage that is progressing normally. Distinct from
    #: `drain_timeout_s`, which bounds waiting for a worker to stop AFTER it has
    #: been asked to. Using the drain bound for both imposed an undocumented
    #: runtime limit on every node-limited stage.
    stage_timeout_s: float
    #: Hard bound on the replay filesystem setup that runs BEFORE the outward
    #: anchor is dispatched. Observational infrastructure may not delay the
    #: decision path by more than this; past it the run proceeds without a
    #: bundle rather than making the anchor wait.
    prepare_budget_s: float
    on_anchor_complete: str

    def instance(self, owner: str) -> str:
        return self.instance_by_owner[owner]


@dataclass(frozen=True)
class StagedVerificationExtensionSettings:
    """Serve-compatible research intervention for M14-G1.

    The extension is a fresh second `go` on the same managed process after a
    clean base VERIFY round. It is evidence collection only and carries neither
    routing nor outward-move authority.
    """

    enabled: bool
    intervention: str
    dispatch_limit: dict[str, object]


@dataclass(frozen=True)
class VerificationSettings:
    """Explicit common-support VERIFY instrumentation.

    v1 remains the single-round compatibility substrate. M14-G1 may attach one
    staged extension round while preserving the same candidate universe and
    process identities. Neither form grants decision authority.
    """

    enabled: bool
    nomination_method: str
    dispatch_limit: dict[str, object]
    staged_extension: StagedVerificationExtensionSettings | None = None


@dataclass(frozen=True)
class RefinementSettings:
    """Bounded recursive REFINE instrumentation.

    Root nominations come from completed VERIFY.  Deeper nominations are
    evidence-only and must pass a separate policy plus fresh resource
    authorization before any oracle/search work starts.
    """

    enabled: bool
    nomination_method: str
    child_partition: str
    dispatch_limit: dict[str, object]
    max_targets: int
    recursive_nomination_method: str = "stage_terminal_bestmove_v1"
    max_depth: int = 2
    max_expansions: int = 3

@dataclass(frozen=True)
class CrossFeedSettings:
    """Decision-inert typed evidence composition over existing specialist work."""

    enabled: bool
    policy: str


@dataclass(frozen=True)
class CounterfactualSettings:
    """Counterfactual proposal generation over typed specialist evidence."""

    enabled: bool
    policy: str


@dataclass(frozen=True)
class HybridAuthoritySettings:
    """Versioned outward decision authority configuration.

    bounded_preanchor_v0 is the frozen M14-C movetime-only contract.
    clocked_staged_preanchor_v1 is M14-G3 and may consume only a complete,
    route-bound staged VERIFY terminal plane under an ONLINE TimePlan.
    """

    enabled: bool
    policy: str
    request_class: str
    terminal_source_policy: str = "base_verify_v0"
    allow_skipped_extension_authority: bool = False


@dataclass(frozen=True)
class RuntimeConfig:
    path: Path
    root: Path
    mode: str
    anchor: str
    backends: dict[str, BackendSpec]
    shadow: ShadowSettings | None = None
    verification: VerificationSettings | None = None
    refinement: RefinementSettings | None = None
    crossfeed: CrossFeedSettings | None = None
    counterfactual: CounterfactualSettings | None = None
    hybrid_authority: HybridAuthoritySettings | None = None
    resource_measurement: ResourceMeasurementSettings | None = None
    budget: dict[str, object] | None = None
    routing: dict[str, object] | None = None
    online_time: OnlineTimeSettings | None = None

    @property
    def instances(self) -> dict[str, BackendSpec]:
        """Alias for ``backends``; both are keyed by process-role identity."""
        return self.backends

    def instances_with_role(self, *roles: str) -> tuple[BackendSpec, ...]:
        wanted = set(roles)
        return tuple(spec for spec in self.backends.values() if spec.role in wanted)

    @property
    def telemetry_execution_mode(self) -> str:
        return TELEMETRY_EXECUTION_MODE[self.mode]


def _require_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object")
    return value


#: Instance names become telemetry filenames, so they are restricted to a safe
#: identifier alphabet rather than merely checked for non-emptiness.
_SAFE_INSTANCE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")


def _require_positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{label} must be a positive number")
    number = float(value)
    if not number > 0:
        raise RuntimeError(f"{label} must be a positive number")
    if not math.isfinite(number):
        # A JSON `1e309` parses to infinity and passes `> 0`. These values reach
        # `Event.wait()` and `Thread.join()`, which raise OverflowError on an
        # infinite timeout -- and in the quiesce path that exception is caught,
        # so the frontend would proceed with a state mutation without excluding
        # the worker that is still running.
        raise RuntimeError(f"{label} must be finite, got {value!r}")
    return number


def _resolve_binary(root: Path, raw: dict[str, object], name: str) -> Path:
    binary_value = raw.get("binary")
    if not isinstance(binary_value, str) or not binary_value:
        raise RuntimeError(f"backend {name}: binary must be a non-empty string")
    direct = (root / binary_value).resolve()
    if direct.is_file():
        return direct

    fallback = raw.get("fallback_glob")
    if fallback is not None:
        if not isinstance(fallback, str) or not fallback:
            raise RuntimeError(f"backend {name}: fallback_glob must be a non-empty string")
        matches = sorted(
            Path(value).resolve()
            for value in glob.glob(str(root / fallback), recursive=True)
            if Path(value).is_file()
        )
        if matches:
            return matches[0]

    raise RuntimeError(f"backend {name}: binary not found: {direct}")


def _hash_file(path: Path) -> str | None:
    """sha256 of a file, or None when it cannot be read."""
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:  # pragma: no cover - identity is best effort
        return None


def _file_identity(path: Path) -> dict[str, object]:
    """Best-effort immutable identity for a configured external artifact."""
    resolved = Path(path).resolve()
    try:
        size = resolved.stat().st_size
    except OSError:
        size = None
    return {
        "path": str(resolved),
        "size": size,
        "sha256": _hash_file(resolved),
    }


def _engine_identity(spec: "BackendSpec") -> dict[str, object]:
    identity: dict[str, object] = {
        "engine": spec.family,
        "role": spec.role,
        "binary": str(spec.binary),
        "binary_sha256": _hash_file(spec.binary),
        "args": list(spec.args),
        "options": dict(spec.options),
    }
    # Preserve historical identity for every existing profile.  Only explicit
    # process-environment overrides are claim-bearing and therefore serialized.
    if spec.environment:
        identity["environment"] = dict(spec.environment)
    if spec.family == "lc0":
        weights = spec.options.get("WeightsFile")
        if isinstance(weights, str) and weights and weights != "<autodiscover>":
            weights_path = Path(weights)
            if not weights_path.is_absolute():
                weights_path = spec.cwd / weights_path
            identity["artifacts"] = {
                "weights": _file_identity(weights_path),
            }
    return identity


def _build_spec(
    *,
    root: Path,
    raw: dict[str, object],
    name: str,
    family: str,
    role: str,
) -> BackendSpec:
    cwd_value = raw.get("cwd", ".")
    if not isinstance(cwd_value, str) or not cwd_value:
        raise RuntimeError(f"backend {name}: cwd must be a non-empty string")
    args_value = raw.get("args", [])
    if not isinstance(args_value, list) or not all(isinstance(item, str) for item in args_value):
        raise RuntimeError(f"backend {name}: args must be an array of strings")
    environment_raw = _require_object(
        raw.get("environment", {}),
        f"backend {name}.environment",
    )
    environment: dict[str, str] = {}
    for key, value in environment_raw.items():
        if not isinstance(key, str) or _ENVIRONMENT_NAME.fullmatch(key) is None:
            raise RuntimeError(
                f"backend {name}: environment variable names must match "
                f"{_ENVIRONMENT_NAME.pattern}, got {key!r}"
            )
        if not isinstance(value, str) or "\x00" in value:
            raise RuntimeError(
                f"backend {name}: environment[{key!r}] must be a NUL-free string"
            )
        environment[key] = value
    options = _require_object(raw.get("options", {}), f"backend {name}.options")
    if options.get("UCI_Chess960") is True:
        # The controller's shared variant state starts as standard chess and is
        # only ever changed by the GUI's `setoption name UCI_Chess960`. Starting
        # a process in Chess960 would leave it searching FRC while every replay
        # recorded `variant: standard` and decoded castling moves the other way.
        # Refuse it rather than record a position the engines are not searching.
        raise RuntimeError(
            f"backend {name}: UCI_Chess960 may not be enabled as a startup option; "
            "the variant is owned by the GUI via 'setoption name UCI_Chess960'"
        )
    return BackendSpec(
        name=name,
        family=family,
        role=role,
        binary=_resolve_binary(root, raw, name),
        cwd=(root / cwd_value).resolve(),
        args=tuple(args_value),
        environment=environment,
        options=dict(options),
    )


def _load_legacy_backends(data: dict[str, object], root: Path) -> tuple[str, dict[str, BackendSpec]]:
    """Load the frozen PR #9/#10 anchor profile.

    Instance identity equals solver-family identity in this shape, so existing
    configs, contracts, and tests keep working unchanged.
    """
    mode = data.get("mode")
    if mode != "anchor":
        raise RuntimeError(f"runtime config schema_version 1 supports only mode='anchor', got {mode!r}")
    anchor = data.get("anchor")
    if not isinstance(anchor, str) or not anchor:
        raise RuntimeError("runtime config anchor must be a non-empty string")

    raw_backends = _require_object(data.get("backends"), "backends")
    expected = set(SOLVER_FAMILIES)
    if set(raw_backends) != expected:
        raise RuntimeError(
            f"runtime config backends must be exactly {sorted(expected)}, got {sorted(raw_backends)}"
        )
    if anchor not in expected:
        raise RuntimeError(f"anchor backend is not configured: {anchor}")

    specs: dict[str, BackendSpec] = {}
    for family in SOLVER_FAMILIES:
        raw = _require_object(raw_backends[family], f"backend {family}")
        specs[family] = _build_spec(
            root=root,
            raw=raw,
            name=family,
            family=family,
            role="anchor" if family == anchor else "managed",
        )
    return anchor, specs


def _load_instances(data: dict[str, object], root: Path) -> tuple[str, dict[str, BackendSpec]]:
    anchor = data.get("anchor")
    if not isinstance(anchor, str) or not anchor:
        raise RuntimeError("runtime config anchor must be a non-empty string")

    raw_instances = _require_object(data.get("instances"), "instances")
    if not raw_instances:
        raise RuntimeError("runtime config instances must not be empty")

    specs: dict[str, BackendSpec] = {}
    for name in sorted(raw_instances):
        if not name:
            raise RuntimeError("instance names must be non-empty strings")
        if not _SAFE_INSTANCE_NAME.fullmatch(name):
            # The name is interpolated into `run_dir / f"{instance}.jsonl"`. A
            # `/`, a `..` or an absolute path writes telemetry outside the run
            # directory, and even a benign nested name breaks replay loading,
            # because the manifest stores only `path.name`.
            raise RuntimeError(
                f"instance name {name!r} must match {_SAFE_INSTANCE_NAME.pattern}: "
                "it is used directly as a telemetry filename"
            )
        raw = _require_object(raw_instances[name], f"instance {name}")
        family = raw.get("family")
        if family not in SOLVER_FAMILIES:
            raise RuntimeError(f"instance {name}: family must be one of {list(SOLVER_FAMILIES)}, got {family!r}")
        role = raw.get("role")
        if role not in INSTANCE_ROLES:
            raise RuntimeError(f"instance {name}: role must be one of {list(INSTANCE_ROLES)}, got {role!r}")
        specs[name] = _build_spec(root=root, raw=raw, name=name, family=str(family), role=str(role))

    anchors = sorted(name for name, spec in specs.items() if spec.role == "anchor")
    if anchors != [anchor]:
        raise RuntimeError(
            f"exactly one instance must hold the anchor role and it must be {anchor!r}; found {anchors}"
        )
    return anchor, specs


def _load_shadow_settings(
    data: dict[str, object],
    *,
    specs: dict[str, BackendSpec],
    anchor: str,
    root: Path,
) -> ShadowSettings:
    raw = _require_object(data.get("shadow"), "shadow")

    raw_owners = raw.get("owners")
    if not isinstance(raw_owners, list) or not raw_owners:
        raise RuntimeError("shadow.owners must be a non-empty array of solver families")
    owners: list[str] = []
    for owner in raw_owners:
        if owner not in SOLVER_FAMILIES:
            raise RuntimeError(f"shadow.owners contains unknown solver family: {owner!r}")
        if owner in owners:
            raise RuntimeError(f"shadow.owners contains a duplicate owner: {owner!r}")
        owners.append(str(owner))

    raw_map = _require_object(raw.get("instance_by_owner"), "shadow.instance_by_owner")
    if sorted(raw_map) != sorted(owners):
        raise RuntimeError(
            f"shadow.instance_by_owner keys must match shadow.owners exactly; "
            f"owners={sorted(owners)}, keys={sorted(raw_map)}"
        )
    instance_by_owner: dict[str, str] = {}
    seen_instances: set[str] = set()
    for owner in owners:
        instance = raw_map[owner]
        if not isinstance(instance, str) or instance not in specs:
            raise RuntimeError(f"shadow owner {owner!r} references unknown instance: {instance!r}")
        spec = specs[instance]
        if spec.role != "shadow":
            raise RuntimeError(f"shadow owner {owner!r} instance {instance!r} must have role 'shadow'")
        if spec.family != owner:
            raise RuntimeError(
                f"shadow owner {owner!r} instance {instance!r} has solver family {spec.family!r}"
            )
        if instance in seen_instances:
            raise RuntimeError(f"shadow instance {instance!r} is assigned to more than one owner")
        seen_instances.add(instance)
        instance_by_owner[owner] = instance

    unmapped = sorted(
        name for name, spec in specs.items() if spec.role == "shadow" and name not in seen_instances
    )
    if unmapped:
        raise RuntimeError(f"shadow instances without a ledger owner are forbidden: {unmapped}")

    if specs[anchor].family != "stockfish":
        # The frontend, the docs and every claim in this milestone say the
        # outward move is the unrestricted Stockfish anchor's in every mode. A
        # config naming a Reckless or LC0 anchor passes every other check and
        # silently changes which engine holds decision authority -- and an LC0
        # anchor's GPU use has no anchor-side reservation in the active ledger.
        raise RuntimeError(
            f"anchor {anchor!r} has solver family {specs[anchor].family!r}; outward "
            "decision authority is declared to be Stockfish in every shadow and "
            "active profile"
        )

    oracle = raw.get("oracle")
    if not isinstance(oracle, str) or oracle not in specs:
        raise RuntimeError(f"shadow.oracle must name a configured instance, got {oracle!r}")
    if specs[oracle].family != "stockfish":
        raise RuntimeError("shadow.oracle must be a Stockfish-family instance (perft oracle)")
    if oracle == anchor:
        raise RuntimeError(
            "shadow.oracle must not be the outward anchor: the anchor may not be blocked by 'go perft 1'"
        )
    if specs[oracle].role != "shadow":
        # "not the anchor" is not the same as "observational". A `managed`
        # instance is authority-critical, so an oracle timeout on one would take
        # the authority failure path and could fail the outward search -- and
        # `record_shadow_failure` would not exclude it from synchronization.
        raise RuntimeError(
            f"shadow.oracle {oracle!r} has role {specs[oracle].role!r}; the legal-root "
            "oracle must be an observational shadow instance so that its failures "
            "stay evidence and never reach outward authority"
        )

    partition = raw.get("partition", "root_index_modulo")
    if partition != "root_index_modulo":
        raise RuntimeError(f"unsupported shadow.partition: {partition!r}")

    dispatch = _require_object(raw.get("dispatch_limit", {"nodes": 20000}), "shadow.dispatch_limit")
    if sorted(dispatch) != ["nodes"]:
        raise RuntimeError("shadow.dispatch_limit currently supports exactly the 'nodes' key")
    nodes = dispatch["nodes"]
    if isinstance(nodes, bool) or not isinstance(nodes, int) or nodes < 1:
        raise RuntimeError("shadow.dispatch_limit.nodes must be a positive integer")

    score_type = raw.get("lc0_score_type", "centipawn")
    if not isinstance(score_type, str) or not score_type:
        raise RuntimeError("shadow.lc0_score_type must be a non-empty string")
    if score_type not in SUPPORTED_SCORE_TYPES:
        # Caught here rather than inside the telemetry writer thread, where a
        # typo would surface as an adapter error after every engine had already
        # started and the LC0 search had been dispatched, leaving the run with
        # no usable LC0 evidence instead of a refusal.
        raise RuntimeError(
            f"shadow.lc0_score_type {score_type!r} is not a supported LC0 ScoreType; "
            f"supported: {sorted(SUPPORTED_SCORE_TYPES)}"
        )

    lc0_instance = instance_by_owner.get("lc0")
    if lc0_instance is not None:
        configured_score_type = specs[lc0_instance].options.get("ScoreType")
        if not isinstance(configured_score_type, str) or not configured_score_type:
            raise RuntimeError(
                f"shadow LC0 instance {lc0_instance!r} must set the ScoreType UCI "
                "option explicitly; telemetry semantics may not rely on LC0 defaults"
            )
        if configured_score_type != score_type:
            raise RuntimeError(
                f"shadow LC0 ScoreType mismatch: instance option is "
                f"{configured_score_type!r} but shadow.lc0_score_type is "
                f"{score_type!r}"
            )

    replay_value = raw.get("replay_root", "build/replays")
    if not isinstance(replay_value, str) or not replay_value:
        raise RuntimeError("shadow.replay_root must be a non-empty path string")
    replay_root = Path(replay_value)
    if not replay_root.is_absolute():
        replay_root = (root / replay_value).resolve()

    on_anchor_complete = raw.get("on_anchor_complete", "drain")
    if on_anchor_complete not in ("drain", "cancel"):
        raise RuntimeError(
            f"shadow.on_anchor_complete must be 'drain' or 'cancel', got {on_anchor_complete!r}"
        )

    return ShadowSettings(
        owners=tuple(owners),
        instance_by_owner=instance_by_owner,
        oracle=oracle,
        replay_root=replay_root,
        partition=str(partition),
        dispatch_limit={"nodes": int(nodes)},
        lc0_score_type=score_type,
        oracle_timeout_s=_require_positive_number(raw.get("oracle_timeout_s", 10.0), "shadow.oracle_timeout_s"),
        drain_timeout_s=_require_positive_number(raw.get("drain_timeout_s", 5.0), "shadow.drain_timeout_s"),
        stage_timeout_s=_require_positive_number(
            raw.get("stage_timeout_s", 120.0), "shadow.stage_timeout_s"
        ),
        prepare_budget_s=_require_positive_number(
            raw.get("prepare_budget_s", 0.25), "shadow.prepare_budget_s"
        ),
        on_anchor_complete=str(on_anchor_complete),
    )


def _load_verification_settings(
    data: dict[str, object],
    *,
    mode: str,
    shadow: ShadowSettings | None,
) -> VerificationSettings | None:
    raw_value = data.get("verification")
    if raw_value is None:
        return None
    if mode not in ("shadow", "active"):
        raise RuntimeError(
            "verification settings are supported only in shadow/active modes"
        )
    if shadow is None:  # pragma: no cover - mode validation already guarantees this
        raise RuntimeError("verification requires shadow settings")

    raw = _require_object(raw_value, "verification")
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        raise RuntimeError("verification.enabled must be a boolean")
    if not enabled:
        return None

    if tuple(shadow.owners) != SOLVER_FAMILIES:
        raise RuntimeError(
            "verification v1 requires shadow.owners exactly "
            f"{list(SOLVER_FAMILIES)} in that order"
        )

    nomination = raw.get("nomination_method", "owner_bestmove_union_v1")
    if nomination != "owner_bestmove_union_v1":
        raise RuntimeError(
            "verification.nomination_method currently supports exactly "
            "'owner_bestmove_union_v1'"
        )

    dispatch = _require_object(
        raw.get("dispatch_limit", {"nodes": 12000}),
        "verification.dispatch_limit",
    )
    if sorted(dispatch) != ["nodes"]:
        raise RuntimeError(
            "verification.dispatch_limit currently supports exactly the 'nodes' key"
        )
    nodes = dispatch["nodes"]
    if isinstance(nodes, bool) or not isinstance(nodes, int) or nodes < 1:
        raise RuntimeError("verification.dispatch_limit.nodes must be a positive integer")

    staged_extension: StagedVerificationExtensionSettings | None = None
    staged_raw_value = raw.get("staged_extension")
    if staged_raw_value is not None:
        staged_raw = _require_object(
            staged_raw_value,
            "verification.staged_extension",
        )
        staged_enabled = staged_raw.get("enabled")
        if not isinstance(staged_enabled, bool):
            raise RuntimeError(
                "verification.staged_extension.enabled must be a boolean"
            )
        unknown = sorted(
            set(staged_raw) - {"enabled", "intervention", "dispatch_limit"}
        )
        if unknown:
            raise RuntimeError(
                "verification.staged_extension contains unsupported keys: "
                f"{unknown}"
            )
        if staged_enabled:
            intervention = staged_raw.get(
                "intervention",
                "same_process_staged_verify_v1",
            )
            if intervention != "same_process_staged_verify_v1":
                raise RuntimeError(
                    "verification.staged_extension.intervention currently "
                    "supports exactly 'same_process_staged_verify_v1'"
                )
            staged_dispatch = _require_object(
                staged_raw.get("dispatch_limit"),
                "verification.staged_extension.dispatch_limit",
            )
            if sorted(staged_dispatch) != ["nodes"]:
                raise RuntimeError(
                    "verification.staged_extension.dispatch_limit currently "
                    "supports exactly the 'nodes' key"
                )
            staged_nodes = staged_dispatch["nodes"]
            if (
                isinstance(staged_nodes, bool)
                or not isinstance(staged_nodes, int)
                or staged_nodes < 1
            ):
                raise RuntimeError(
                    "verification.staged_extension.dispatch_limit.nodes must "
                    "be a positive integer"
                )
            if int(staged_nodes) <= int(nodes):
                raise RuntimeError(
                    "verification.staged_extension nodes must exceed the base "
                    "VERIFY node limit"
                )
            staged_extension = StagedVerificationExtensionSettings(
                enabled=True,
                intervention=str(intervention),
                dispatch_limit={"nodes": int(staged_nodes)},
            )

    return VerificationSettings(
        enabled=True,
        nomination_method=str(nomination),
        dispatch_limit={"nodes": int(nodes)},
        staged_extension=staged_extension,
    )


def _load_refinement_settings(
    data: dict[str, object],
    *,
    mode: str,
    shadow: ShadowSettings | None,
    verification: VerificationSettings | None,
) -> RefinementSettings | None:
    raw_value = data.get("refinement")
    if raw_value is None:
        return None
    if mode not in ("shadow", "active"):
        raise RuntimeError(
            "refinement settings are supported only in shadow/active modes"
        )
    if shadow is None:
        raise RuntimeError("refinement requires shadow settings")
    if verification is None or not verification.enabled:
        raise RuntimeError("refinement requires verification.enabled")

    raw = _require_object(raw_value, "refinement")
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        raise RuntimeError("refinement.enabled must be a boolean")
    if not enabled:
        return None

    if tuple(shadow.owners) != SOLVER_FAMILIES:
        raise RuntimeError(
            "refinement v1 requires shadow.owners exactly "
            f"{list(SOLVER_FAMILIES)} in that order"
        )

    nomination = raw.get(
        "nomination_method", "verify_final_disagreement_union_v1"
    )
    if nomination != "verify_final_disagreement_union_v1":
        raise RuntimeError(
            "refinement.nomination_method currently supports exactly "
            "'verify_final_disagreement_union_v1'"
        )

    child_partition = raw.get("child_partition", "child_index_modulo")
    if child_partition != "child_index_modulo":
        raise RuntimeError(
            "refinement.child_partition currently supports exactly "
            "'child_index_modulo'"
        )

    dispatch = _require_object(
        raw.get("dispatch_limit", {"nodes": 3000}),
        "refinement.dispatch_limit",
    )
    if sorted(dispatch) != ["nodes"]:
        raise RuntimeError(
            "refinement.dispatch_limit currently supports exactly the 'nodes' key"
        )
    nodes = dispatch["nodes"]
    if isinstance(nodes, bool) or not isinstance(nodes, int) or nodes < 1:
        raise RuntimeError("refinement.dispatch_limit.nodes must be a positive integer")

    max_targets = raw.get("max_targets", 3)
    if (
        isinstance(max_targets, bool)
        or not isinstance(max_targets, int)
        or max_targets < 1
        or max_targets > 3
    ):
        raise RuntimeError("refinement.max_targets must be an integer in [1, 3]")

    recursive_nomination = raw.get(
        "recursive_nomination_method", "stage_terminal_bestmove_v1"
    )
    if recursive_nomination != "stage_terminal_bestmove_v1":
        raise RuntimeError(
            "refinement.recursive_nomination_method currently supports exactly "
            "'stage_terminal_bestmove_v1'"
        )

    max_depth = raw.get("max_depth", 2)
    if (
        isinstance(max_depth, bool)
        or not isinstance(max_depth, int)
        or max_depth < 2
        or max_depth > 8
    ):
        raise RuntimeError("refinement.max_depth must be an integer in [2, 8]")

    max_expansions = raw.get("max_expansions", max_targets)
    if (
        isinstance(max_expansions, bool)
        or not isinstance(max_expansions, int)
        or max_expansions < 1
        or max_expansions > 64
    ):
        raise RuntimeError("refinement.max_expansions must be an integer in [1, 64]")

    unknown = sorted(
        set(raw)
        - {
            "enabled",
            "nomination_method",
            "recursive_nomination_method",
            "child_partition",
            "dispatch_limit",
            "max_targets",
            "max_depth",
            "max_expansions",
        }
    )
    if unknown:
        raise RuntimeError(f"refinement contains unsupported keys: {unknown}")

    return RefinementSettings(
        enabled=True,
        nomination_method=str(nomination),
        recursive_nomination_method=str(recursive_nomination),
        child_partition=str(child_partition),
        dispatch_limit={"nodes": int(nodes)},
        max_targets=int(max_targets),
        max_depth=int(max_depth),
        max_expansions=int(max_expansions),
    )

def _load_crossfeed_settings(
    data: dict[str, object],
    *,
    mode: str,
    verification: VerificationSettings | None,
) -> CrossFeedSettings | None:
    """Load decision-inert cross-feed settings.

    Cross-feed v1 performs no engine dispatch. It only composes evidence from
    the already-qualified VERIFY / optional REFINE path, so enabling it without
    VERIFY would create an empty feature whose name overstates its authority.
    """

    raw_value = data.get("crossfeed")
    if raw_value is None:
        return None
    if mode not in ("shadow", "active"):
        raise RuntimeError("crossfeed settings are supported only in shadow/active modes")

    raw = _require_object(raw_value, "crossfeed")
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        raise RuntimeError("crossfeed.enabled must be a boolean")
    if not enabled:
        return None
    if verification is None or not verification.enabled:
        raise RuntimeError("crossfeed requires verification.enabled")

    policy = raw.get("policy", "typed_verify_refine_v1")
    if policy != "typed_verify_refine_v1":
        raise RuntimeError(
            "crossfeed.policy currently supports exactly 'typed_verify_refine_v1'"
        )
    unknown = sorted(set(raw) - {"enabled", "policy"})
    if unknown:
        raise RuntimeError(f"crossfeed contains unsupported keys: {unknown}")

    return CrossFeedSettings(enabled=True, policy=str(policy))


def _load_counterfactual_settings(
    data: dict[str, object],
    *,
    mode: str,
    crossfeed: CrossFeedSettings | None,
) -> CounterfactualSettings | None:
    """Load the counterfactual-only hybrid decision policy.

    This layer consumes an already-built CrossFeedView. It performs no engine
    search and grants no outward authority, so enabling it without cross-feed
    would make its evidence boundary undefined.
    """

    raw_value = data.get("counterfactual")
    if raw_value is None:
        return None
    if mode not in ("shadow", "active"):
        raise RuntimeError(
            "counterfactual settings are supported only in shadow/active modes"
        )

    raw = _require_object(raw_value, "counterfactual")
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        raise RuntimeError("counterfactual.enabled must be a boolean")
    if not enabled:
        return None
    if crossfeed is None or not crossfeed.enabled:
        raise RuntimeError("counterfactual requires crossfeed.enabled")

    policy = raw.get("policy", "unanimous_verify_v1")
    if policy != "unanimous_verify_v1":
        raise RuntimeError(
            "counterfactual.policy currently supports exactly 'unanimous_verify_v1'"
        )
    unknown = sorted(set(raw) - {"enabled", "policy"})
    if unknown:
        raise RuntimeError(f"counterfactual contains unsupported keys: {unknown}")

    return CounterfactualSettings(enabled=True, policy=str(policy))


def _load_hybrid_authority_settings(
    data: dict[str, object],
    *,
    mode: str,
    counterfactual: CounterfactualSettings | None,
    resource_measurement: ResourceMeasurementSettings,
) -> HybridAuthoritySettings | None:
    """Load the first live hybrid-authority gate."""

    raw_value = data.get("hybrid_authority")
    if raw_value is None:
        return None
    if mode != "active":
        raise RuntimeError("hybrid_authority settings are supported only in active mode")
    raw = _require_object(raw_value, "hybrid_authority")
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        raise RuntimeError("hybrid_authority.enabled must be a boolean")
    if not enabled:
        return None
    if counterfactual is None or not counterfactual.enabled:
        raise RuntimeError("hybrid_authority requires counterfactual.enabled")
    if not resource_measurement.enabled:
        raise RuntimeError("hybrid_authority requires resource_measurement.enabled")
    if not resource_measurement.require_cpu_for_claim:
        raise RuntimeError(
            "hybrid_authority v0 requires resource_measurement.require_cpu_for_claim=true"
        )
    if resource_measurement.require_gpu_for_claim:
        raise RuntimeError(
            "hybrid_authority v0 has no GPU device-time provider and cannot require GPU claims"
        )

    policy = raw.get("policy", "bounded_preanchor_v0")
    if policy not in ("bounded_preanchor_v0", "clocked_staged_preanchor_v1"):
        raise RuntimeError(
            "hybrid_authority.policy must be 'bounded_preanchor_v0' or "
            "'clocked_staged_preanchor_v1'"
        )

    if policy == "bounded_preanchor_v0":
        request_class = raw.get("request_class", "movetime_v0")
        if request_class != "movetime_v0":
            raise RuntimeError(
                "bounded_preanchor_v0 request_class supports exactly 'movetime_v0'"
            )
        terminal_source_policy = raw.get("terminal_source_policy", "base_verify_v0")
        allow_skipped = raw.get("allow_skipped_extension_authority", False)
        if terminal_source_policy != "base_verify_v0" or allow_skipped is not False:
            raise RuntimeError(
                "bounded_preanchor_v0 cannot consume staged/skip authority semantics"
            )
    else:
        request_class = raw.get("request_class", "online_time_v1")
        if request_class != "online_time_v1":
            raise RuntimeError(
                "clocked_staged_preanchor_v1 supports exactly request_class='online_time_v1'"
            )
        terminal_source_policy = raw.get(
            "terminal_source_policy", "route_bound_staged_v1"
        )
        if terminal_source_policy != "route_bound_staged_v1":
            raise RuntimeError(
                "clocked_staged_preanchor_v1 requires terminal_source_policy="
                "'route_bound_staged_v1'"
            )
        allow_skipped = raw.get("allow_skipped_extension_authority", False)
        if allow_skipped is not False:
            raise RuntimeError(
                "M14-G3 does not license SKIP-derived move authority; "
                "allow_skipped_extension_authority must be false"
            )

    unknown = sorted(
        set(raw)
        - {
            "enabled",
            "policy",
            "request_class",
            "terminal_source_policy",
            "allow_skipped_extension_authority",
        }
    )
    if unknown:
        raise RuntimeError(f"hybrid_authority contains unsupported keys: {unknown}")
    return HybridAuthoritySettings(
        enabled=True,
        policy=str(policy),
        request_class=str(request_class),
        terminal_source_policy=str(terminal_source_policy),
        allow_skipped_extension_authority=bool(allow_skipped),
    )


def load_runtime_config(path: Path) -> RuntimeConfig:
    path = path.resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load runtime config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("runtime config root must be an object")
    version = data.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version not in (1, 2):
        raise RuntimeError(f"unsupported runtime config schema_version: {version!r}")

    root_value = data.get("root", "..")
    if not isinstance(root_value, str) or not root_value:
        raise RuntimeError("runtime config root must be a non-empty path string")
    root = (path.parent / root_value).resolve()

    if version == 1:
        if "online_time" in data:
            raise RuntimeError("online_time requires schema_version 2; legacy profiles are unchanged")
        anchor, specs = _load_legacy_backends(data, root)
        return RuntimeConfig(path=path, root=root, mode="anchor", anchor=anchor, backends=specs)

    mode = data.get("mode")
    if mode not in EXECUTION_MODES:
        raise RuntimeError(f"runtime config mode must be one of {list(EXECUTION_MODES)}, got {mode!r}")
    anchor, specs = _load_instances(data, root)

    shadow: ShadowSettings | None = None
    if mode in ("shadow", "active"):
        shadow = _load_shadow_settings(data, specs=specs, anchor=anchor, root=root)
    elif "shadow" in data:
        raise RuntimeError("shadow settings are only valid in shadow/active mode")

    verification = _load_verification_settings(data, mode=str(mode), shadow=shadow)
    refinement = _load_refinement_settings(
        data,
        mode=str(mode),
        shadow=shadow,
        verification=verification,
    )
    crossfeed = _load_crossfeed_settings(
        data,
        mode=str(mode),
        verification=verification,
    )
    counterfactual = _load_counterfactual_settings(
        data,
        mode=str(mode),
        crossfeed=crossfeed,
    )

    raw_resource = data.get("resource_measurement")
    if raw_resource is not None:
        raw_resource = _require_object(raw_resource, "resource_measurement")
    try:
        resource_measurement = ResourceMeasurementSettings.from_config(
            raw_resource,
            mode=str(mode),
        )
    except ResourceMeasurementError as exc:
        raise RuntimeError(str(exc)) from exc

    hybrid_authority = _load_hybrid_authority_settings(
        data,
        mode=str(mode),
        counterfactual=counterfactual,
        resource_measurement=resource_measurement,
    )
    if (
        hybrid_authority is not None
        and hybrid_authority.policy == "bounded_preanchor_v0"
        and verification is not None
        and verification.staged_extension is not None
    ):
        raise RuntimeError(
            "M14-G1 staged VERIFY remains incompatible with frozen "
            "bounded_preanchor_v0 authority"
        )
    if hybrid_authority is not None and hybrid_authority.policy == "clocked_staged_preanchor_v1":
        if verification is None or verification.staged_extension is None:
            raise RuntimeError(
                "clocked_staged_preanchor_v1 requires verification.staged_extension"
            )
        if crossfeed is None or counterfactual is None:
            raise RuntimeError(
                "clocked_staged_preanchor_v1 requires crossfeed and counterfactual evidence"
            )
    if (
        hybrid_authority is not None
        and refinement is not None
        and refinement.max_depth > 2
    ):
        raise RuntimeError(
            "M14-D recursive REFINE is evidence-only: hybrid_authority profiles "
            "must keep refinement.max_depth=2 until a later authority milestone"
        )

    budget = data.get("budget")
    if budget is not None:
        budget = _require_object(budget, "budget")
    routing = data.get("routing")
    if routing is not None:
        routing = _require_object(routing, "routing")
    if mode == "active" and routing is None:
        raise RuntimeError("active mode requires an explicit routing configuration")
    if mode == "active" and budget is None:
        raise RuntimeError("active mode requires an explicit budget configuration")

    if mode == "active" and verification is not None:
        raw_verify_reserve = budget.get("verification_reserve_fraction", 0.0)
        if (
            isinstance(raw_verify_reserve, bool)
            or not isinstance(raw_verify_reserve, (int, float))
            or not math.isfinite(float(raw_verify_reserve))
            or float(raw_verify_reserve) <= 0.0
        ):
            raise RuntimeError(
                "active verification requires budget.verification_reserve_fraction > 0"
            )
    if mode == "active" and refinement is not None:
        raw_refine_reserve = budget.get("refinement_reserve_fraction", 0.0)
        if (
            isinstance(raw_refine_reserve, bool)
            or not isinstance(raw_refine_reserve, (int, float))
            or not math.isfinite(float(raw_refine_reserve))
            or float(raw_refine_reserve) <= 0.0
        ):
            raise RuntimeError(
                "active refinement requires budget.refinement_reserve_fraction > 0"
            )

    try:
        online_time = OnlineTimeSettings.from_config(data.get("online_time"))
    except OnlineTimeError as exc:
        raise RuntimeError(str(exc)) from exc
    if (
        hybrid_authority is not None
        and hybrid_authority.policy == "clocked_staged_preanchor_v1"
        and online_time is None
    ):
        raise RuntimeError(
            "clocked_staged_preanchor_v1 requires online_time"
        )
    if online_time is not None:
        if mode != "active":
            raise RuntimeError("ONLINE-1 requires active resource routing")
        if (
            hybrid_authority is not None
            and hybrid_authority.policy != "clocked_staged_preanchor_v1"
        ):
            raise RuntimeError(
                "ONLINE timing may grant hybrid authority only through "
                "clocked_staged_preanchor_v1"
            )
        if hybrid_authority is not None:
            if verification is None or verification.staged_extension is None:
                raise RuntimeError(
                    "clocked staged authority requires a configured staged VERIFY extension"
                )
            if routing is None or routing.get("policy") != "unified_value_v1":
                raise RuntimeError(
                    "clocked staged authority requires routing.policy='unified_value_v1'"
                )
            if hybrid_authority.allow_skipped_extension_authority:
                raise RuntimeError(
                    "M14-G3 cannot authorize a skipped staged extension"
                )
        if budget is None or budget.get("gpu_ms", 0) != 0:
            raise RuntimeError("ONLINE-1 supports CPU-only envelopes")
        for key in ("wall_ms", "cpu_ms", "gpu_ms", "verification_reserve_fraction",
                    "refinement_reserve_fraction", "controller_overhead_reserve_ms"):
            value = budget.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise RuntimeError(f"ONLINE-1 budget.{key} must be finite numeric, not boolean")
        from controller.budget import ResourceEnvelope, BudgetError
        try:
            declared = ResourceEnvelope.from_config(budget)
            if declared.wall_ms <= 0 or declared.cpu_ms <= 0:
                raise RuntimeError("ONLINE-1 requires positive wall/CPU caps")
        except BudgetError as exc:
            raise RuntimeError(str(exc)) from exc
        if refinement is not None:
            raise RuntimeError("ONLINE-1 clock profile does not qualify recursive REFINE")
        if not resource_measurement.enabled or not resource_measurement.require_cpu_for_claim:
            raise RuntimeError("ONLINE-1 requires physical CPU measurement for claims")
        # ONLINE-1 claims a CPU-only envelope. When an LC0 backend is configured,
        # fail closed unless its vendored implementation is unambiguously CPU-only.
        # Legacy/fake fixtures may omit Backend; the shipped ONLINE-1 profile pins it
        # explicitly, and ONLINE-2 will tighten device/profile identity further.
        cpu_only_lc0_backends = {
            "random", "trivial", "blas", "eigen", "onnx-cpu", "tensorflow-cc-cpu"
        }
        for spec in specs.values():
            if spec.family != "lc0":
                continue
            backend = spec.options.get("Backend")
            if not isinstance(backend, str) or not backend:
                raise RuntimeError(
                    "ONLINE-1 CPU-only envelope requires every LC0 instance to set "
                    f"an explicit CPU Backend; {spec.name} omitted Backend"
                )
            if backend.lower() not in cpu_only_lc0_backends:
                raise RuntimeError(
                    "ONLINE-1 CPU-only envelope rejects accelerator/unknown LC0 Backend "
                    f"{backend!r} for {spec.name}; allowed configured backends are "
                    f"{sorted(cpu_only_lc0_backends)}"
                )

    return RuntimeConfig(
        path=path,
        root=root,
        mode=mode,
        anchor=anchor,
        backends=specs,
        shadow=shadow,
        verification=verification,
        refinement=refinement,
        crossfeed=crossfeed,
        counterfactual=counterfactual,
        hybrid_authority=hybrid_authority,
        resource_measurement=resource_measurement,
        budget=budget,
        routing=routing,
        online_time=online_time,
    )


@dataclass
class ShadowHealth:
    """Observational health of one shadow instance.

    A shadow failure is recorded evidence. It never grants outward authority and
    never fails the anchor search.
    """

    instance: str
    alive: bool = True
    failure: str | None = None
    failed_generation: int | None = None

    def snapshot(self) -> dict[str, object]:
        return {
            "instance": self.instance,
            "alive": self.alive,
            "failure": self.failure,
            "failed_generation": self.failed_generation,
        }


class BackendManager:
    """Own every managed engine process and separate authority from observation."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.backends: dict[str, UciProcess] = {}
        self._lock = threading.RLock()
        self._started = False
        self._closing = False
        self._unhealthy_reason: str | None = None
        self._failure_handler: Callable[[str, int | None], None] | None = None
        self._shadow_health: dict[str, ShadowHealth] = {
            spec.name: ShadowHealth(instance=spec.name)
            for spec in config.instances_with_role("shadow")
        }
        self._shadow_exit_handler: Callable[[str, int | None, int | None], None] | None = None
        self._observer: Callable[[str, int, str, float], None] | None = None
        #: Observer exceptions, kept as evidence. An observer failure costs
        #: replay evidence, never the outward search, so it is recorded here
        #: rather than raised.
        self._observer_failures: list[str] = []
        self._deferred_observers: list[DeferredObserver] = []
        self._online_clock: ClockSearch | None = None

        # Provenance is captured here, before `start()` launches anything. It
        # used to be computed when the shadow coordinator was constructed --
        # after every process was already running -- so a config or binary
        # replaced during that window would be recorded in the manifest even
        # though the running controller and processes came from the old bytes.
        self.config_sha256: str = _hash_file(config.path) or ""
        self.engine_identity: dict[str, Any] = {
            name: _engine_identity(spec)
            for name, spec in sorted(config.backends.items())
        }
        self._position_command: str | None = None
        # The GUI owns the variant, via `setoption name UCI_Chess960`. A config
        # that starts the engines in Chess960 would leave them searching FRC
        # while this controller parsed every position as standard chess and
        # recorded the wrong variant in every replay, so such a config is
        # refused at load rather than silently disagreed with.
        self._chess960 = False

        # Deterministic startup/shutdown ordering: the outward anchor starts
        # first so its readiness is never gated behind observational workers.
        anchor_first = [config.anchor]
        anchor_first += [name for name in config.backends if name != config.anchor]
        self._startup_order: tuple[str, ...] = tuple(anchor_first)

    @classmethod
    def from_path(cls, path: Path) -> "BackendManager":
        return cls(load_runtime_config(path))

    # ------------------------------------------------------------------
    # identity
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self.config.mode

    @property
    def anchor_name(self) -> str:
        return self.config.anchor

    @property
    def shadow_instances(self) -> tuple[str, ...]:
        if self.config.shadow is None:
            return ()
        return tuple(
            self.config.shadow.instance(owner) for owner in self.config.shadow.owners
        )

    @property
    def authority_instances(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in self._startup_order
            if self.config.backends[name].role in AUTHORITY_ROLES
        )

    @property
    def position_command(self) -> str | None:
        with self._lock:
            return self._position_command

    @property
    def chess960(self) -> bool:
        with self._lock:
            return self._chess960

    def spec(self, instance: str) -> BackendSpec:
        try:
            return self.config.backends[instance]
        except KeyError as exc:
            raise RuntimeError(f"unknown engine instance: {instance!r}") from exc

    def process(self, instance: str) -> UciProcess:
        try:
            return self.backends[instance]
        except KeyError as exc:
            raise RuntimeError(f"engine instance is not running: {instance!r}") from exc

    def process_pid(self, instance: str) -> int | None:
        """Return the live child PID used by the physical resource meter."""
        process = self.backends.get(instance)
        return None if process is None else process.pid

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------

    def set_failure_handler(self, handler: Callable[[str, int | None], None]) -> None:
        with self._lock:
            self._failure_handler = handler

    def set_shadow_exit_handler(
        self, handler: Callable[[str, int | None, int | None], None] | None
    ) -> None:
        """Install the shadow coordinator's unexpected-exit notification hook."""
        with self._lock:
            self._shadow_exit_handler = handler

    def set_instance_observer(
        self, observer: Callable[[str, int, str, float], None] | None
    ) -> None:
        """Observe every dispatched search line as it is dequeued by the reader.

        The observer runs on the owning process's stdout reader thread. It must
        therefore never block: implementations hand work to their own writer
        thread.
        """
        with self._lock:
            self._observer = observer

    @property
    def healthy(self) -> bool:
        """Authority health.

        Shadow instances are deliberately excluded: an observational worker
        death is recorded evidence and must not fail the outward search.
        """
        with self._lock:
            if not self._started or self._closing or self._unhealthy_reason is not None:
                return False
            authority = self.authority_instances
            if len(authority) != len([
                name for name in self.config.backends if self.config.backends[name].role in AUTHORITY_ROLES
            ]):
                return False
            return all(
                name in self.backends and self.backends[name].alive for name in authority
            )

    @property
    def unhealthy_reason(self) -> str | None:
        with self._lock:
            return self._unhealthy_reason

    def shadow_health(self) -> dict[str, ShadowHealth]:
        with self._lock:
            return {name: ShadowHealth(**vars(health)) for name, health in self._shadow_health.items()}

    def shadow_available(self, instance: str) -> bool:
        with self._lock:
            health = self._shadow_health.get(instance)
            if health is None or not health.alive:
                return False
            process = self.backends.get(instance)
            return process is not None and process.alive

    def observer_failures(self) -> tuple[str, ...]:
        """Telemetry-observer exceptions recorded so far, newest last."""
        with self._lock:
            return tuple(self._observer_failures)

    def record_shadow_failure(self, instance: str, message: str, *, generation: int | None = None) -> None:
        with self._lock:
            health = self._shadow_health.get(instance)
            if health is None:
                raise RuntimeError(f"unknown shadow instance: {instance!r}")
            health.alive = False
            if health.failure is None:
                health.failure = message
                health.failed_generation = generation

    @property
    def anchor(self) -> UciProcess:
        try:
            return self.backends[self.config.anchor]
        except KeyError as exc:
            raise RuntimeError("anchor process is not available") from exc

    def _notify_failure(self, message: str, token: int | None) -> None:
        handler: Callable[[str, int | None], None] | None
        with self._lock:
            if self._unhealthy_reason is None:
                self._unhealthy_reason = message
            handler = self._failure_handler
        if handler is not None:
            handler(message, token)

    def _handle_exit(self, name: str, rc: int | None, active_token: int | None) -> None:
        with self._lock:
            if self._closing:
                return
            spec = self.config.backends.get(name)
            shadow_handler = self._shadow_exit_handler
        if spec is not None and spec.role == "shadow":
            # Observational health only. The outward search keeps running.
            self.record_shadow_failure(
                name,
                f"shadow instance {name} exited unexpectedly; rc={rc}",
                generation=active_token,
            )
            if shadow_handler is not None:
                try:
                    shadow_handler(name, rc, active_token)
                except Exception:  # pragma: no cover - coordinator isolation
                    pass
            return
        self._notify_failure(f"backend {name} exited unexpectedly; rc={rc}", active_token)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("backend runtime already started")
            self._started = True
            self._closing = False
            self._unhealthy_reason = None

        try:
            for name in self._startup_order:
                spec = self.config.backends[name]
                try:
                    process = UciProcess(
                        name=name,
                        binary=spec.binary,
                        cwd=spec.cwd,
                        args=list(spec.args),
                        environment=dict(spec.environment),
                        on_exit=self._handle_exit,
                    )
                    self.backends[name] = process
                    process.start()
                    process.configure(spec.options)
                except Exception as exc:
                    if spec.role in AUTHORITY_ROLES:
                        raise
                    # An observational worker that dies during `uci`, times out,
                    # or rejects an option is exactly the failure the role split
                    # exists for. Letting it reach the outer handler closed the
                    # already-healthy anchor and aborted the whole controller,
                    # so a shadow outage denied outward service entirely -- the
                    # opposite of what every post-startup path does.
                    message = f"shadow instance failed during startup: {type(exc).__name__}: {exc}"
                    self._diagnostic_startup_failure(name, message)
                    self.record_shadow_failure(name, message)
                    failed = self.backends.pop(name, None)
                    if failed is not None:
                        try:
                            failed.close()
                        except Exception:  # pragma: no cover - best effort
                            pass
            self.ready_all()
            if self.config.online_time is not None:
                for process in self.backends.values():
                    process.timeout = min(process.timeout, self.config.online_time.quiesce_budget_ms / 1000)
        except Exception as exc:
            self.close()
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"backend startup failed: {exc}") from exc

    def _diagnostic_startup_failure(self, instance: str, message: str) -> None:
        with self._lock:
            if len(self._observer_failures) < 64:
                self._observer_failures.append(f"{instance}: {message}")

    def _require_healthy(self) -> None:
        if not self.healthy:
            reason = self.unhealthy_reason or "backend runtime is not healthy"
            raise RuntimeError(reason)

    def _require_started(self) -> None:
        with self._lock:
            if not self._started or self._closing:
                raise RuntimeError("backend runtime is not active")

    def _for_each_instance(
        self,
        action: Callable[[str, UciProcess], None],
        *,
        label: str,
        instances: Iterable[str] | None = None,
    ) -> None:
        """Apply one synchronization action across managed instances.

        Authority failures escalate and fail closed. Shadow failures are
        recorded as observational evidence so an outward search survives them.
        """
        names = self._startup_order if instances is None else tuple(instances)
        for name in names:
            process = self.backends.get(name)
            if process is None:
                continue
            spec = self.config.backends[name]
            if spec.role == "shadow":
                if not self.shadow_available(name):
                    continue
                try:
                    action(name, process)
                except UciProcessError as exc:
                    self.record_shadow_failure(name, f"{label} failed: {exc}")
                continue
            try:
                action(name, process)
            except UciProcessError as exc:
                self._notify_failure(f"{label} failed for {name}: {exc}", None)
                raise RuntimeError(str(exc)) from exc

    def ready_all(self) -> None:
        """Synchronize every live backend.

        Startup/state-mutation barriers use this stronger form because a shadow
        that is about to receive synchronized state must either acknowledge the
        barrier or be quarantined before the mutation proceeds.
        """
        self._require_started()
        self._for_each_instance(lambda name, process: process.ready(), label="backend readiness")
        self._require_healthy()

    def ready_authority(self) -> None:
        """Synchronize only authority-bearing instances for external isready.

        Observational shadows are intentionally unable to delay the outward UCI
        readiness path. Their health is still checked/quarantined at the
        synchronization barriers that actually mutate shared state.
        """
        self._require_started()
        self._for_each_instance(
            lambda name, process: process.ready(),
            label="authority readiness",
            instances=self.authority_instances,
        )
        self._require_healthy()

    def set_chess960(self, enabled: bool) -> None:
        self._require_healthy()
        self._for_each_instance(
            lambda name, process: process.set_option("UCI_Chess960", enabled),
            label="UCI_Chess960 synchronization",
        )
        with self._lock:
            self._chess960 = bool(enabled)
        self.ready_all()

    def new_game(self) -> None:
        self._require_healthy()
        self._for_each_instance(lambda name, process: process.new_game(), label="ucinewgame synchronization")
        with self._lock:
            self._position_command = None
        self.ready_all()

    def set_position(self, command: str) -> None:
        self._require_healthy()
        self._for_each_instance(
            lambda name, process: process.send_position(command),
            label="position synchronization",
        )
        with self._lock:
            self._position_command = command

    def set_shadow_position(self, instance: str, command: str) -> None:
        """Temporarily position one idle observational worker.

        The manager's globally synchronized position is not changed. REFINE
        callers must restore the worker before releasing the generation.
        """

        spec = self.spec(instance)
        if spec.role != "shadow":
            raise RuntimeError(
                f"instance {instance!r} is not an observational shadow worker"
            )
        if not self.shadow_available(instance):
            raise RuntimeError(f"shadow instance is unavailable: {instance}")
        try:
            parse_position_command(
                command,
                variant="chess960" if self.chess960 else "standard",
            )
        except SearchRequestError as exc:
            raise RuntimeError(f"invalid shadow position command: {exc}") from exc

        process = self.backends.get(instance)
        if process is None or not process.alive:
            raise RuntimeError(f"shadow instance is unavailable: {instance}")
        if process.active_search:
            raise RuntimeError(
                f"shadow instance {instance!r} cannot be repositioned during active search"
            )
        try:
            process.send_position(command)
            process.ready(
                timeout=(
                    None
                    if self.config.shadow is None
                    else self.config.shadow.oracle_timeout_s
                )
            )
        except UciProcessError as exc:
            self.record_shadow_failure(
                instance, f"shadow position synchronization failed: {exc}"
            )
            raise RuntimeError(str(exc)) from exc

    def restore_shadow_position(self, instance: str) -> None:
        """Restore one idle shadow worker to the authoritative external state."""

        with self._lock:
            command = self._position_command or "position startpos"
        self.set_shadow_position(instance, command)

    def legal_moves_at_shadow_position(
        self,
        *,
        instance: str,
        position_command: str,
        timeout: float | None = None,
    ) -> tuple[str, ...]:
        """Run the configured Stockfish shadow oracle at a descendant position.

        The global synchronized position is never changed. Restoration runs in
        all cases; a restoration failure quarantines the shadow rather than
        pretending it is synchronized.
        """

        if self.config.shadow is None or instance != self.config.shadow.oracle:
            raise RuntimeError(
                "descendant legal-move oracle must use configured shadow.oracle"
            )
        spec = self.spec(instance)
        if spec.role != "shadow" or spec.family != "stockfish":
            raise RuntimeError(
                "descendant legal-move oracle requires configured Stockfish shadow"
            )

        primary_error: Exception | None = None
        result: tuple[str, ...] | None = None
        try:
            self.set_shadow_position(instance, position_command)
            result = self.legal_root_moves(instance=instance, timeout=timeout)
        except Exception as exc:
            primary_error = exc
        try:
            self.restore_shadow_position(instance)
        except Exception as restore_exc:
            if primary_error is None:
                raise RuntimeError(
                    f"could not restore shadow oracle {instance!r}: {restore_exc}"
                ) from restore_exc
        if primary_error is not None:
            raise primary_error
        assert result is not None
        return result
    # ------------------------------------------------------------------
    # legal-root oracle
    # ------------------------------------------------------------------

    @property
    def oracle_name(self) -> str:
        """Instance that answers ``go perft 1``.

        In shadow/active mode this is a dedicated Stockfish shadow process so
        the outward anchor is never blocked by root qualification.
        """
        if self.config.shadow is not None:
            return self.config.shadow.oracle
        return self.config.anchor

    def legal_root_moves(self, *, instance: str | None = None, timeout: float | None = None) -> tuple[str, ...]:
        """Return canonical legal root moves from a Stockfish depth-1 perft oracle."""
        self._require_healthy()
        name = self.oracle_name if instance is None else instance
        spec = self.spec(name)
        if spec.family != "stockfish":
            raise RuntimeError("root legal-move oracle requires a Stockfish-family instance")
        process = self.backends.get(name)
        if process is None or not process.alive:
            raise RuntimeError(f"legal-root oracle instance is unavailable: {name}")
        if timeout is None:
            timeout = 10.0 if self.config.shadow is None else self.config.shadow.oracle_timeout_s

        shadow_oracle = spec.role == "shadow"
        try:
            lines = process.run_idle_request(
                "go perft 1",
                lambda line: _PERFT_TOTAL_RE.fullmatch(line) is not None,
                label=f"{name} go perft 1",
                timeout=timeout,
            )
        except UciProcessError as exc:
            message = f"legal-root oracle process failure: {exc}"
            if shadow_oracle:
                self.record_shadow_failure(name, message)
                if self.config.online_time is not None:
                    process.kill_now()  # An idle perft transaction has no search token.
            else:
                self._notify_failure(message, None)
            raise RuntimeError(str(exc)) from exc

        def _fail(message: str) -> None:
            if shadow_oracle:
                self.record_shadow_failure(name, message)
            else:
                self._notify_failure(message, None)

        roots: list[str] = []
        seen: set[str] = set()
        total: int | None = None
        for line in lines:
            total_match = _PERFT_TOTAL_RE.fullmatch(line)
            if total_match is not None:
                total = int(total_match.group(1))
                continue
            root_match = _PERFT_ROOT_RE.fullmatch(line)
            if root_match is None:
                # Stockfish may emit unrelated informational material (for
                # example network-verification strings) before perft output.
                continue
            move, count_raw = root_match.groups()
            count = int(count_raw)
            if count != 1:
                _fail(f"legal-root oracle returned depth-1 count {count} for {move}")
                raise RuntimeError(
                    f"Stockfish perft-1 root {move} reported count {count}, expected 1"
                )
            if move in seen:
                _fail(f"legal-root oracle returned duplicate root {move}")
                raise RuntimeError(f"Stockfish perft-1 returned duplicate root {move}")
            seen.add(move)
            roots.append(move)

        if total is None:
            _fail("legal-root oracle omitted Nodes searched total")
            raise RuntimeError("Stockfish perft-1 omitted Nodes searched total")
        if total != len(roots):
            _fail(f"legal-root oracle total mismatch: total={total}, roots={len(roots)}")
            raise RuntimeError(
                f"Stockfish perft-1 total mismatch: Nodes searched={total}, "
                f"parsed roots={len(roots)}"
            )
        return tuple(roots)

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    def _observed_callbacks(
        self,
        instance: str,
        on_info: Callable[[int, str], None],
        on_complete: Callable[[int, str], None],
    ) -> tuple[Callable[[int, str], None], Callable[[int, str], None]]:
        def _observe(token: int, line: str, observed: float | None = None) -> None:
            """Run the telemetry observer without letting it reach authority.

            An observer exception must never preempt the authoritative callback.
            It did: `observer(...)` raised, `on_complete` was skipped, and for
            the anchor's `bestmove` the frontend stayed in SEARCHING forever --
            observational machinery taking down the outward search, which is
            exactly what the authority/observation split exists to prevent.
            """
            with self._lock:
                observer = self._observer
            if observer is None:
                return
            try:
                observer(instance, token, line, time.monotonic() if observed is None else observed)
            except Exception as exc:  # observation is never authoritative
                message = f"telemetry observer failed: {type(exc).__name__}: {exc}"
                try:
                    self.record_shadow_failure(instance, message)
                except Exception:
                    # The anchor has no observational health record, and
                    # `record_shadow_failure` raises for it. Losing the
                    # authority stream's replay evidence is real and is
                    # recorded below, but it can never withhold the
                    # outward answer.
                    pass
                with self._lock:
                    if len(self._observer_failures) < 64:
                        self._observer_failures.append(f"{instance}: {message}")

        def _info(token: int, line: str) -> None:
            _observe(token, line)
            on_info(token, line)

        def _complete(token: int, line: str) -> None:
            _observe(token, line)
            on_complete(token, line)

        return _info, _complete

    def start_anchor_search(
        self,
        command: str,
        *,
        token: int,
        on_info: Callable[[int, str], None],
        on_complete: Callable[[int, str], None],
        clock: ClockSearch | None = None,
        observe_online: bool = False,
        on_observation_end: Callable[[int, str, int], None] | None = None,
    ) -> None:
        self._require_healthy()
        if clock is not None:
            with self._lock:
                previous = self._online_clock
                if previous is not None:
                    previous.measurement_superseded.set()
                self._online_clock = clock
            # A short-lived tail guard remains active even after the outward
            # answer, but token-scoped kills cannot touch the next generation.
            def expire_shadows():
                clock.finished.wait(max(0, clock.plan.hard_deadline - time.monotonic()))
                time.sleep(max(0, clock.plan.hard_deadline - time.monotonic()))
                for instance in self.shadow_instances:
                    process = self.backends.get(instance)
                    if process is not None and process.kill_search(token):
                        self.record_shadow_failure(instance, "clock hard deadline while stopping", generation=token)
            threading.Thread(target=expire_shadows, name=f"allfather-clock-tail-{token}", daemon=True).start()
        deferred = None
        if clock is None:
            info_cb, complete_cb = self._observed_callbacks(self.config.anchor, on_info, on_complete)
        else:
            # Online authority never waits for an observational callback. One
            # bounded FIFO drains the original receipt timestamps afterwards.
            if observe_online:
                if on_observation_end is None:
                    raise RuntimeError("online replay requires an observation completion hook")
                def observe(tok: int, line: str, observed: float) -> None:
                    with self._lock:
                        observer = self._observer
                    if observer is not None:
                        observer(self.config.anchor, tok, line, observed)
                deferred = DeferredObserver(observe=observe, finished=on_observation_end)
                self._deferred_observers = [o for o in self._deferred_observers if not o.done.is_set()]
                self._deferred_observers.append(deferred)
            def info_cb(tok: int, line: str) -> None:
                observed = time.monotonic()
                if deferred is not None:
                    deferred.submit(tok, line, observed)
                on_info(tok, line)
            def complete_cb(tok: int, line: str) -> None:
                observed = time.monotonic()
                on_complete(tok, line)
                if deferred is not None:
                    deferred.submit(tok, line, observed)
        try:
            kwargs = {} if clock is None else {
                "timeout": max(0.001, clock.plan.hard_deadline - time.monotonic()),
                "permit": clock.work_open,
            }
            self.anchor.start_search(command, token=token, on_info=info_cb,
                                     on_complete=complete_cb, **kwargs)
        except UciProcessError as exc:
            if deferred is not None:
                deferred.abort()
            self._notify_failure(f"anchor search dispatch failed: {exc}", token)
            raise RuntimeError(str(exc)) from exc

    def stop_anchor_for(self, token: int, *, timeout: float) -> bool:
        try:
            return self.anchor.stop_search(token, timeout=timeout)
        except UciProcessError:
            # The independent hard watchdog owns escalation; this writer must
            # not attach a late failure to a later frontend generation.
            return False

    def fail_clock_search(self, token: int, reason: str) -> None:
        # Called only after the frontend atomically retires this generation.
        # No successful new go can race into this failed runtime.
        self.anchor.kill_now()
        self._notify_failure(reason, token)
        for observer in self._deferred_observers:
            observer.abort()

    def start_shadow_search(
        self,
        instance: str,
        command: str,
        *,
        token: int,
        on_info: Callable[[int, str], None],
        on_complete: Callable[[int, str], None],
    ) -> bool:
        """Dispatch one restricted observational search.

        Returns ``False`` and records a shadow failure instead of raising: a
        shadow dispatch problem is evidence, never an authority failure.
        """
        spec = self.spec(instance)
        if spec.role != "shadow":
            raise RuntimeError(f"instance {instance!r} is not a shadow worker")
        if not self.shadow_available(instance):
            return False
        info_cb, complete_cb = self._observed_callbacks(instance, on_info, on_complete)
        kwargs = {}
        if self.config.online_time is not None:
            clock = self._online_clock
            if clock is None or clock.plan.generation != token or not clock.work_open():
                return False
            kwargs = {"permit": clock.work_open,
                      "timeout": max(0.001, clock.plan.hard_deadline - time.monotonic())}
        try:
            self.backends[instance].start_search(
                command,
                token=token,
                on_info=info_cb,
                on_complete=complete_cb,
                **kwargs,
            )
        except UciProcessError as exc:
            self.record_shadow_failure(instance, f"shadow dispatch failed: {exc}", generation=token)
            return False
        return True

    def stop_instance_for(self, instance: str, token: int, *, timeout: float) -> None:
        process = self.backends.get(instance)
        if process is not None:
            try:
                process.stop_search(token, timeout=timeout)
            except UciProcessError:
                pass  # Hard expiry/quiesce owns quarantine, not a late writer.

    def stop_instance(self, instance: str) -> None:
        process = self.backends.get(instance)
        if process is None:
            return
        try:
            process.stop()
        except UciProcessError as exc:
            spec = self.config.backends[instance]
            if spec.role == "shadow":
                self.record_shadow_failure(instance, f"shadow stop failed: {exc}")
                return
            self._notify_failure(f"{instance} stop failed: {exc}", process.active_token)
            raise RuntimeError(str(exc)) from exc

    def stop_anchor(self) -> None:
        self._require_started()
        self.stop_instance(self.config.anchor)

    def ponderhit_anchor(self) -> None:
        self._require_started()
        try:
            self.anchor.ponderhit()
        except UciProcessError as exc:
            self._notify_failure(f"anchor ponderhit failed: {exc}", self.anchor.active_token)
            raise RuntimeError(str(exc)) from exc

    def close(self) -> None:
        with self._lock:
            if self._closing:
                return
            self._closing = True
        for observer in self._deferred_observers:
            observer.abort()
        try:
            for name in reversed(self._startup_order):
                process = self.backends.get(name)
                if process is not None:
                    process.close()
        finally:
            with self._lock:
                self._started = False
