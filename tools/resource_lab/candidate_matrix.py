"""Frozen candidate-matrix construction for the J6 resource laboratory."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from controller.decision import canonical_digest


FAMILIES = ("stockfish", "reckless", "lc0")
VARIABLE_ALLOWLIST = {
    "stockfish": {"Threads", "Hash"},
    "reckless": {"Threads", "Hash"},
    "lc0": {"Threads", "NNCacheSize", "MinibatchSize", "MaxPrefetch", "TaskWorkers"},
}
FIXED_CHESS_POLICY_OPTIONS = {
    "MultiPV",
    "ScoreType",
    "Backend",
    "BackendOptions",
    "WeightsFile",
    "UCI_Chess960",
    "Minimal",
    "MaxConcurrentSearchers",
    "AdaptivePrefetch",
    "DefectTelemetry",
}
REFERENCE_INSTANCE = {
    "stockfish": "stockfish-anchor",
    "reckless": "reckless-shadow",
    "lc0": "lc0-shadow",
}
REFERENCE_PROFILE = {
    "stockfish": "stockfish/anchor-engine-opt-v2",
    "reckless": "reckless/specialist-engine-opt-v2",
    "lc0": "lc0/specialist-engine-opt-v2",
}
REFERENCE_CONTRACT_PATHS_BY_LAB = {
    "resource-lab-v1": {
        "catalog": "qualification/resource-profile-catalog-v1.json",
        "runtime": "config/allfather.online-hybrid-v2.validation.json",
        "build_policy": "qualification/online-engine-opt-v2.json",
    },
    "resource-lab-v2": {
        "selection": "qualification/engine-opt-v2-candidate-selection.json",
        "runtime": "config/allfather.online-engine-opt-v2.candidate.json",
        "build_policy": "qualification/online-engine-opt-v2.json",
    },
}


class ResourceLabSpecError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ResourceLabSpecError(message)


def _object(value: Any, label: str) -> Mapping[str, Any]:
    require(isinstance(value, Mapping), f"{label} must be an object")
    return value


def _strict(raw: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(raw) - allowed)
    require(not unknown, f"{label} contains unsupported fields: {unknown}")


def _positive_int(value: Any, label: str) -> int:
    require(
        not isinstance(value, bool) and isinstance(value, int) and value > 0,
        f"{label} must be a positive integer",
    )
    return value


@dataclass(frozen=True)
class Candidate:
    family: str
    variant: str
    nodes: int
    binary_relpath: str
    network_relpath: str | None
    binary_sha256: str
    network_sha256: str | None
    args: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    options: tuple[tuple[str, Any], ...]
    warmup_nodes: int | None
    reference: bool

    def material(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "family": self.family,
            "variant": self.variant,
            "nodes": self.nodes,
            "binary_relpath": self.binary_relpath,
            "network_relpath": self.network_relpath,
            "binary_sha256": self.binary_sha256,
            "network_sha256": self.network_sha256,
            "args": list(self.args),
            "environment": dict(self.environment),
            "options": dict(self.options),
            "warmup_nodes": self.warmup_nodes,
            "reference": self.reference,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.material())

    @property
    def candidate_id(self) -> str:
        return (
            f"resource-candidate/{self.family}/{self.variant}/n{self.nodes}/"
            f"{self.digest[:16]}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.material(),
            "candidate_id": self.candidate_id,
            "candidate_digest": self.digest,
        }

    def execution_options(self, bundle_root: Path) -> dict[str, Any]:
        options = dict(self.options)
        if self.family == "lc0":
            network = self.network_relpath
            require(network is not None, "LC0 candidate lacks network")
            for name, value in tuple(options.items()):
                if value == "__BUNDLE_NETWORK__":
                    options[name] = str((bundle_root / network).resolve())
        return options


@dataclass(frozen=True)
class CompositionMember:
    instance: str
    role: str
    candidate_id: str
    candidate_digest: str

    def as_dict(self) -> dict[str, str]:
        return {
            "instance": self.instance,
            "role": self.role,
            "candidate_id": self.candidate_id,
            "candidate_digest": self.candidate_digest,
        }


@dataclass(frozen=True)
class CompositionCandidate:
    composition_id: str
    members: tuple[CompositionMember, ...]

    def material(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "composition_id": self.composition_id,
            "members": [member.as_dict() for member in self.members],
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.material())

    def as_dict(self) -> dict[str, Any]:
        return {**self.material(), "composition_digest": self.digest}


@dataclass(frozen=True)
class LabSpec:
    raw: Mapping[str, Any]
    path: Path

    @property
    def lab_id(self) -> str:
        return str(self.raw["lab_id"])

    @property
    def repeats(self) -> int:
        return int(self.raw["repeats"])

    @property
    def corpus(self) -> str:
        return str(self.raw["corpus"])

    @property
    def corpus_cases(self) -> int:
        return int(self.raw["corpus_cases"])

    @property
    def digest(self) -> str:
        return canonical_digest(dict(self.raw))

    @property
    def bundle_root(self) -> str:
        return str(self.raw["bundle_root"])


def validate_lab_spec(raw: Mapping[str, Any]) -> None:
    _strict(
        raw,
        {
            "schema_version",
            "lab_id",
            "corpus",
            "corpus_cases",
            "repeats",
            "attempt_policy",
            "attempt_order_policy",
            "placement_mode",
            "affinity_observation",
            "cpu_measurement",
            "reference_contract",
            "bundle_root",
            "execution_domain_required",
            "isolated_deadline_ms",
            "composition_deadline_ms",
            "families",
            "stage_b",
            "pareto",
            "claim_boundary",
            "expected_counts",
        },
        "resource lab spec",
    )
    require(raw.get("schema_version") == 1, "unsupported resource lab schema")
    lab_id = raw.get("lab_id")
    require(
        lab_id in REFERENCE_CONTRACT_PATHS_BY_LAB,
        f"unsupported resource lab id: {lab_id!r}",
    )
    require(
        raw.get("attempt_policy") == "single-pass-no-retry-v1",
        "lab must forbid retry selection",
    )
    require(
        raw.get("attempt_order_policy") == "blocked-cyclic-v1",
        "J6 attempt order policy drift",
    )
    require(
        raw.get("placement_mode") == "observed",
        "J6 hosted lab must remain observed-only",
    )
    require(raw.get("execution_domain_required") is True, "execution domain must be required")
    _positive_int(raw.get("corpus_cases"), "corpus_cases")
    repeats = _positive_int(raw.get("repeats"), "repeats")
    require(repeats >= 2, "resource lab requires at least two repeats")
    _positive_int(raw.get("isolated_deadline_ms"), "isolated_deadline_ms")
    _positive_int(raw.get("composition_deadline_ms"), "composition_deadline_ms")

    affinity = _object(raw.get("affinity_observation"), "affinity_observation")
    _strict(affinity, {"policy", "max_attempts"}, "affinity_observation")
    require(
        affinity.get("policy") == "bounded-observed-affinity-v1",
        "J6 affinity observation policy drift",
    )
    _positive_int(affinity.get("max_attempts"), "affinity_observation.max_attempts")

    cpu = _object(raw.get("cpu_measurement"), "cpu_measurement")
    _strict(cpu, {"required_method", "max_resolution_ns"}, "cpu_measurement")
    require(
        cpu.get("required_method") == "posix-process-cpu-clock-v1",
        "J6 CPU measurement method drift",
    )
    _positive_int(cpu.get("max_resolution_ns"), "cpu_measurement.max_resolution_ns")

    reference_contract = _object(raw.get("reference_contract"), "reference_contract")
    require(
        dict(reference_contract) == REFERENCE_CONTRACT_PATHS_BY_LAB[lab_id],
        f"{lab_id} reference contract paths drift",
    )

    families = _object(raw.get("families"), "families")
    require(
        set(families) == set(FAMILIES),
        "resource lab must define exactly three engine families",
    )
    total = 0
    for family in FAMILIES:
        cfg = _object(families[family], family)
        _strict(
            cfg,
            {
                "binary",
                "network",
                "args",
                "environment",
                "fixed_options",
                "variable_options",
                "reference_variant",
                "work_budgets_nodes",
                "variants",
                "warmup_nodes",
            },
            f"{family} spec",
        )
        variable = cfg.get("variable_options")
        require(isinstance(variable, list), f"{family}.variable_options must be an array")
        require(
            set(variable) == VARIABLE_ALLOWLIST[family],
            f"{family}: resource knob allowlist drift",
        )
        fixed = _object(cfg.get("fixed_options"), f"{family}.fixed_options")
        require(
            not (set(fixed) & VARIABLE_ALLOWLIST[family]),
            f"{family}: fixed/variable options overlap",
        )
        require(
            set(fixed).issubset(FIXED_CHESS_POLICY_OPTIONS),
            f"{family}: unsupported fixed option",
        )

        variants = cfg.get("variants")
        require(
            isinstance(variants, list) and variants,
            f"{family}.variants must be non-empty",
        )
        ids: set[str] = set()
        for row in variants:
            row = _object(row, f"{family} variant")
            _strict(row, {"id", "overrides"}, f"{family} variant")
            variant_id = row.get("id")
            require(
                isinstance(variant_id, str) and variant_id,
                f"{family}: variant id missing",
            )
            require(variant_id not in ids, f"{family}: duplicate variant {variant_id}")
            ids.add(variant_id)
            overrides = _object(
                row.get("overrides"),
                f"{family}.{variant_id}.overrides",
            )
            require(
                set(overrides) == VARIABLE_ALLOWLIST[family],
                f"{family}.{variant_id}: variable option set drift",
            )
            for name, value in overrides.items():
                require(
                    not isinstance(value, bool)
                    and isinstance(value, int)
                    and value >= 0,
                    f"{family}.{variant_id}.{name}: resource option must be non-negative integer",
                )
        require(
            cfg.get("reference_variant") in ids,
            f"{family}: reference variant missing",
        )
        budgets = cfg.get("work_budgets_nodes")
        require(
            isinstance(budgets, list) and budgets,
            f"{family}: work budgets missing",
        )
        require(
            len(set(budgets)) == len(budgets),
            f"{family}: duplicate work budgets",
        )
        for nodes in budgets:
            _positive_int(nodes, f"{family} nodes")
        total += len(variants) * len(budgets)

    if lab_id == "resource-lab-v1":
        require(
            total == 57,
            f"frozen J6 Stage-A matrix must contain 57 candidates, got {total}",
        )
    expected_counts = raw.get("expected_counts")
    if expected_counts is not None:
        expected_counts = _object(expected_counts, "expected_counts")
        _strict(
            expected_counts,
            {
                "stage_a_candidates",
                "stage_a_measurements",
                "stage_b_compositions",
                "stage_b_batches",
                "preflight_reference_attempts",
            },
            "expected_counts",
        )
        require(
            _positive_int(
                expected_counts.get("stage_a_candidates"),
                "expected_counts.stage_a_candidates",
            )
            == total,
            "Stage-A candidate count differs from frozen expected_counts",
        )
        require(
            _positive_int(
                expected_counts.get("stage_a_measurements"),
                "expected_counts.stage_a_measurements",
            )
            == total * int(raw["corpus_cases"]) * int(raw["repeats"]),
            "Stage-A measurement count differs from frozen expected_counts",
        )
    stage_b = _object(raw.get("stage_b"), "stage_b")
    _strict(stage_b, {"compositions"}, "stage_b")
    compositions = stage_b.get("compositions")
    require(
        isinstance(compositions, list) and compositions,
        "Stage B must freeze at least one composition",
    )
    if lab_id == "resource-lab-v1":
        require(len(compositions) == 3, "Stage B must freeze three compositions")
    if expected_counts is not None:
        require(
            _positive_int(
                expected_counts.get("stage_b_compositions"),
                "expected_counts.stage_b_compositions",
            )
            == len(compositions),
            "Stage-B composition count differs from frozen expected_counts",
        )
        require(
            _positive_int(
                expected_counts.get("stage_b_batches"),
                "expected_counts.stage_b_batches",
            )
            == len(compositions) * int(raw["corpus_cases"]) * int(raw["repeats"]),
            "Stage-B batch count differs from frozen expected_counts",
        )
        require(
            _positive_int(
                expected_counts.get("preflight_reference_attempts"),
                "expected_counts.preflight_reference_attempts",
            )
            == sum(len(cfg["work_budgets_nodes"]) for cfg in families.values()),
            "preflight reference-attempt count differs from frozen expected_counts",
        )
    pareto = _object(raw.get("pareto"), "pareto")
    _strict(
        pareto,
        {
            "dimensions",
            "require_bestmove_reference_match",
            "require_repeatable_bestmove",
            "require_repeatable_native_work",
            "require_usable_cpu",
            "require_complete_process_cpu_scope",
        },
        "pareto",
    )
    require(
        pareto.get("dimensions")
        == [
            "median_wall_ms",
            "p95_wall_ms",
            "median_cpu_ms",
            "p95_cpu_ms",
            "p95_vm_hwm_bytes",
        ],
        "J6 Pareto dimensions drift",
    )
    for name in (
        "require_bestmove_reference_match",
        "require_repeatable_bestmove",
        "require_repeatable_native_work",
        "require_usable_cpu",
        "require_complete_process_cpu_scope",
    ):
        require(pareto.get(name) is True, f"J6 Pareto policy {name} must be true")

    claim = _object(raw.get("claim_boundary"), "claim_boundary")
    require(
        claim
        == {
            "resource_measurement": True,
            "profile_selection": False,
            "strength": False,
            "elo": False,
            "equal_compute": False,
            "deployment": False,
        },
        "resource lab claim boundary drift",
    )


def load_lab_spec(path: Path | str) -> LabSpec:
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(raw, Mapping), "resource lab spec root must be object")
    validate_lab_spec(raw)
    return LabSpec(raw=raw, path=path)


def _artifact_sha(bundle_manifest: Mapping[str, Any], family: str, category: str) -> str:
    artifacts = _object(bundle_manifest.get("artifacts"), "bundle artifacts")
    group = _object(artifacts.get(category), f"bundle {category}")
    row = _object(group.get(family), f"bundle {category}.{family}")
    sha = row.get("sha256")
    require(
        isinstance(sha, str)
        and len(sha) == 64
        and all(ch in "0123456789abcdef" for ch in sha),
        f"bundle {category}.{family} sha256 missing",
    )
    return sha


def expand_candidates(
    spec: LabSpec,
    bundle_manifest: Mapping[str, Any],
) -> tuple[Candidate, ...]:
    families = _object(spec.raw["families"], "families")
    rows: list[Candidate] = []
    for family in FAMILIES:
        cfg = _object(families[family], family)
        binary_sha = _artifact_sha(bundle_manifest, family, "engines")
        network_rel = cfg.get("network")
        network_sha = (
            None
            if network_rel is None
            else _artifact_sha(bundle_manifest, family, "networks")
        )
        fixed = dict(_object(cfg["fixed_options"], f"{family}.fixed_options"))
        reference_variant = str(cfg["reference_variant"])
        for variant in cfg["variants"]:
            variant_id = str(variant["id"])
            options = {**fixed, **dict(variant["overrides"])}
            for nodes in cfg["work_budgets_nodes"]:
                rows.append(
                    Candidate(
                        family=family,
                        variant=variant_id,
                        nodes=int(nodes),
                        binary_relpath=str(cfg["binary"]),
                        network_relpath=None
                        if network_rel is None
                        else str(network_rel),
                        binary_sha256=binary_sha,
                        network_sha256=network_sha,
                        args=tuple(cfg["args"]),
                        environment=tuple(
                            sorted(dict(cfg["environment"]).items())
                        ),
                        options=tuple(sorted(options.items())),
                        warmup_nodes=cfg["warmup_nodes"],
                        reference=variant_id == reference_variant,
                    )
                )
    ids = [row.candidate_id for row in rows]
    require(len(ids) == len(set(ids)), "candidate ids are not unique")
    return tuple(rows)


def candidate_lookup(
    candidates: tuple[Candidate, ...],
) -> dict[tuple[str, str, int], Candidate]:
    result: dict[tuple[str, str, int], Candidate] = {}
    for candidate in candidates:
        key = (candidate.family, candidate.variant, candidate.nodes)
        require(key not in result, f"duplicate candidate key: {key}")
        result[key] = candidate
    return result


def expand_compositions(
    spec: LabSpec,
    candidates: tuple[Candidate, ...],
) -> tuple[CompositionCandidate, ...]:
    lookup = candidate_lookup(candidates)
    rows: list[CompositionCandidate] = []
    for raw in spec.raw["stage_b"]["compositions"]:
        raw = _object(raw, "Stage B composition")
        _strict(raw, {"id", "members"}, "Stage B composition")
        composition_id = raw.get("id")
        require(
            isinstance(composition_id, str) and composition_id,
            "composition id missing",
        )
        members: list[CompositionMember] = []
        instances: set[str] = set()
        for member in raw.get("members", []):
            member = _object(member, f"{composition_id} member")
            _strict(
                member,
                {"instance", "role", "family", "variant", "nodes"},
                f"{composition_id} member",
            )
            instance = member.get("instance")
            role = member.get("role")
            family = member.get("family")
            variant = member.get("variant")
            nodes = member.get("nodes")
            require(
                isinstance(instance, str)
                and instance
                and instance not in instances,
                "duplicate/missing composition instance",
            )
            instances.add(instance)
            require(
                role in ("anchor", "specialist"),
                f"{composition_id}: invalid role",
            )
            require(family in FAMILIES, f"{composition_id}: invalid family")
            _positive_int(nodes, f"{composition_id}: member nodes")
            candidate = lookup.get((family, variant, nodes))
            require(
                candidate is not None,
                f"{composition_id}: member references absent Stage-A candidate",
            )
            members.append(
                CompositionMember(
                    instance=instance,
                    role=role,
                    candidate_id=candidate.candidate_id,
                    candidate_digest=candidate.digest,
                )
            )
        require(
            sum(member.role == "anchor" for member in members) == 1,
            f"{composition_id}: exactly one anchor required",
        )
        rows.append(
            CompositionCandidate(
                composition_id=composition_id,
                members=tuple(members),
            )
        )
    return tuple(rows)


def _reference_variant(spec: LabSpec, family: str) -> Mapping[str, Any]:
    cfg = _object(spec.raw["families"][family], family)
    reference_id = cfg["reference_variant"]
    for row in cfg["variants"]:
        if row["id"] == reference_id:
            return row
    raise ResourceLabSpecError(f"{family}: reference variant missing")


def _normalized_reference_options(
    spec: LabSpec,
    family: str,
) -> dict[str, Any]:
    cfg = _object(spec.raw["families"][family], family)
    options = {
        **dict(_object(cfg["fixed_options"], f"{family}.fixed_options")),
        **dict(_reference_variant(spec, family)["overrides"]),
    }
    if family == "lc0" and options.get("WeightsFile") == "__BUNDLE_NETWORK__":
        options["WeightsFile"] = f"{spec.bundle_root}/{cfg['network']}"
    return options


def validate_reference_contract(spec: LabSpec, root: Path) -> None:
    """Prove the hand-written laboratory reference matches its declared runtime."""
    ref = _object(spec.raw["reference_contract"], "reference_contract")
    runtime = json.loads((root / str(ref["runtime"])).read_text(encoding="utf-8"))
    build_policy = json.loads(
        (root / str(ref["build_policy"])).read_text(encoding="utf-8")
    )
    instances = _object(runtime.get("instances"), "reference runtime instances")
    builds = _object(build_policy.get("builds"), "reference build policy")

    catalog = None
    if spec.lab_id == "resource-lab-v1":
        from controller.resource_profile_catalog import load_resource_profile_catalog

        catalog = load_resource_profile_catalog(root / str(ref["catalog"]))
    elif spec.lab_id == "resource-lab-v2":
        from controller.engine_opt_profile import validate_reference

        selection = json.loads(
            (root / str(ref["selection"])).read_text(encoding="utf-8")
        )
        validate_reference(
            build_policy,
            selection,
            runtime,
            require_selected=False,
        )
    else:  # guarded by validate_lab_spec; defensive for hand-constructed LabSpec tests
        raise ResourceLabSpecError(f"unsupported reference contract for {spec.lab_id}")

    for family in FAMILIES:
        cfg = _object(spec.raw["families"][family], family)
        instance = REFERENCE_INSTANCE[family]
        runtime_instance = _object(instances.get(instance), f"runtime {instance}")
        expected_options = _normalized_reference_options(spec, family)
        require(
            runtime_instance.get("family") == family,
            f"{family}: reference runtime family drift",
        )
        require(
            dict(runtime_instance.get("options") or {}) == expected_options,
            f"{family}: J6 v2-current options differ from frozen runtime",
        )
        require(
            list(runtime_instance.get("args") or []) == list(cfg["args"]),
            f"{family}: J6 argv differs from frozen runtime",
        )
        require(
            dict(runtime_instance.get("environment") or {})
            == dict(cfg["environment"]),
            f"{family}: J6 environment differs from frozen runtime",
        )
        runtime_warmup = runtime_instance.get("warmup")
        expected_warmup = (
            None
            if cfg["warmup_nodes"] is None
            else {
                "enabled": True,
                "nodes": cfg["warmup_nodes"],
                "position": "startpos",
                "reset_after": True,
            }
        )
        require(
            runtime_warmup == expected_warmup,
            f"{family}: J6 warmup differs from frozen runtime",
        )

        if catalog is not None:
            profile_id = REFERENCE_PROFILE[family]
            require(
                catalog.startup_options(profile_id) == expected_options,
                f"{family}: J6 v2-current differs from frozen catalog",
            )
            require(
                catalog.warmup(profile_id) == expected_warmup,
                f"{family}: J6 warmup differs from frozen catalog",
            )
            profile = catalog.profile(profile_id)
            require(
                list(profile.process_identity.args) == list(cfg["args"]),
                f"{family}: catalog argv differs from J6 reference",
            )
            require(
                dict(profile.process_identity.environment) == dict(cfg["environment"]),
                f"{family}: catalog environment differs from J6 reference",
            )

        build = _object(builds.get(family), f"build policy {family}")
        require(
            build.get("artifact") == cfg["binary"],
            f"{family}: J6 binary path differs from build policy",
        )
        require(
            build.get("network_artifact") == cfg["network"],
            f"{family}: J6 network path differs from build policy",
        )


def blocked_cyclic(items: Sequence[Any], block_index: int) -> tuple[Any, ...]:
    require(bool(items), "blocked-cyclic ordering requires non-empty item set")
    offset = block_index % len(items)
    return tuple(items[offset:]) + tuple(items[:offset])


def stage_a_attempt_plan(
    candidates: tuple[Candidate, ...],
    case_ids: tuple[str, ...],
    repeats: int,
) -> tuple[dict[str, Any], ...]:
    plan: list[dict[str, Any]] = []
    ordinal = 0
    for repeat_index in range(repeats):
        for case_index, case_id in enumerate(case_ids):
            block_index = repeat_index * len(case_ids) + case_index
            for order_index, candidate in enumerate(
                blocked_cyclic(candidates, block_index)
            ):
                plan.append(
                    {
                        "candidate_id": candidate.candidate_id,
                        "repeat_index": repeat_index,
                        "case_id": case_id,
                        "block_index": block_index,
                        "order_index": order_index,
                        "attempt_ordinal": ordinal,
                    }
                )
                ordinal += 1
    return tuple(plan)


def stage_b_attempt_plan(
    compositions: tuple[CompositionCandidate, ...],
    case_ids: tuple[str, ...],
    repeats: int,
) -> tuple[dict[str, Any], ...]:
    plan: list[dict[str, Any]] = []
    ordinal = 0
    for repeat_index in range(repeats):
        for case_index, case_id in enumerate(case_ids):
            block_index = repeat_index * len(case_ids) + case_index
            for order_index, composition in enumerate(
                blocked_cyclic(compositions, block_index)
            ):
                plan.append(
                    {
                        "composition_id": composition.composition_id,
                        "repeat_index": repeat_index,
                        "case_id": case_id,
                        "block_index": block_index,
                        "order_index": order_index,
                        "attempt_ordinal": ordinal,
                    }
                )
                ordinal += 1
    return tuple(plan)
