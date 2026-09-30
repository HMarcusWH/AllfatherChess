"""Frozen candidate-matrix construction for the J6 resource laboratory."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

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
        return {**self.material(), "candidate_id": self.candidate_id, "candidate_digest": self.digest}

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
            "placement_mode",
            "bundle_root",
            "execution_domain_required",
            "isolated_deadline_ms",
            "composition_deadline_ms",
            "families",
            "stage_b",
            "pareto",
            "claim_boundary",
        },
        "resource lab spec",
    )
    require(raw.get("schema_version") == 1, "unsupported resource lab schema")
    require(raw.get("lab_id") == "resource-lab-v1", "unexpected lab id")
    require(raw.get("attempt_policy") == "single-pass-no-retry-v1", "lab must forbid retry selection")
    require(raw.get("placement_mode") == "observed", "J6 hosted lab must remain observed-only")
    require(raw.get("execution_domain_required") is True, "execution domain must be required")
    _positive_int(raw.get("corpus_cases"), "corpus_cases")
    repeats = _positive_int(raw.get("repeats"), "repeats")
    require(repeats >= 2, "resource lab requires at least two repeats")
    _positive_int(raw.get("isolated_deadline_ms"), "isolated_deadline_ms")
    _positive_int(raw.get("composition_deadline_ms"), "composition_deadline_ms")

    families = _object(raw.get("families"), "families")
    require(set(families) == set(FAMILIES), "resource lab must define exactly three engine families")
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
        require(set(variable) == VARIABLE_ALLOWLIST[family], f"{family}: resource knob allowlist drift")
        fixed = _object(cfg.get("fixed_options"), f"{family}.fixed_options")
        require(not (set(fixed) & VARIABLE_ALLOWLIST[family]), f"{family}: fixed/variable options overlap")
        require(set(fixed).issubset(FIXED_CHESS_POLICY_OPTIONS), f"{family}: unsupported fixed option")

        variants = cfg.get("variants")
        require(isinstance(variants, list) and variants, f"{family}.variants must be non-empty")
        ids: set[str] = set()
        for row in variants:
            row = _object(row, f"{family} variant")
            _strict(row, {"id", "overrides"}, f"{family} variant")
            variant_id = row.get("id")
            require(isinstance(variant_id, str) and variant_id, f"{family}: variant id missing")
            require(variant_id not in ids, f"{family}: duplicate variant {variant_id}")
            ids.add(variant_id)
            overrides = _object(row.get("overrides"), f"{family}.{variant_id}.overrides")
            require(set(overrides) == VARIABLE_ALLOWLIST[family], f"{family}.{variant_id}: variable option set drift")
            for name, value in overrides.items():
                require(
                    not isinstance(value, bool) and isinstance(value, int) and value >= 0,
                    f"{family}.{variant_id}.{name}: resource option must be non-negative integer",
                )
        require(cfg.get("reference_variant") in ids, f"{family}: reference variant missing")
        budgets = cfg.get("work_budgets_nodes")
        require(isinstance(budgets, list) and budgets, f"{family}: work budgets missing")
        require(len(set(budgets)) == len(budgets), f"{family}: duplicate work budgets")
        for nodes in budgets:
            _positive_int(nodes, f"{family} nodes")
        total += len(variants) * len(budgets)

    require(total == 57, f"frozen J6 Stage-A matrix must contain 57 candidates, got {total}")
    stage_b = _object(raw.get("stage_b"), "stage_b")
    _strict(stage_b, {"compositions"}, "stage_b")
    compositions = stage_b.get("compositions")
    require(isinstance(compositions, list) and len(compositions) == 3, "Stage B must freeze three compositions")
    claim = _object(raw.get("claim_boundary"), "claim_boundary")
    require(
        claim == {
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
        isinstance(sha, str) and len(sha) == 64 and all(ch in "0123456789abcdef" for ch in sha),
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
        network_sha = None if network_rel is None else _artifact_sha(bundle_manifest, family, "networks")
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
                        network_relpath=None if network_rel is None else str(network_rel),
                        binary_sha256=binary_sha,
                        network_sha256=network_sha,
                        args=tuple(cfg["args"]),
                        environment=tuple(sorted(dict(cfg["environment"]).items())),
                        options=tuple(sorted(options.items())),
                        warmup_nodes=cfg["warmup_nodes"],
                        reference=variant_id == reference_variant,
                    )
                )
    require(len(rows) == 57, "expanded Stage-A matrix is not frozen 57-candidate set")
    ids = [row.candidate_id for row in rows]
    require(len(ids) == len(set(ids)), "candidate ids are not unique")
    return tuple(rows)


def candidate_lookup(candidates: tuple[Candidate, ...]) -> dict[tuple[str, str, int], Candidate]:
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
        require(isinstance(composition_id, str) and composition_id, "composition id missing")
        members: list[CompositionMember] = []
        instances: set[str] = set()
        for member in raw.get("members", []):
            member = _object(member, f"{composition_id} member")
            _strict(member, {"instance", "role", "family", "variant", "nodes"}, f"{composition_id} member")
            instance = member.get("instance")
            role = member.get("role")
            family = member.get("family")
            variant = member.get("variant")
            nodes = member.get("nodes")
            require(isinstance(instance, str) and instance and instance not in instances, "duplicate/missing composition instance")
            instances.add(instance)
            require(role in ("anchor", "specialist"), f"{composition_id}: invalid role")
            require(family in FAMILIES, f"{composition_id}: invalid family")
            _positive_int(nodes, f"{composition_id}: member nodes")
            candidate = lookup.get((family, variant, nodes))
            require(candidate is not None, f"{composition_id}: member references absent Stage-A candidate")
            members.append(
                CompositionMember(
                    instance=instance,
                    role=role,
                    candidate_id=candidate.candidate_id,
                    candidate_digest=candidate.digest,
                )
            )
        require(sum(member.role == "anchor" for member in members) == 1, f"{composition_id}: exactly one anchor required")
        rows.append(CompositionCandidate(composition_id=composition_id, members=tuple(members)))
    require(len(rows) == 3, "Stage B composition count drift")
    return tuple(rows)
