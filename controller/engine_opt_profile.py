"""Static contracts for the ENGINE-OPT-V2 candidate and promoted profiles."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

class EngineOptProfileError(RuntimeError):
    pass

def load_json(path: Path | str) -> dict[str, Any]:
    try:
        value=json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc:
        raise EngineOptProfileError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value,dict):
        raise EngineOptProfileError(f"{path}: root must be an object")
    return value

def require(condition: bool, message: str) -> None:
    if not condition:
        raise EngineOptProfileError(message)

def validate_reference(policy: dict[str,Any], selection: dict[str,Any], config: dict[str,Any], *, require_selected: bool=False) -> None:
    require(policy.get("schema_version")==1 and policy.get("profile_id")=="online-engine-opt-v2","wrong ENGINE-OPT reference policy")
    require(selection.get("profile_id")=="engine-opt-v2-selection","wrong ENGINE-OPT selection")
    if require_selected:
        require(selection.get("status")=="selected","ENGINE-OPT promotion requires a measured, frozen selection")
        require(isinstance(selection.get("source_report"),dict),"selected ENGINE-OPT profile must bind its benchmark report")
    require(policy.get("bundle_root")=="build/online-engine-opt-v2","unexpected v2 bundle root")
    require(config.get("mode")=="active","ENGINE-OPT reference must use active observation mode")
    instances=config.get("instances") or {}
    require(set(instances)=={"stockfish-anchor","stockfish-shadow","reckless-shadow","lc0-shadow"},"v2 instance set drift")
    lc0=instances["lc0-shadow"]
    require(lc0.get("family")=="lc0" and lc0.get("role")=="shadow","wrong LC0 specialist identity")
    opts=lc0.get("options") or {}
    for name in ("Backend","WeightsFile","NNCacheSize","MinibatchSize","MaxPrefetch","AdaptivePrefetch","DefectTelemetry","MultiPV"):
        require(name in opts,f"v2 LC0 must bind {name} explicitly")
    require(opts["Backend"]=="blas","promoted v2 reference remains CPU-BLAS until GPU accounting is qualified")
    require(isinstance(opts["NNCacheSize"],int) and opts["NNCacheSize"]>0,"v2 LC0 cache must be explicitly nonzero")
    require((lc0.get("warmup") or {}).get("reset_after") is True,"v2 LC0 warmup must reset state")
    phases=lc0.get("phase_options") or {}
    require((phases.get("EXPLORE") or {}).get("MultiPV")==1,"LC0 EXPLORE must use MultiPV=1")
    require((phases.get("VERIFY") or {}).get("MultiPV")==3,"LC0 VERIFY must use MultiPV=3")
    require((phases.get("STAGED_VERIFY") or {}).get("MultiPV")==3,"LC0 staged VERIFY must use MultiPV=3")
    shadow=config.get("shadow") or {}
    require(shadow.get("dispatch_limit")=={"nodes":16},"v2 preserves n16 EXPLORE identity")
    routing=config.get("routing") or {}
    require(set((routing.get("stage_cpu_ms_estimate_by_owner") or {}))=={"stockfish","reckless","lc0"},"v2 requires owner-specific EXPLORE resource estimates")
    require(set((routing.get("verify_stage_cpu_ms_estimate_by_owner") or {}))=={"stockfish","reckless","lc0"},"v2 requires owner-specific VERIFY resource estimates")
    require((config.get("resource_measurement") or {}).get("require_gpu_for_claim") is False,"v2 CPU profile may not make GPU claims")
    require((config.get("budget") or {}).get("gpu_ms")==0,"v2 CPU profile budget must remain GPU-free")

def validate_hybrid(policy: dict[str,Any], reference: dict[str,Any], hybrid: dict[str,Any]) -> None:
    require(policy.get("profile_id")=="online-hybrid-v2","wrong v2 hybrid policy")
    require(policy.get("source_profile")=="online-engine-opt-v2","v2 hybrid must derive from engine-opt reference")
    require(hybrid.get("instances")==reference.get("instances"),"v2 hybrid must reuse exact optimized engine identities")
    require(hybrid.get("resource_measurement")==reference.get("resource_measurement"),"v2 hybrid resource provider drift")
    require((hybrid.get("shadow") or {}).get("dispatch_limit")=={"nodes":16},"v2 hybrid EXPLORE must remain n16")
    verify=hybrid.get("verification") or {}
    require(verify.get("dispatch_limit")=={"nodes":16},"v2 base VERIFY must remain n16")
    require((verify.get("staged_extension") or {}).get("dispatch_limit")=={"nodes":32},"v2 staged VERIFY must remain n32")
    authority=hybrid.get("hybrid_authority") or {}
    require(authority.get("policy")=="clocked_staged_preanchor_v1","v2 may not change G3 authority policy")
    require(authority.get("allow_skipped_extension_authority") is False,"v2 may not license SKIP authority")
