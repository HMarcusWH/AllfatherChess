#!/usr/bin/env python3
"""End-to-end M14-B measured resource accounting contract."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from controller.runtime import load_runtime_config


CONFIG_PATH = ROOT / "config" / "allfather.active.specialist.validation.json"
ACTIVE_REPORT = ROOT / "build" / "test-results" / "active-specialist" / "report.json"
RESULT_DIR = ROOT / "build" / "test-results" / "resource-accounting"


class ContractError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_active_run() -> dict:
    if not ACTIVE_REPORT.is_file():
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "active-specialist-contract.py")],
            cwd=ROOT,
            check=True,
        )
    return json.loads(ACTIVE_REPORT.read_text(encoding="utf-8"))


def main() -> int:
    config = load_runtime_config(CONFIG_PATH)
    settings = config.resource_measurement
    if settings is None or not settings.enabled or not settings.require_cpu_for_claim:
        raise ContractError("active specialist profile must require measured CPU")
    if settings.require_gpu_for_claim:
        raise ContractError("CPU reference profile must not claim measured GPU time")
    if config.shadow is None or config.budget is None:
        raise ContractError("active specialist profile is incomplete")

    active = ensure_active_run()
    run_id = active["run_id"]
    run_dir = config.shadow.replay_root / run_id
    resource_path = run_dir / "resource.json"
    route_path = run_dir / "route.json"
    if not resource_path.is_file() or not route_path.is_file():
        # The report may belong to an older pre-M14-B run left in build/. Run
        # the current contract once and reload the resulting run identity.
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "active-specialist-contract.py")],
            cwd=ROOT,
            check=True,
        )
        active = json.loads(ACTIVE_REPORT.read_text(encoding="utf-8"))
        run_id = active["run_id"]
        run_dir = config.shadow.replay_root / run_id
        resource_path = run_dir / "resource.json"
        route_path = run_dir / "route.json"
    if not resource_path.is_file() or not route_path.is_file():
        raise ContractError("active run did not produce resource.json and route.json")

    resource = json.loads(resource_path.read_text(encoding="utf-8"))
    route = json.loads(route_path.read_text(encoding="utf-8"))
    summary = route.get("resource_measurement")
    if not isinstance(summary, dict):
        raise ContractError("route.json does not bind resource measurement evidence")
    if summary.get("sha256") != sha256_file(resource_path):
        raise ContractError("route.json resource hash does not match resource.json")
    if summary.get("report_id") != resource.get("report_id"):
        raise ContractError("route/resource report ids disagree")
    if summary.get("provider") != "linux-procfs-v1":
        raise ContractError(f"unexpected provider: {summary.get('provider')!r}")

    coverage = resource.get("coverage") or {}
    cpu = coverage.get("cpu") or {}
    gpu = coverage.get("gpu") or {}
    if cpu.get("required") is not True or cpu.get("complete") is not True:
        raise ContractError(f"CPU measurement coverage incomplete: {cpu}")
    if gpu.get("required") is not False:
        raise ContractError(f"CPU reference unexpectedly requires GPU measurement: {gpu}")
    if resource.get("qualified") is not True:
        raise ContractError("resource report did not qualify")

    stages = resource.get("stages") or []
    if not stages or not all(row.get("complete") for row in stages):
        raise ContractError("one or more dispatched resource stages are incomplete")
    phases = {row.get("phase") for row in stages}
    for phase in ("ANCHOR", "QUALIFY", "EXPLORE", "VERIFY"):
        if phase not in phases:
            raise ContractError(f"resource report is missing {phase} measurement")

    processes = resource.get("processes") or {}
    expected = set(config.backends)
    if set(processes) != expected:
        raise ContractError(
            f"run-level process coverage mismatch: expected {sorted(expected)}, "
            f"got {sorted(processes)}"
        )
    if not all(row.get("complete") for row in processes.values()):
        raise ContractError("one or more run-level backend process measurements are incomplete")

    physical_cpu = resource.get("physical_cpu_ms")
    if not isinstance(physical_cpu, (int, float)) or physical_cpu <= 0:
        raise ContractError(f"invalid physical CPU total: {physical_cpu!r}")
    if physical_cpu > float(config.budget["cpu_ms"]):
        raise ContractError(
            f"physical CPU {physical_cpu}ms exceeds envelope {config.budget['cpu_ms']}ms"
        )

    claim = route.get("envelope_claim") or {}
    if claim.get("cpu_measurement") != "linux-procfs-v1":
        raise ContractError(f"route still reports estimated CPU: {claim}")
    if claim.get("physical_measurement_required") is not True:
        raise ContractError("route did not require physical resource evidence")
    if claim.get("physical_measurement_qualified") is not True:
        raise ContractError("route physical resource evidence did not qualify")
    if claim.get("physical_cpu_within_envelope") is not True:
        raise ContractError("physical CPU did not satisfy envelope")
    if claim.get("claimed") is not True:
        raise ContractError("measured equal-envelope control-plane claim did not close")

    budget = route.get("budget") or {}
    lanes = budget.get("lanes") or {}
    for lane in ("anchor", "shadow:stockfish", "shadow:reckless", "shadow:lc0"):
        row = lanes.get(lane)
        if not isinstance(row, dict):
            raise ContractError(f"budget is missing lane {lane!r}")
        if row.get("measured_settlements", 0) < 1:
            raise ContractError(f"lane {lane!r} did not settle from measured CPU: {row}")

    report = {
        "schema_version": 1,
        "run_id": run_id,
        "provider": resource["provider"],
        "report_id": resource["report_id"],
        "resource_sha256": summary["sha256"],
        "physical_cpu_ms": physical_cpu,
        "engine_cpu_ms": resource["engine_cpu_ms"],
        "controller_cpu_ms": resource["controller"]["cpu_ms"],
        "phases": sorted(str(value) for value in phases),
        "processes": processes,
        "envelope_claim": claim,
        "claim": (
            "M14-B control-plane qualification: engine process CPU is measured "
            "from Linux procfs, controller CPU from process_time_ns, reservations "
            "remain separately auditable, and missing physical evidence fails "
            "the measured envelope certificate. No Elo or strength claim."
        ),
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "Resource accounting contract passed: "
        f"report={resource['report_id']} physical_cpu_ms={physical_cpu:.1f}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ContractError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        message = f"resource accounting contract failure: {exc}"
        print(message, file=sys.stderr)
        (RESULT_DIR / "failure.txt").write_text(message + "\n", encoding="utf-8")
        raise SystemExit(1)
