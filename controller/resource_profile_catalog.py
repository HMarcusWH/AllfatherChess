"""Frozen M14-J resource-profile catalog.

The catalog is an identity/qualification layer, not an allocator.  It names
prequalified engine operating points and compositions and can prove that the
first catalog exactly represents the frozen ENGINE-OPT-v2 runtime.  It grants
neither compute authorization nor outward move authority.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from controller.decision import canonical_digest
from controller.resource_profiles import (
    CompositionProfile,
    EngineResourceProfile,
    MutationBoundary,
    OrchestrationContractError,
    _git_oid,
    _mapping,
    _option_scalar,
    _positive_int,
    _reject_unknown,
    _safe_id,
    _sha256,
)


CATALOG_VERSION = "resource-profile-catalog-v1"
_BINDING_SCOPES = ("exact_host_observation", "reusable_host_domain")


class ResourceProfileCatalogError(OrchestrationContractError):
    """Raised when catalog identity, qualification, or equivalence fails."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ResourceProfileCatalogError(message)


def _finite_nonnegative(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResourceProfileCatalogError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ResourceProfileCatalogError(f"{label} must be finite and non-negative")
    return 0.0 if number == 0.0 else number


@dataclass(frozen=True)
class ProfileRuntimeMetadata:
    frozen_options: tuple[tuple[str, str | int | float | bool], ...]
    warmup: tuple[tuple[str, Any], ...] | None = None

    def __post_init__(self) -> None:
        names: set[str] = set()
        normalized: list[tuple[str, str | int | float | bool]] = []
        for name, value in tuple(self.frozen_options):
            if not isinstance(name, str) or not name or name != name.strip():
                raise ResourceProfileCatalogError("frozen option names must be non-empty/trimmed")
            if name in names:
                raise ResourceProfileCatalogError(f"duplicate frozen option {name!r}")
            names.add(name)
            normalized.append((name, _option_scalar(value, f"frozen option {name}")))
        object.__setattr__(self, "frozen_options", tuple(sorted(normalized)))

        if self.warmup is None:
            return
        warm = dict(self.warmup)
        if set(warm) != {"enabled", "nodes", "position", "reset_after"}:
            raise ResourceProfileCatalogError("warmup fields differ from frozen schema")
        if warm["enabled"] is not True or warm["reset_after"] is not True:
            raise ResourceProfileCatalogError("catalog warmup must be enabled and reset_after=true")
        _positive_int(warm["nodes"], "warmup nodes")
        if warm["position"] != "startpos":
            raise ResourceProfileCatalogError("catalog warmup currently supports startpos only")
        object.__setattr__(
            self,
            "warmup",
            tuple((key, warm[key]) for key in ("enabled", "nodes", "position", "reset_after")),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ProfileRuntimeMetadata":
        raw = _mapping(raw, "profile runtime metadata")
        _reject_unknown(raw, {"frozen_options", "warmup"}, "profile runtime metadata")
        frozen = _mapping(raw.get("frozen_options", {}), "profile frozen_options")
        warm = raw.get("warmup")
        if warm is not None:
            warm = _mapping(warm, "profile warmup")
        return cls(
            frozen_options=tuple(frozen.items()),
            warmup=None if warm is None else tuple(warm.items()),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "frozen_options": {key: value for key, value in self.frozen_options},
            "warmup": None if self.warmup is None else dict(self.warmup),
        }


class ResourceProfileCatalog:
    def __init__(
        self,
        *,
        catalog_id: str,
        source_profile: str,
        selection_enabled: bool,
        fallback_profile: str,
        profiles: tuple[EngineResourceProfile, ...],
        compositions: tuple[CompositionProfile, ...],
        default_composition_id: str,
        profile_runtime: Mapping[str, ProfileRuntimeMetadata],
        qualification_snapshot: Mapping[str, Any],
        legacy_policy: Mapping[str, Any],
    ) -> None:
        _safe_id(catalog_id, "catalog_id")
        _safe_id(source_profile, "source_profile")
        _safe_id(fallback_profile, "fallback_profile")
        if not isinstance(selection_enabled, bool):
            raise ResourceProfileCatalogError("selection_enabled must be boolean")

        profile_map = {item.profile_id: item for item in profiles}
        if len(profile_map) != len(profiles) or not profile_map:
            raise ResourceProfileCatalogError("catalog profile ids must be unique/non-empty")
        composition_map = {item.composition_id: item for item in compositions}
        if len(composition_map) != len(compositions) or not composition_map:
            raise ResourceProfileCatalogError("catalog composition ids must be unique/non-empty")
        if default_composition_id not in composition_map:
            raise ResourceProfileCatalogError("default composition is not present in catalog")
        for composition in compositions:
            composition.validate_against(profile_map)

        runtime_map = dict(profile_runtime)
        if set(runtime_map) != set(profile_map):
            raise ResourceProfileCatalogError(
                "profile_runtime keys must match catalog profile ids exactly"
            )
        for profile_id, metadata in runtime_map.items():
            if not isinstance(metadata, ProfileRuntimeMetadata):
                raise ResourceProfileCatalogError(
                    f"{profile_id}: runtime metadata has wrong type"
                )
            declared_names = {option.name for option in profile_map[profile_id].options}
            overlap = declared_names.intersection(dict(metadata.frozen_options))
            if overlap:
                raise ResourceProfileCatalogError(
                    f"{profile_id}: frozen options overlap managed profile options: {sorted(overlap)}"
                )

        snapshot = dict(qualification_snapshot)
        required_snapshot = {
            "qualified_head",
            "merge_commit",
            "workflow_run",
            "aggregate_artifact_id",
            "aggregate_artifact_sha256",
            "aggregate_report_sha256",
            "qualification_disposition",
            "execution_domain_id",
            "execution_domain_digest",
            "binding_scope",
        }
        if set(snapshot) != required_snapshot:
            raise ResourceProfileCatalogError("qualification_snapshot fields differ from frozen schema")
        _git_oid(snapshot["qualified_head"], "qualification qualified_head")
        _git_oid(snapshot["merge_commit"], "qualification merge_commit")
        _positive_int(snapshot["workflow_run"], "qualification workflow_run")
        _positive_int(snapshot["aggregate_artifact_id"], "qualification aggregate_artifact_id")
        _sha256(snapshot["aggregate_artifact_sha256"], "qualification aggregate artifact sha256")
        _sha256(snapshot["aggregate_report_sha256"], "qualification aggregate report sha256")
        if snapshot["qualification_disposition"] not in (
            "QUALIFIED_EXACT_HOST_ONLY",
            "QUALIFIED_REUSABLE_DOMAIN",
        ):
            raise ResourceProfileCatalogError("catalog seed is not a qualified disposition")
        _safe_id(snapshot["execution_domain_id"], "qualification execution_domain_id")
        _sha256(snapshot["execution_domain_digest"], "qualification execution_domain_digest")
        if snapshot["binding_scope"] not in _BINDING_SCOPES:
            raise ResourceProfileCatalogError("qualification binding scope is invalid")
        expected_domain = f"exec-domain/{snapshot['execution_domain_digest'][:20]}"
        if snapshot["execution_domain_id"] != expected_domain:
            raise ResourceProfileCatalogError("qualification execution-domain id/digest mismatch")

        policy = dict(legacy_policy)
        if set(policy) != {"dispatch_limits", "resource_estimates_ms"}:
            raise ResourceProfileCatalogError("legacy_policy fields differ from frozen schema")
        dispatch = _mapping(policy["dispatch_limits"], "legacy dispatch_limits")
        if set(dispatch) != {"EXPLORE", "VERIFY", "STAGED_VERIFY"}:
            raise ResourceProfileCatalogError("legacy dispatch phases differ from frozen v2")
        normalized_dispatch: dict[str, dict[str, int]] = {}
        for phase, raw_limit in dispatch.items():
            raw_limit = _mapping(raw_limit, f"legacy dispatch {phase}")
            if set(raw_limit) != {"nodes"}:
                raise ResourceProfileCatalogError(f"{phase}: only nodes is supported")
            nodes = raw_limit["nodes"]
            _positive_int(nodes, f"{phase} nodes")
            normalized_dispatch[phase] = {"nodes": int(nodes)}

        estimates = _mapping(policy["resource_estimates_ms"], "legacy resource estimates")
        if set(estimates) != {"explore", "verify"}:
            raise ResourceProfileCatalogError("legacy resource estimate phases differ from v2")
        normalized_estimates: dict[str, dict[str, float]] = {}
        for phase, raw_estimates in estimates.items():
            raw_estimates = _mapping(raw_estimates, f"{phase} resource estimates")
            if set(raw_estimates) != {"stockfish", "reckless", "lc0"}:
                raise ResourceProfileCatalogError(
                    f"{phase}: resource estimate families differ from v2"
                )
            normalized_estimates[phase] = {
                family: _finite_nonnegative(raw_estimates[family], f"{phase}.{family}")
                for family in ("stockfish", "reckless", "lc0")
            }

        self.catalog_id = catalog_id
        self.source_profile = source_profile
        self.selection_enabled = selection_enabled
        self.fallback_profile = fallback_profile
        self._profiles = dict(sorted(profile_map.items()))
        self._compositions = dict(sorted(composition_map.items()))
        self.default_composition_id = default_composition_id
        self._profile_runtime = dict(sorted(runtime_map.items()))
        self.qualification_snapshot = snapshot
        self.legacy_policy = {
            "dispatch_limits": normalized_dispatch,
            "resource_estimates_ms": normalized_estimates,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ResourceProfileCatalog":
        raw = _mapping(raw, "resource profile catalog")
        allowed = {
            "schema_version",
            "catalog_version",
            "catalog_id",
            "source_profile",
            "selection_enabled",
            "fallback_profile",
            "profiles",
            "compositions",
            "default_composition_id",
            "profile_runtime",
            "qualification_snapshot",
            "legacy_policy",
            "authority",
        }
        _reject_unknown(raw, allowed, "resource profile catalog")
        if raw.get("schema_version") != 1 or raw.get("catalog_version") != CATALOG_VERSION:
            raise ResourceProfileCatalogError("unsupported resource profile catalog version")
        if raw.get("authority") != {
            "resource_profile_catalog": True,
            "resource_authorization": False,
            "outward_move": False,
        }:
            raise ResourceProfileCatalogError("resource profile catalog authority marker is invalid")
        profiles_raw = raw.get("profiles")
        compositions_raw = raw.get("compositions")
        runtime_raw = raw.get("profile_runtime")
        if not isinstance(profiles_raw, list) or not isinstance(compositions_raw, list):
            raise ResourceProfileCatalogError("profiles/compositions must be arrays")
        runtime_raw = _mapping(runtime_raw, "profile_runtime")
        return cls(
            catalog_id=raw.get("catalog_id"),
            source_profile=raw.get("source_profile"),
            selection_enabled=raw.get("selection_enabled"),
            fallback_profile=raw.get("fallback_profile"),
            profiles=tuple(EngineResourceProfile.from_dict(item) for item in profiles_raw),
            compositions=tuple(CompositionProfile.from_dict(item) for item in compositions_raw),
            default_composition_id=raw.get("default_composition_id"),
            profile_runtime={
                key: ProfileRuntimeMetadata.from_dict(value)
                for key, value in runtime_raw.items()
            },
            qualification_snapshot=_mapping(
                raw.get("qualification_snapshot"), "qualification_snapshot"
            ),
            legacy_policy=_mapping(raw.get("legacy_policy"), "legacy_policy"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "catalog_version": CATALOG_VERSION,
            "catalog_id": self.catalog_id,
            "source_profile": self.source_profile,
            "selection_enabled": self.selection_enabled,
            "fallback_profile": self.fallback_profile,
            "profiles": [self._profiles[key].as_dict() for key in sorted(self._profiles)],
            "compositions": [
                self._compositions[key].as_dict() for key in sorted(self._compositions)
            ],
            "default_composition_id": self.default_composition_id,
            "profile_runtime": {
                key: self._profile_runtime[key].as_dict()
                for key in sorted(self._profile_runtime)
            },
            "qualification_snapshot": dict(self.qualification_snapshot),
            "legacy_policy": {
                "dispatch_limits": {
                    phase: dict(self.legacy_policy["dispatch_limits"][phase])
                    for phase in ("EXPLORE", "VERIFY", "STAGED_VERIFY")
                },
                "resource_estimates_ms": {
                    phase: dict(self.legacy_policy["resource_estimates_ms"][phase])
                    for phase in ("explore", "verify")
                },
            },
            "authority": {
                "resource_profile_catalog": True,
                "resource_authorization": False,
                "outward_move": False,
            },
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())

    def profile(self, profile_id: str) -> EngineResourceProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise ResourceProfileCatalogError(f"unknown resource profile: {profile_id!r}") from exc

    def composition(self, composition_id: str) -> CompositionProfile:
        try:
            return self._compositions[composition_id]
        except KeyError as exc:
            raise ResourceProfileCatalogError(
                f"unknown composition profile: {composition_id!r}"
            ) from exc

    @property
    def default_composition(self) -> CompositionProfile:
        return self.composition(self.default_composition_id)

    def runtime_metadata(self, profile_id: str) -> ProfileRuntimeMetadata:
        self.profile(profile_id)
        return self._profile_runtime[profile_id]

    def profile_for_instance(
        self, instance: str, *, composition_id: str | None = None
    ) -> EngineResourceProfile:
        composition = self.composition(composition_id or self.default_composition_id)
        matches = [item for item in composition.bindings if item.instance == instance]
        if len(matches) != 1:
            raise ResourceProfileCatalogError(
                f"composition does not bind instance exactly once: {instance!r}"
            )
        return self.profile(matches[0].profile_id)

    def require_execution_domain(
        self,
        profile_id: str,
        *,
        execution_domain_id: str,
        execution_domain_digest: str,
        binding_scope: str,
    ) -> None:
        profile = self.profile(profile_id)
        qualification = profile.qualification
        if (
            qualification.execution_domain_id != execution_domain_id
            or qualification.execution_domain_digest != execution_domain_digest
            or qualification.binding_scope != binding_scope
        ):
            raise ResourceProfileCatalogError(
                f"profile {profile_id!r} is not qualified for the supplied execution domain"
            )

    def phase_options(self, profile_id: str) -> dict[str, dict[str, Any]]:
        profile = self.profile(profile_id)
        result: dict[str, dict[str, Any]] = {}
        for option in profile.options:
            if option.boundary is MutationBoundary.SEARCH:
                assert option.phase is not None
                result.setdefault(option.phase, {})[option.name] = option.value
        return {phase: dict(sorted(values.items())) for phase, values in sorted(result.items())}

    def startup_options(self, profile_id: str) -> dict[str, Any]:
        profile = self.profile(profile_id)
        values = dict(self.runtime_metadata(profile_id).frozen_options)
        for option in profile.options:
            if option.boundary in (MutationBoundary.PROCESS, MutationBoundary.GAME):
                values[option.name] = option.value
        for name, value in self.phase_options(profile_id).get("EXPLORE", {}).items():
            values[name] = value
        return dict(sorted(values.items()))

    def game_options(self, profile_id: str) -> dict[str, Any]:
        profile = self.profile(profile_id)
        return {
            option.name: option.value
            for option in profile.options
            if option.boundary is MutationBoundary.GAME
        }

    def process_options(self, profile_id: str) -> dict[str, Any]:
        profile = self.profile(profile_id)
        return {
            option.name: option.value
            for option in profile.options
            if option.boundary is MutationBoundary.PROCESS
        }

    def warmup(self, profile_id: str) -> dict[str, Any] | None:
        warmup = self.runtime_metadata(profile_id).warmup
        return None if warmup is None else dict(warmup)

    def validate_v2_equivalence(
        self, runtime_config: Mapping[str, Any], selection: Mapping[str, Any]
    ) -> dict[str, Any]:
        if self.source_profile != "engine-opt-v2":
            raise ResourceProfileCatalogError("seed catalog source_profile drift")
        if self.selection_enabled:
            raise ResourceProfileCatalogError("J3 seed catalog must not enable adaptive selection")
        if self.fallback_profile != "engine-opt-v2":
            raise ResourceProfileCatalogError("J3 seed fallback profile drift")

        runtime = _mapping(runtime_config, "v2 runtime config")
        instances = _mapping(runtime.get("instances"), "v2 runtime instances")
        composition = self.default_composition
        bound_instances = {binding.instance for binding in composition.bindings}
        _require(
            bound_instances == set(instances),
            "catalog composition instances differ from current v2 runtime",
        )
        by_instance = {binding.instance: binding for binding in composition.bindings}
        for instance in sorted(instances):
            raw = _mapping(instances[instance], f"runtime instance {instance}")
            binding = by_instance[instance]
            profile = self.profile(binding.profile_id)
            _require(raw.get("family") == profile.family, f"{instance}: family drift")
            expected_role = "anchor" if binding.role.value == "anchor" else "shadow"
            _require(raw.get("role") == expected_role, f"{instance}: role drift")
            _require(
                _mapping(raw.get("options"), f"{instance}.options")
                == self.startup_options(profile.profile_id),
                f"{instance}: startup options differ from catalog",
            )
            _require(
                _mapping(raw.get("phase_options", {}), f"{instance}.phase_options")
                == self.phase_options(profile.profile_id),
                f"{instance}: phase options differ from catalog",
            )
            _require(
                list(raw.get("args", [])) == list(profile.process_identity.args),
                f"{instance}: argv differs from catalog",
            )
            _require(
                _mapping(raw.get("environment", {}), f"{instance}.environment")
                == dict(profile.process_identity.environment),
                f"{instance}: process environment differs from catalog",
            )
            warmup = raw.get("warmup")
            _require(
                warmup == self.warmup(profile.profile_id),
                f"{instance}: warmup differs from catalog",
            )
            backend = profile.process_identity.backend
            if backend is not None:
                _require(
                    self.startup_options(profile.profile_id).get("Backend") == backend,
                    f"{instance}: process backend identity drift",
                )

        chosen = _mapping(selection.get("selected"), "ENGINE-OPT selection")
        sf = _mapping(chosen.get("stockfish"), "selected stockfish")
        rr = _mapping(chosen.get("reckless"), "selected reckless")
        lc0 = _mapping(chosen.get("lc0"), "selected lc0")
        _require(
            self.startup_options("stockfish/anchor-engine-opt-v2").get("Hash")
            == sf.get("hash_mb"),
            "catalog Stockfish Hash differs from selection",
        )
        _require(
            self.startup_options("stockfish/specialist-engine-opt-v2").get("Hash")
            == sf.get("hash_mb"),
            "catalog specialist Stockfish Hash differs from selection",
        )
        _require(
            self.startup_options("reckless/specialist-engine-opt-v2").get("Hash")
            == rr.get("hash_mb"),
            "catalog Reckless Hash differs from selection",
        )
        lc0_opts = self.startup_options("lc0/specialist-engine-opt-v2")
        expected_lc0 = {
            "Backend": lc0.get("backend"),
            "NNCacheSize": lc0.get("nn_cache_size"),
            "MinibatchSize": lc0.get("minibatch_size"),
            "MaxPrefetch": lc0.get("max_prefetch"),
            "AdaptivePrefetch": lc0.get("adaptive_prefetch"),
            "DefectTelemetry": lc0.get("defect_telemetry"),
        }
        for name, value in expected_lc0.items():
            _require(lc0_opts.get(name) == value, f"catalog LC0 {name} differs from selection")
        _require(
            list(self.profile("lc0/specialist-engine-opt-v2").process_identity.args)
            == list(lc0.get("uci_args", [])),
            "catalog LC0 argv differs from selection",
        )
        warmup = self.warmup("lc0/specialist-engine-opt-v2")
        _require(
            isinstance(warmup, dict) and warmup.get("nodes") == lc0.get("warmup_nodes"),
            "catalog LC0 warmup differs from selection",
        )
        multipv = {
            phase: values.get("MultiPV")
            for phase, values in self.phase_options("lc0/specialist-engine-opt-v2").items()
        }
        _require(multipv == lc0.get("phase_multipv"), "catalog LC0 phase MultiPV drift")
        weights = lc0_opts.get("WeightsFile")
        _require(
            isinstance(weights, str)
            and weights.endswith(f"/{lc0.get('network')}.pb.gz"),
            "catalog LC0 network path differs from selection",
        )

        _require(
            self.legacy_policy["resource_estimates_ms"]
            == {
                phase: {
                    family: float(value)
                    for family, value in _mapping(raw, phase).items()
                }
                for phase, raw in _mapping(
                    chosen.get("resource_estimates_ms"), "selected resource estimates"
                ).items()
            },
            "catalog resource reservations differ from selection",
        )
        shadow = _mapping(runtime.get("shadow"), "runtime shadow")
        verify = _mapping(runtime.get("verification"), "runtime verification")
        extension = _mapping(verify.get("staged_extension"), "runtime staged extension")
        _require(
            self.legacy_policy["dispatch_limits"]["EXPLORE"]
            == _mapping(shadow.get("dispatch_limit"), "shadow dispatch"),
            "catalog EXPLORE dispatch limit drift",
        )
        _require(
            self.legacy_policy["dispatch_limits"]["VERIFY"]
            == _mapping(verify.get("dispatch_limit"), "verify dispatch"),
            "catalog VERIFY dispatch limit drift",
        )
        _require(
            self.legacy_policy["dispatch_limits"]["STAGED_VERIFY"]
            == _mapping(extension.get("dispatch_limit"), "staged dispatch"),
            "catalog STAGED_VERIFY dispatch limit drift",
        )
        return {
            "catalog_id": self.catalog_id,
            "catalog_digest": self.digest,
            "composition_id": composition.composition_id,
            "profiles": sorted(self._profiles),
            "equivalent_to": "config/allfather.online-hybrid-v2.validation.json",
        }


def load_resource_profile_catalog(path: Path | str) -> ResourceProfileCatalog:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResourceProfileCatalogError(f"{path}: cannot load catalog: {exc}") from exc
    return ResourceProfileCatalog.from_dict(raw)
