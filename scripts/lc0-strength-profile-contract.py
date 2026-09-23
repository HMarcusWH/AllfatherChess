#!/usr/bin/env python3
"""Run the LC0 real-network / real-backend qualification contract."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.process import UciProcess, UciProcessError
from adapters.telemetry import Lc0TelemetryAdapter
from controller.strength_profile import (
    QualificationReport,
    StrengthProfileError,
    load_json,
    validate_profile,
    validate_runtime_config,
    validate_vendor_binding,
    verify_network_file,
)

LOCK_PATH = ROOT / "qualification" / "lc0-strength.lock.json"
PROFILE_PATH = ROOT / "qualification" / "lc0-strength-profile.json"
CONFIG_PATH = ROOT / "config" / "allfather.strength.validation.json"
RESULT_DIR = ROOT / "build" / "test-results" / "lc0-strength"
_MOVE_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class ContractError(RuntimeError):
    pass


def observe_backend(requested: str, stderr_lines: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """Infer only from backend-specific runtime diagnostics emitted by LC0."""

    if requested == "blas":
        vendor = tuple(line for line in stderr_lines if line.startswith("BLAS vendor:"))
        max_batch = tuple(
            line for line in stderr_lines if line.startswith("BLAS max batch size is ")
        )
        implementation = tuple(
            line for line in stderr_lines
            if line.startswith("OpenBLAS [") or line.startswith("OpenBLAS found ")
        )
        if not vendor or not max_batch:
            raise ContractError(
                "requested BLAS backend but LC0 did not emit the required "
                f"BLAS runtime diagnostics; stderr tail={stderr_lines!r}"
            )
        return "blas", vendor + implementation + max_batch

    explicit = tuple(
        line for line in stderr_lines
        if f"Creating backend [{requested}]" in line
    )
    if explicit:
        return requested, explicit
    raise ContractError(
        f"cannot prove requested backend {requested!r} from LC0 runtime "
        f"diagnostics; stderr tail={stderr_lines!r}"
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_search(
    process: UciProcess,
    *,
    token: int,
    nodes: int,
    adapter: Lc0TelemetryAdapter | None = None,
) -> tuple[str, list[dict]]:
    done = threading.Event()
    bestmove: list[str] = []
    events: list[dict] = []
    started = time.monotonic()

    if adapter is not None:
        events.append(
            adapter.start(
                position={"command": "position startpos", "variant": "standard"},
                request={"kind": "nodes", "nodes": nodes},
                observed_ms=0,
            )
        )

    def observed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    def on_info(_token: int, line: str) -> None:
        if adapter is not None:
            events.extend(adapter.consume(line, observed_ms=observed_ms()))

    def on_complete(_token: int, line: str) -> None:
        fields = line.split()
        if len(fields) < 2 or _MOVE_RE.fullmatch(fields[1].lower()) is None:
            bestmove.append("")
        else:
            bestmove.append(fields[1].lower())
        if adapter is not None:
            events.extend(adapter.consume(line, observed_ms=observed_ms()))
        done.set()

    process.new_game()
    process.send_position("position startpos")
    process.start_search(
        f"go nodes {nodes}",
        token=token,
        on_info=on_info,
        on_complete=on_complete,
    )
    if not done.wait(60.0):
        process.stop()
        raise ContractError(f"LC0 search n{nodes} timed out")
    if len(bestmove) != 1 or not bestmove[0]:
        raise ContractError(f"LC0 search n{nodes} did not return one canonical bestmove")
    return bestmove[0], events


def hardware_probe() -> dict:
    raw = subprocess.check_output(
        [sys.executable, str(ROOT / "scripts" / "lc0-hardware-probe.py")],
        text=True,
    )
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ContractError("hardware probe did not return an object")
    return data


def git_head_sha() -> str:
    value = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ContractError(f"checked-out source HEAD is not a canonical SHA: {value!r}")
    return value


def main() -> int:
    lock = load_json(LOCK_PATH)
    vendor = load_json(ROOT / "vendor.lock.json")
    profile = load_json(PROFILE_PATH)
    config = load_json(CONFIG_PATH)
    validate_vendor_binding(lock, vendor)
    validate_profile(profile)
    validate_runtime_config(config, lock, profile)

    source_sha = git_head_sha()
    expected_source_sha = os.environ.get("ALLFATHER_SOURCE_SHA")
    if expected_source_sha and expected_source_sha != source_sha:
        raise ContractError(
            f"workflow source identity mismatch: expected {expected_source_sha}, "
            f"checked out {source_sha}"
        )

    network_path = ROOT / "build" / "artifacts" / "lc0" / lock["network"]["filename"]
    network_identity = verify_network_file(network_path, lock, require_frozen=True)

    lc0_name = config["shadow"]["instance_by_owner"]["lc0"]
    lc0 = config["instances"][lc0_name]
    binary = (ROOT / lc0["binary"]).resolve()
    if not binary.is_file():
        raise ContractError(f"LC0 strength binary is missing: {binary}")
    binary_identity = {
        "path": str(binary),
        "size": binary.stat().st_size,
        "sha256": sha256_file(binary),
    }

    hardware = hardware_probe()
    policy = profile["hardware_policy"]
    if hardware.get("commit_sha") != source_sha:
        raise ContractError(
            f"hardware probe source {hardware.get('commit_sha')!r} does not "
            f"match checked-out source {source_sha!r}"
        )
    if hardware.get("runner_class") != policy["runner_class"]:
        raise ContractError(
            f"hardware runner class {hardware.get('runner_class')!r} does not "
            f"match profile {policy['runner_class']!r}"
        )
    if hardware.get("runner_environment") != "github-hosted":
        raise ContractError("reference qualification requires a GitHub-hosted runner")
    if hardware.get("system") != "Linux":
        raise ContractError(f"reference qualification requires Linux, got {hardware.get('system')!r}")
    os_release = hardware.get("os_release")
    if (
        not isinstance(os_release, dict)
        or os_release.get("ID") != "ubuntu"
        or os_release.get("VERSION_ID") != "24.04"
    ):
        raise ContractError(
            f"reference qualification requires Ubuntu 24.04, got {os_release!r}"
        )
    if policy.get("os") != "ubuntu-24.04":
        raise ContractError(f"unsupported hardware policy OS: {policy.get('os')!r}")
    if policy.get("accelerator_kind") != "cpu":
        raise ContractError(
            f"reference qualification currently supports accelerator_kind=cpu, "
            f"got {policy.get('accelerator_kind')!r}"
        )
    if hardware.get("architecture") not in {policy["architecture"], "amd64"}:
        raise ContractError(
            f"hardware architecture {hardware.get('architecture')!r} does not "
            f"match profile {policy['architecture']!r}"
        )
    if not hardware.get("cpu_model"):
        raise ContractError("hardware probe did not bind a CPU model")
    memory = hardware.get("memory_bytes")
    if isinstance(memory, bool) or not isinstance(memory, int) or memory <= 0:
        raise ContractError("hardware probe did not bind positive physical memory")
    if not hardware.get("openblas_package"):
        raise ContractError("OpenBLAS package identity is missing")

    package_evidence = hardware.get("packages")
    if not isinstance(package_evidence, dict):
        raise ContractError("hardware probe did not bind build package identities")
    for package in profile["build"]["required_packages"]:
        observed = subprocess.check_output(
            ["dpkg-query", "-W", "-f=${Package}=${Version}", package],
            text=True,
        ).strip()
        if not observed.startswith(package + "="):
            raise ContractError(
                f"required package {package!r} could not be bound: {observed!r}"
            )
        if package_evidence.get(package) != observed:
            raise ContractError(
                f"hardware package evidence mismatch for {package!r}: "
                f"probe={package_evidence.get(package)!r}, live={observed!r}"
            )

    toolchain = hardware.get("toolchain")
    if not isinstance(toolchain, dict):
        raise ContractError("hardware probe did not bind the LC0 build toolchain")
    for name in ("gcc", "g++", "meson", "ninja", "pkg-config", "protoc"):
        value = toolchain.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ContractError(f"hardware probe did not bind toolchain component {name!r}")

    options = dict(lc0["options"])
    process = UciProcess(
        name="lc0-strength",
        binary=binary,
        cwd=ROOT,
        args=list(lc0.get("args") or []),
        timeout=30.0,
    )
    try:
        process.start()
        process.configure(options)

        warmup_move, _ = run_search(
            process,
            token=1,
            nodes=int(profile["warmup"]["nodes"]),
        )

        adapter = Lc0TelemetryAdapter(
            search_id="lc0-strength-qualification",
            engine_instance="lc0-strength",
            position_id="startpos",
            variant="standard",
            score_type=options["ScoreType"],
        )
        qualification_move, events = run_search(
            process,
            token=2,
            nodes=256,
            adapter=adapter,
        )

        observed_backend, backend_evidence = observe_backend(
            options["Backend"],
            process.stderr_tail,
        )
    finally:
        process.close()

    expected_semantics = f"lc0.uci_score.{options['ScoreType']}"
    semantics = {
        evaluation.get("semantics")
        for event in events
        if event.get("event_type") == "candidate.update"
        for evaluation in (event.get("candidate") or {}).get("evaluations", [])
        if isinstance(evaluation, dict)
    }
    if expected_semantics not in semantics:
        raise ContractError(
            f"real LC0 telemetry did not expose expected score semantics "
            f"{expected_semantics!r}; observed={sorted(str(x) for x in semantics)}"
        )

    report = QualificationReport(
        profile_id=profile["profile_id"],
        commit_sha=source_sha,
        contracts={
            "vendor_lock_sha256": sha256_file(ROOT / "vendor.lock.json"),
            "strength_lock_sha256": sha256_file(LOCK_PATH),
            "strength_profile_sha256": sha256_file(PROFILE_PATH),
            "runtime_config_sha256": sha256_file(CONFIG_PATH),
        },
        binary=binary_identity,
        network=network_identity,
        requested_backend=options["Backend"],
        observed_backend=observed_backend,
        backend_evidence=backend_evidence,
        runtime_options={
            name: options[name]
            for name in (
                "Backend",
                "BackendOptions",
                "WeightsFile",
                "ScoreType",
                "NNCacheSize",
                "MinibatchSize",
                "MaxConcurrentSearchers",
                "TaskWorkers",
                "Threads",
                "MultiPV",
            )
        },
        hardware=hardware,
        warmup_bestmove=warmup_move,
        qualification_bestmove=qualification_move,
        telemetry_score_semantics=expected_semantics,
    ).as_dict()

    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULT_DIR / "report.json"
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        "LC0 real-inference qualification passed: "
        f"report={report['report_id']} backend={options['Backend']} "
        f"network={network_identity['sha256'][:16]}..."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        ContractError,
        StrengthProfileError,
        UciProcessError,
        OSError,
        ValueError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"lc0-strength-profile contract failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
