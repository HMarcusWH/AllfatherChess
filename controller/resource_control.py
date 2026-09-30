"""Fail-closed resource placement for M14-J J5.

J5 turns catalogued slot requirements into host-specific placement evidence.
It still grants no WorkGrant and no outward move authority.

Important boundaries:
- HostCapabilities remain read-only J2 evidence.
- The frozen J3 seed composition remains observed and causes no affinity writes.
- Affinity placement is opt-in and requires complete host capacity evidence.
- cgroup-v2 enforcement is represented but not implemented in J5.
- concurrency-group CPU reuse is refused until J9 can enforce mutual exclusion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from adapters.resource.linux_affinity import (
    LinuxAffinityError,
    LinuxAffinityProvider,
    ProcessTreeAffinity,
)
from controller.decision import canonical_digest
from controller.host_capabilities import HostCapabilities
from controller.resource_profiles import (
    CompositionProfile,
    CompositionRole,
    EnforcementMode,
    EngineResourceProfile,
)


READY = "READY"
FALLBACK_INCOMPLETE_HOST = "FALLBACK_INCOMPLETE_HOST"
FALLBACK_INSUFFICIENT_CPU = "FALLBACK_INSUFFICIENT_CPU"
FALLBACK_CPU_QUOTA = "FALLBACK_CPU_QUOTA"
FALLBACK_INSUFFICIENT_MEMORY = "FALLBACK_INSUFFICIENT_MEMORY"
FALLBACK_UNSUPPORTED_ENFORCEMENT = "FALLBACK_UNSUPPORTED_ENFORCEMENT"
FALLBACK_SCHEDULED_REUSE_UNSUPPORTED = "FALLBACK_SCHEDULED_REUSE_UNSUPPORTED"
FALLBACK_HOST_CPUSET_CHANGED = "FALLBACK_HOST_CPUSET_CHANGED"


class ResourceControlError(RuntimeError):
    """Placement or placement evidence is invalid."""


class ResourceFallbackRequired(ResourceControlError):
    """A valid host/composition cannot satisfy the requested placement contract."""

    def __init__(self, assessment: "ResourcePlacementAssessment") -> None:
        self.assessment = assessment
        super().__init__(
            f"{assessment.disposition}: "
            + ("; ".join(assessment.reasons) if assessment.reasons else "fallback required")
        )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ResourceControlError(message)


@dataclass(frozen=True)
class ResourcePlacementAssessment:
    disposition: str
    ready: bool
    fallback_required: bool
    enforcement_mode: str
    required_cpu_slots: int
    available_cpu_slots: int | None
    expected_memory_mib: int
    effective_memory_limit_bytes: int | None
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "ready": self.ready,
            "fallback_required": self.fallback_required,
            "enforcement_mode": self.enforcement_mode,
            "required_cpu_slots": self.required_cpu_slots,
            "available_cpu_slots": self.available_cpu_slots,
            "expected_memory_mib": self.expected_memory_mib,
            "effective_memory_limit_bytes": self.effective_memory_limit_bytes,
            "reasons": list(self.reasons),
            "authority": {
                "resource_eligibility": True,
                "resource_authorization": False,
                "outward_move": False,
            },
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())


@dataclass(frozen=True)
class ResourceLayoutBinding:
    instance: str
    profile_id: str
    cpu_set: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance": self.instance,
            "profile_id": self.profile_id,
            "cpu_set": list(self.cpu_set),
        }


@dataclass(frozen=True)
class ResourceLayoutPlan:
    policy_id: str
    host_capability_id: str
    host_capability_digest: str
    composition_id: str
    composition_digest: str
    allowed_cpus: tuple[int, ...]
    bindings: tuple[ResourceLayoutBinding, ...]

    def __post_init__(self) -> None:
        if self.policy_id != "anchor-first-contiguous-v1":
            raise ResourceControlError("unsupported J5 layout policy")
        if not self.allowed_cpus:
            raise ResourceControlError("layout requires non-empty allowed CPU set")
        if tuple(sorted(set(self.allowed_cpus))) != self.allowed_cpus:
            raise ResourceControlError("layout allowed CPUs must be sorted/unique")
        instances = [row.instance for row in self.bindings]
        if len(instances) != len(set(instances)):
            raise ResourceControlError("layout instances must be unique")
        used: set[int] = set()
        allowed = set(self.allowed_cpus)
        for row in self.bindings:
            if not row.cpu_set:
                raise ResourceControlError(f"{row.instance}: layout CPU set may not be empty")
            if not set(row.cpu_set).issubset(allowed):
                raise ResourceControlError(
                    f"{row.instance}: layout CPU set leaves host allowed CPUs"
                )
            overlap = used.intersection(row.cpu_set)
            if overlap:
                raise ResourceControlError(
                    f"resident J5 bindings may not overlap CPUs: {sorted(overlap)}"
                )
            used.update(row.cpu_set)

    def as_dict(self) -> dict[str, Any]:
        return {
            "layout_version": "resource-layout-v1",
            "policy_id": self.policy_id,
            "host_capability_id": self.host_capability_id,
            "host_capability_digest": self.host_capability_digest,
            "composition_id": self.composition_id,
            "composition_digest": self.composition_digest,
            "allowed_cpus": list(self.allowed_cpus),
            "bindings": [row.as_dict() for row in self.bindings],
            "authority": {
                "resource_layout": True,
                "resource_authorization": False,
                "outward_move": False,
            },
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())

    @property
    def layout_id(self) -> str:
        return f"resource-layout/{self.digest[:20]}"

    def binding(self, instance: str) -> ResourceLayoutBinding:
        for row in self.bindings:
            if row.instance == instance:
                return row
        raise ResourceControlError(f"layout does not contain instance {instance!r}")


@dataclass(frozen=True)
class ResourcePlacementEvidence:
    provider_id: str
    instance: str
    enforcement_mode: str
    host_capability_id: str
    host_capability_digest: str
    composition_id: str
    composition_digest: str
    layout_id: str | None
    layout_digest: str | None
    requested_cpu_set: tuple[int, ...] | None
    observation: ProcessTreeAffinity
    success: bool
    faults: tuple[str, ...]
    enforced: bool
    isolated: bool

    def __post_init__(self) -> None:
        if self.success and self.faults:
            raise ResourceControlError("successful placement evidence may not carry faults")
        if not self.success and not self.faults:
            raise ResourceControlError("failed placement evidence must carry faults")
        if self.enforcement_mode == EnforcementMode.AFFINITY.value:
            if self.requested_cpu_set is None:
                raise ResourceControlError("affinity evidence requires requested_cpu_set")
            if self.layout_id is None or self.layout_digest is None:
                raise ResourceControlError("affinity evidence requires layout identity")
        if self.enforcement_mode == EnforcementMode.OBSERVED.value and self.enforced:
            raise ResourceControlError("observed mode may not claim enforcement")
        if self.isolated:
            raise ResourceControlError(
                "J5 affinity cannot claim CPU isolation from unrelated host processes"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "provider_id": self.provider_id,
            "instance": self.instance,
            "enforcement_mode": self.enforcement_mode,
            "host_capability_id": self.host_capability_id,
            "host_capability_digest": self.host_capability_digest,
            "composition_id": self.composition_id,
            "composition_digest": self.composition_digest,
            "layout_id": self.layout_id,
            "layout_digest": self.layout_digest,
            "requested_cpu_set": (
                None if self.requested_cpu_set is None else list(self.requested_cpu_set)
            ),
            "observation": self.observation.as_dict(),
            "success": self.success,
            "faults": list(self.faults),
            "enforced": self.enforced,
            "isolated": self.isolated,
            "authority": {
                "resource_evidence": True,
                "resource_authorization": False,
                "outward_move": False,
            },
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.as_dict())


def assess_resource_placement(
    host: HostCapabilities,
    composition: CompositionProfile,
    profiles: Mapping[str, EngineResourceProfile],
) -> ResourcePlacementAssessment:
    if not isinstance(host, HostCapabilities):
        raise ResourceControlError("host must be HostCapabilities")
    if not isinstance(composition, CompositionProfile):
        raise ResourceControlError("composition must be CompositionProfile")
    composition.validate_against(profiles)

    required_slots = sum(binding.cpu_slots for binding in composition.bindings)
    available = None if host.allowed_cpus is None else len(host.allowed_cpus)
    mode = composition.enforcement_required.value

    if required_slots > composition.declared_cpu_slots:
        return ResourcePlacementAssessment(
            disposition=FALLBACK_SCHEDULED_REUSE_UNSUPPORTED,
            ready=False,
            fallback_required=True,
            enforcement_mode=mode,
            required_cpu_slots=required_slots,
            available_cpu_slots=available,
            expected_memory_mib=composition.expected_memory_mib,
            effective_memory_limit_bytes=host.effective_memory_limit_bytes,
            reasons=(
                "resident bindings exceed declared slots; concurrency-group reuse "
                "is not enforceable until J9",
            ),
        )

    if available is not None and required_slots > available:
        return ResourcePlacementAssessment(
            disposition=FALLBACK_INSUFFICIENT_CPU,
            ready=False,
            fallback_required=True,
            enforcement_mode=mode,
            required_cpu_slots=required_slots,
            available_cpu_slots=available,
            expected_memory_mib=composition.expected_memory_mib,
            effective_memory_limit_bytes=host.effective_memory_limit_bytes,
            reasons=("host exposes fewer effective CPUs than resident profile slots",),
        )

    needed_memory = composition.expected_memory_mib * 1024 * 1024
    if (
        host.effective_memory_limit_bytes is not None
        and host.effective_memory_limit_bytes < needed_memory
    ):
        return ResourcePlacementAssessment(
            disposition=FALLBACK_INSUFFICIENT_MEMORY,
            ready=False,
            fallback_required=True,
            enforcement_mode=mode,
            required_cpu_slots=required_slots,
            available_cpu_slots=available,
            expected_memory_mib=composition.expected_memory_mib,
            effective_memory_limit_bytes=host.effective_memory_limit_bytes,
            reasons=("effective memory limit is below the composition expectation",),
        )

    if composition.enforcement_required is EnforcementMode.CGROUP_V2:
        return ResourcePlacementAssessment(
            disposition=FALLBACK_UNSUPPORTED_ENFORCEMENT,
            ready=False,
            fallback_required=True,
            enforcement_mode=mode,
            required_cpu_slots=required_slots,
            available_cpu_slots=available,
            expected_memory_mib=composition.expected_memory_mib,
            effective_memory_limit_bytes=host.effective_memory_limit_bytes,
            reasons=("J5 does not implement writable cgroup-v2 placement",),
        )

    if composition.enforcement_required is EnforcementMode.AFFINITY:
        if not host.capacity_complete or host.allowed_cpus is None:
            return ResourcePlacementAssessment(
                disposition=FALLBACK_INCOMPLETE_HOST,
                ready=False,
                fallback_required=True,
                enforcement_mode=mode,
                required_cpu_slots=required_slots,
                available_cpu_slots=available,
                expected_memory_mib=composition.expected_memory_mib,
                effective_memory_limit_bytes=host.effective_memory_limit_bytes,
                reasons=("affinity enforcement requires complete J2 capacity evidence",),
            )
        if host.cpu_quota_status == "limited":
            assert host.cpu_quota_equivalents is not None
            if host.cpu_quota_equivalents + 1e-12 < required_slots:
                return ResourcePlacementAssessment(
                    disposition=FALLBACK_CPU_QUOTA,
                    ready=False,
                    fallback_required=True,
                    enforcement_mode=mode,
                    required_cpu_slots=required_slots,
                    available_cpu_slots=available,
                    expected_memory_mib=composition.expected_memory_mib,
                    effective_memory_limit_bytes=host.effective_memory_limit_bytes,
                    reasons=("CPU quota is below simultaneous resident slot demand",),
                )
        elif host.cpu_quota_status != "unlimited":
            return ResourcePlacementAssessment(
                disposition=FALLBACK_INCOMPLETE_HOST,
                ready=False,
                fallback_required=True,
                enforcement_mode=mode,
                required_cpu_slots=required_slots,
                available_cpu_slots=available,
                expected_memory_mib=composition.expected_memory_mib,
                effective_memory_limit_bytes=host.effective_memory_limit_bytes,
                reasons=("CPU quota state is unknown",),
            )

    return ResourcePlacementAssessment(
        disposition=READY,
        ready=True,
        fallback_required=False,
        enforcement_mode=mode,
        required_cpu_slots=required_slots,
        available_cpu_slots=available,
        expected_memory_mib=composition.expected_memory_mib,
        effective_memory_limit_bytes=host.effective_memory_limit_bytes,
        reasons=(),
    )


def build_affinity_layout(
    host: HostCapabilities,
    composition: CompositionProfile,
    profiles: Mapping[str, EngineResourceProfile],
) -> ResourceLayoutPlan:
    assessment = assess_resource_placement(host, composition, profiles)
    if not assessment.ready:
        raise ResourceFallbackRequired(assessment)
    if composition.enforcement_required is not EnforcementMode.AFFINITY:
        raise ResourceControlError("affinity layout requested for non-affinity composition")
    assert host.allowed_cpus is not None

    ordered = sorted(
        composition.bindings,
        key=lambda row: (
            0 if row.role is CompositionRole.ANCHOR else 1,
            row.instance,
        ),
    )
    cpus = list(host.allowed_cpus)
    cursor = 0
    bindings: list[ResourceLayoutBinding] = []
    for binding in ordered:
        selected = tuple(cpus[cursor : cursor + binding.cpu_slots])
        if len(selected) != binding.cpu_slots:
            raise ResourceControlError("layout allocator ran out of CPUs after assessment")
        bindings.append(
            ResourceLayoutBinding(
                instance=binding.instance,
                profile_id=binding.profile_id,
                cpu_set=selected,
            )
        )
        cursor += binding.cpu_slots

    return ResourceLayoutPlan(
        policy_id="anchor-first-contiguous-v1",
        host_capability_id=host.capability_id,
        host_capability_digest=host.digest,
        composition_id=composition.composition_id,
        composition_digest=composition.digest,
        allowed_cpus=host.allowed_cpus,
        bindings=tuple(bindings),
    )


class ResourceController:
    """One host/composition placement session."""

    def __init__(
        self,
        *,
        host: HostCapabilities,
        composition: CompositionProfile,
        profiles: Mapping[str, EngineResourceProfile],
        provider: LinuxAffinityProvider | None = None,
    ) -> None:
        self.host = host
        self.composition = composition
        self.profiles = dict(profiles)
        self.provider = provider or LinuxAffinityProvider()
        self.assessment = assess_resource_placement(
            host,
            composition,
            self.profiles,
        )
        self.layout = (
            build_affinity_layout(host, composition, self.profiles)
            if self.assessment.ready
            and composition.enforcement_required is EnforcementMode.AFFINITY
            else None
        )
        self._state: dict[str, ResourcePlacementEvidence] = {}

    @classmethod
    def from_catalog(
        cls,
        *,
        host: HostCapabilities,
        catalog,
        composition_id: str | None = None,
        provider: LinuxAffinityProvider | None = None,
    ) -> "ResourceController":
        composition = catalog.composition(
            composition_id or catalog.default_composition_id
        )
        profiles = {
            binding.profile_id: catalog.profile(binding.profile_id)
            for binding in composition.bindings
        }
        return cls(
            host=host,
            composition=composition,
            profiles=profiles,
            provider=provider,
        )

    def require_ready(self) -> None:
        if not self.assessment.ready:
            raise ResourceFallbackRequired(self.assessment)

    def _binding(self, instance: str):
        for binding in self.composition.bindings:
            if binding.instance == instance:
                return binding
        raise ResourceControlError(
            f"composition does not contain instance {instance!r}"
        )

    def _live_host_affinity(self) -> tuple[int, ...]:
        live = self.provider.host_affinity()
        if (
            self.composition.enforcement_required is EnforcementMode.AFFINITY
            and self.host.affinity_cpus is not None
            and tuple(self.host.affinity_cpus) != tuple(live)
        ):
            assessment = ResourcePlacementAssessment(
                disposition=FALLBACK_HOST_CPUSET_CHANGED,
                ready=False,
                fallback_required=True,
                enforcement_mode=self.composition.enforcement_required.value,
                required_cpu_slots=self.assessment.required_cpu_slots,
                available_cpu_slots=len(live),
                expected_memory_mib=self.composition.expected_memory_mib,
                effective_memory_limit_bytes=self.host.effective_memory_limit_bytes,
                reasons=(
                    "live sched_getaffinity differs from HostCapabilities used "
                    "to build the layout",
                ),
            )
            raise ResourceFallbackRequired(assessment)
        return live

    def _evidence(
        self,
        *,
        instance: str,
        observation: ProcessTreeAffinity,
        requested: tuple[int, ...] | None,
        success: bool,
        faults: tuple[str, ...] = (),
    ) -> ResourcePlacementEvidence:
        layout = self.layout
        evidence = ResourcePlacementEvidence(
            provider_id=self.provider.provider_id,
            instance=instance,
            enforcement_mode=self.composition.enforcement_required.value,
            host_capability_id=self.host.capability_id,
            host_capability_digest=self.host.digest,
            composition_id=self.composition.composition_id,
            composition_digest=self.composition.digest,
            layout_id=None if layout is None else layout.layout_id,
            layout_digest=None if layout is None else layout.digest,
            requested_cpu_set=requested,
            observation=observation,
            success=success,
            faults=faults,
            enforced=(
                success
                and self.composition.enforcement_required is EnforcementMode.AFFINITY
            ),
            isolated=False,
        )
        self._state[instance] = evidence
        return evidence

    def place_instance(self, instance: str, pid: int) -> ResourcePlacementEvidence:
        self.require_ready()
        self._binding(instance)
        if self.composition.enforcement_required is EnforcementMode.OBSERVED:
            try:
                observed = self.provider.inspect_tree_affinity(pid)
            except LinuxAffinityError as exc:
                raise ResourceControlError(
                    f"{instance}: observed placement evidence failed: {exc}"
                ) from exc
            return self._evidence(
                instance=instance,
                observation=observed,
                requested=None,
                success=True,
            )

        if self.composition.enforcement_required is not EnforcementMode.AFFINITY:
            raise ResourceControlError("unsupported enforcement mode reached placement")
        assert self.layout is not None
        live = set(self._live_host_affinity())
        requested = self.layout.binding(instance).cpu_set
        if not set(requested).issubset(live):
            assessment = ResourcePlacementAssessment(
                disposition=FALLBACK_HOST_CPUSET_CHANGED,
                ready=False,
                fallback_required=True,
                enforcement_mode=EnforcementMode.AFFINITY.value,
                required_cpu_slots=self.assessment.required_cpu_slots,
                available_cpu_slots=len(live),
                expected_memory_mib=self.composition.expected_memory_mib,
                effective_memory_limit_bytes=self.host.effective_memory_limit_bytes,
                reasons=(f"{instance}: requested CPUs no longer exist in live affinity",),
            )
            raise ResourceFallbackRequired(assessment)
        try:
            observed = self.provider.apply_tree_affinity(pid, requested)
        except LinuxAffinityError as exc:
            raise ResourceControlError(
                f"{instance}: affinity application failed: {exc}"
            ) from exc
        return self._evidence(
            instance=instance,
            observation=observed,
            requested=requested,
            success=True,
        )

    def verify_instance(self, instance: str, pid: int) -> ResourcePlacementEvidence:
        self.require_ready()
        self._binding(instance)
        previous = self._state.get(instance)
        try:
            observed = self.provider.inspect_tree_affinity(pid)
        except LinuxAffinityError as exc:
            raise ResourceControlError(
                f"{instance}: placement verification failed: {exc}"
            ) from exc

        if previous is not None:
            if (
                observed.root_pid != previous.observation.root_pid
                or observed.root_start_time_ticks
                != previous.observation.root_start_time_ticks
            ):
                raise ResourceControlError(
                    f"{instance}: root process identity changed since placement"
                )

        if self.composition.enforcement_required is EnforcementMode.OBSERVED:
            return self._evidence(
                instance=instance,
                observation=observed,
                requested=None,
                success=True,
            )

        if self.composition.enforcement_required is not EnforcementMode.AFFINITY:
            raise ResourceControlError("unsupported enforcement mode reached verification")
        assert self.layout is not None
        self._live_host_affinity()
        requested = self.layout.binding(instance).cpu_set
        mismatches = [row for row in observed.tasks if row.cpus != requested]
        if mismatches:
            details = ", ".join(
                f"pid={row.identity.pid}/tid={row.identity.tid}:{list(row.cpus)}"
                for row in mismatches
            )
            failed = self._evidence(
                instance=instance,
                observation=observed,
                requested=requested,
                success=False,
                faults=(f"effective affinity mismatch: {details}",),
            )
            raise ResourceControlError(
                f"{instance}: effective affinity mismatch; evidence={failed.digest}"
            )
        return self._evidence(
            instance=instance,
            observation=observed,
            requested=requested,
            success=True,
        )

    def finalize_instance(self, instance: str, pid: int) -> ResourcePlacementEvidence:
        """Re-enforce after warmup to catch lazily-created worker tasks."""
        if self.composition.enforcement_required is EnforcementMode.AFFINITY:
            return self.place_instance(instance, pid)
        return self.verify_instance(instance, pid)

    def finalize_all(
        self, pids: Mapping[str, int]
    ) -> dict[str, ResourcePlacementEvidence]:
        self.require_ready()
        expected = {binding.instance for binding in self.composition.bindings}
        if set(pids) != expected:
            raise ResourceControlError(
                "runtime PID map does not exactly match composition instances"
            )
        return {
            instance: self.finalize_instance(instance, pids[instance])
            for instance in sorted(pids)
        }

    def verify_all(
        self, pids: Mapping[str, int]
    ) -> dict[str, ResourcePlacementEvidence]:
        self.require_ready()
        expected = {binding.instance for binding in self.composition.bindings}
        if set(pids) != expected:
            raise ResourceControlError(
                "runtime PID map does not exactly match composition instances"
            )
        return {
            instance: self.verify_instance(instance, pids[instance])
            for instance in sorted(pids)
        }

    def resource_state(self, instance: str) -> ResourcePlacementEvidence | None:
        return self._state.get(instance)

    def resource_state_digest(self, instance: str) -> str | None:
        evidence = self.resource_state(instance)
        return None if evidence is None else evidence.digest

    def reset_runtime_state(self) -> None:
        """Discard PID/task evidence while retaining the immutable host/layout plan."""
        self._state.clear()
