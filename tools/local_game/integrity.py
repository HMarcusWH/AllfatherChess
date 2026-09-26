"""Independent qualification of campaign identities, prerequisites and actual commands."""
from __future__ import annotations

from pathlib import Path
import math
import re

from .common import ROOT, load, require, sha, verify_record, contained, runtime_config


def input_paths(p: dict) -> list[Path]:
    return [ROOT / path for path in (
        "qualification/local-full-game.json", "qualification/fastchess.lock.json",
        "qualification/local-game-requirements.txt", p["source_runtime"],
        "build/online-cpu-reference/build-manifest.json", "build/tools/fastchess/build-manifest.json",
    )] + sorted((ROOT / "tests/fixtures/local_full_game").glob("*"))


def verify_builds(source: dict) -> Path:
    fc_root = ROOT / "build/tools/fastchess"
    fc = load(fc_root / "build-manifest.json")
    lock = ROOT / "qualification/fastchess.lock.json"
    require(fc["source"] == load(lock) and fc["lock_sha256"] == sha(lock), "Fastchess pin mismatch")
    require(fc["upstream_tests_passed"] is True, "Fastchess upstream tests missing")
    fastchess = verify_record(fc_root, fc["binary"])
    verify_record(fc_root, fc["license"])
    attestation = fc.get("source_test_attestation")
    if attestation is not None:
        attestation_path = verify_record(fc_root, attestation)
        tested = load(attestation_path)
        require(tested.get("passed") is True, "Fastchess source-test attestation is not a pass")
        require(tested.get("commit") == fc["source"]["commit"] and
                tested.get("tree") == fc["source"]["tree"] and
                tested.get("lock_sha256") == fc["lock_sha256"],
                "Fastchess source-test attestation identity mismatch")

    bundle = ROOT / "build/online-cpu-reference"
    build = load(bundle / "build-manifest.json")
    require(build["source_commit"] == source["commit"], "ONLINE-2 bundle source mismatch")
    for category in ("engines", "networks"):
        require(set(build["artifacts"][category]) == {"stockfish", "reckless", "lc0"},
                "bundle is missing a constituent identity")
        for item in build["artifacts"][category].values():
            verify_record(bundle, {"path": item["path"], "sha256": item["sha256"], "bytes": item["size"]})
    return fastchess


def verify_prerequisites(output: Path, source: dict) -> None:
    from controller.replay import verify_bundle_integrity
    from controller.final_decision import verify_final_decision_integrity
    from controller.counterfactual import verify_counterfactual_integrity

    root = output / "prerequisites"
    lc0, online, g3 = (load(root / f"{label}.json") for label in ("lc0", "online2", "g3"))
    build = load(ROOT / "build/online-cpu-reference/build-manifest.json")
    require(lc0["commit_sha"] == online["source_commit"] == source["commit"], "stale prerequisite source")
    require(lc0["requested_backend"] == lc0["observed_backend"] == "blas", "real BLAS not observed")
    require(lc0["binary"]["sha256"] == build["artifacts"]["engines"]["lc0"]["sha256"], "LC0 binary mismatch")
    require(lc0["network"]["sha256"] == build["artifacts"]["networks"]["lc0"]["sha256"], "LC0 weights mismatch")
    require(online["contracts"]["build_manifest_sha256"] == sha(ROOT / "build/online-cpu-reference/build-manifest.json"),
            "ONLINE-2 prerequisite bundle mismatch")
    require(online["contracts"]["lc0_reference_report_sha256"] == sha(root / "lc0.json"), "LC0 report binding mismatch")
    positive = g3["positive_case"]
    require(positive["authority"] == "HYBRID" and positive["emitted_move"] != positive["anchor_move"],
            "G3 positive prerequisite is not an actual override")
    run = root / "g3-positive-replay"
    for verifier in (verify_bundle_integrity, verify_final_decision_integrity, verify_counterfactual_integrity):
        problems = verifier(run)
        require(not problems, f"G3 prerequisite replay rejected: {problems}")
    manifest = load(run / "manifest.json")
    final = load(run / "decision/final.json")["decision"]
    require(manifest["run_id"] == positive["run_id"] == run.name,
            "G3 prerequisite run identity mismatch")
    require(final["authority"] == "HYBRID" and final["emitted_move"] == positive["emitted_move"] and
            final["anchor_move"] == positive["anchor_move"], "G3 prerequisite report disagrees with played decision")


def verify_probes(output: Path, declared: dict) -> None:
    from .probes import check_transition
    from controller.replay import verify_bundle_integrity
    from controller.final_decision import verify_final_decision_integrity

    root = output / "rule-probes"
    report = load(root / "report.json")
    require(report == declared and report["passed"] is True, "probe report/manifest mismatch")
    cases = load(ROOT / "tests/fixtures/local_full_game/rule-probes.json")
    require([r["case"] for r in report["cases"]] == cases, "missing/extra/modified rule probe")
    used = set()
    for row in report["cases"]:
        board = check_transition(row["case"], row["emitted"])
        require(row["after"] == board.fen(en_passant="fen"), "false post-transition FEN")
        require(row["run_id"] not in used, "reused probe replay")
        used.add(row["run_id"])
        run = contained(root / "replays", row["run_id"])
        for verifier in (verify_bundle_integrity, verify_final_decision_integrity):
            problems = verifier(run)
            require(not problems, f"rule-probe replay rejected: {problems}")
        m = load(run / "manifest.json")
        require(m["run_id"] == run.name, "probe replay directory/run_id mismatch")
        final = load(run / "decision/final.json")["decision"]
        require(final["emitted_move"] == row["emitted"], "probe final decision mismatch")
        require(m["position"]["base_fen"] == row["case"]["fen"] and
                m["position"]["moves"] == row["case"]["moves"], "probe history mismatch")
        require(m["clock_outcome"]["output_within_deadline"] is True, "late probe move")
    require({p.name for p in (root / "replays").iterdir() if p.is_dir()} == used, "orphan probe replay")


def _clock_ms(control: str) -> tuple[int, int]:
    require(isinstance(control, str) and "+" in control, "clock must contain an increment")
    base, increment = control.rsplit("+", 1)

    def seconds(text: str) -> float:
        parts = text.split(":")
        require(1 <= len(parts) <= 3 and all(part != "" for part in parts), "malformed base clock")
        total = 0.0
        for index, part in enumerate(reversed(parts)):
            value = float(part)
            require(math.isfinite(value) and value >= 0, "invalid clock component")
            total += value * (60 ** index)
        return total

    base_ms = int(round(seconds(base) * 1000))
    inc_value = float(increment)
    require(math.isfinite(inc_value) and inc_value >= 0, "invalid increment")
    return base_ms, int(round(inc_value * 1000))


def verify_session_commands(events: list[dict], arm: str, plan: dict, source: dict) -> None:
    """Check actual transmitted UCI limits/options and the scheduled clock."""
    from .runner import engine_options

    required, _ = engine_options(arm, source)
    options = {}
    expected_base, expected_increment = _clock_ms(plan["clock"])
    game_searches: dict[int, int] = {}

    for event in events:
        if event["direction"] != "in":
            continue
        line = event["line"]
        if line.startswith("setoption "):
            match = re.fullmatch(r"setoption name (.*?)(?: value(?: (.*))?)?", line)
            require(match is not None, "malformed recorded option")
            options[match[1]] = match[2] or ""
            require(not (match[1] == "Ponder" and (match[2] or "").lower() == "true"), "ponder enabled")
        if line == "go" or line.startswith("go "):
            for name, value in required.items():
                rendered = str(value).lower() if isinstance(value, bool) else str(value)
                require(options.get(name) == rendered, f"{arm}: effective {name} differs from baseline policy")
            words = line.split()[1:]
            require(len(words) % 2 == 0, "unexpected search restriction/limit in tournament")
            pairs = list(zip(words[::2], words[1::2]))
            limits = dict(pairs)
            require(len(limits) == len(pairs), "duplicate UCI limit")
            allowed = {"wtime", "btime", "winc", "binc"}
            if plan["driver_nodes"] is not None and arm == "stockfish":
                allowed.add("nodes")
                require(limits.get("nodes") == str(plan["driver_nodes"]), "driver work limit drift")
            require(set(limits) == allowed, "baseline received non-clock or missing limits")

            game = event.get("game")
            require(type(game) is int and game >= 1, "go is not bound to a positive game ordinal")
            seen = game_searches.get(game, 0)
            wtime, btime = int(limits["wtime"]), int(limits["btime"])
            winc, binc = int(limits["winc"]), int(limits["binc"])
            require(wtime >= 0 and btime >= 0, "negative tournament clock")
            require(winc == binc == expected_increment, "increment differs across arms")
            if seen == 0:
                require(wtime == btime == expected_base,
                        f"{arm}: first transmitted clocks do not match scheduled {plan['clock']}")
            # No clock may grow faster than one increment per already observed
            # turn for this engine session. This is deliberately a loose upper
            # bound after move one, but it catches cross-case/low-clock drift
            # without pretending the proxy is the tournament clock authority.
            ceiling = expected_base + seen * expected_increment
            require(wtime <= ceiling and btime <= ceiling,
                    f"{arm}: transmitted clock exceeds scheduled progression")
            game_searches[game] = seen + 1

    require(game_searches, f"{arm}: session transmitted no go command")


def _number(value, label: str) -> float:
    require(type(value) in (int, float) and math.isfinite(float(value)) and float(value) >= 0,
            f"{label} must be finite and non-negative")
    return float(value)


def _close(actual, expected, label: str, tolerance: float = 0.002) -> None:
    require(abs(_number(actual, label) - float(expected)) <= tolerance,
            f"{label} arithmetic mismatch: {actual!r} vs {expected!r}")


def verify_resource_claim(run: Path, manifest: dict) -> dict:
    """Reconstruct physical/resource-envelope qualification from primitive rows.

    Producer booleans are compared with independently derived values; they are
    never accepted as the premise of the LOCAL-1 gate.
    """
    from common.search_request import parse_go_request
    from controller.budget import ResourceEnvelope

    resource_path = run / "resource.json"
    route_path = run / "route.json"
    resource, route = load(resource_path), load(route_path)
    require(resource.get("run_id") == manifest.get("run_id") == run.name,
            "resource/manifest/directory run identity mismatch")

    settings = resource.get("settings") or {}
    require(settings.get("enabled") is True, "resource measurement disabled")
    require(resource.get("provider") == settings.get("provider") == "linux-procfs-v1",
            "unexpected resource provider")
    require(resource.get("provider_error") is None and resource.get("interval_error") is None,
            "resource provider/interval error")

    stages = resource.get("stages")
    processes = resource.get("processes")
    require(isinstance(stages, list) and stages, "resource stages are missing")
    require(isinstance(processes, dict) and processes, "resource process totals are missing")

    stage_cpu = 0.0
    for row in stages:
        require(isinstance(row, dict) and row.get("complete") is True,
                "incomplete measured stage")
        stage_cpu += _number(row.get("cpu_ms"), "stage cpu_ms")
        _number(row.get("wall_ms"), "stage wall_ms")

    process_cpu = 0.0
    for name, row in processes.items():
        require(isinstance(name, str) and name and isinstance(row, dict), "malformed process total")
        require(row.get("complete") is True, f"incomplete process total: {name}")
        process_cpu += _number(row.get("cpu_ms"), f"{name} cpu_ms")

    controller = resource.get("controller") or {}
    controller_cpu = _number(controller.get("cpu_ms"), "controller cpu_ms")
    _close(resource.get("stage_engine_cpu_ms"), round(stage_cpu, 3), "stage_engine_cpu_ms")
    _close(resource.get("engine_cpu_ms"), round(process_cpu, 3), "engine_cpu_ms")
    physical = process_cpu + controller_cpu
    _close(resource.get("physical_cpu_ms"), round(physical, 3), "physical_cpu_ms")

    cpu_complete = True
    gpu_complete = False
    coverage = resource.get("coverage") or {}
    cpu = coverage.get("cpu") or {}
    gpu = coverage.get("gpu") or {}
    require(cpu.get("required") is settings.get("require_cpu_for_claim"),
            "CPU-required coverage flag differs from settings")
    require(gpu.get("required") is settings.get("require_gpu_for_claim"),
            "GPU-required coverage flag differs from settings")
    require(cpu.get("complete") is cpu_complete, "CPU coverage flag contradicts primitive rows")
    require(gpu.get("complete") is gpu_complete, "GPU coverage flag contradicts provider semantics")

    expected_qualified = bool(
        settings.get("enabled")
        and (cpu_complete if settings.get("require_cpu_for_claim") else True)
        and (gpu_complete if settings.get("require_gpu_for_claim") else True)
    )
    require(resource.get("qualified") is expected_qualified,
            "resource qualified flag contradicts reconstructed coverage")

    route_resource = route.get("resource_measurement") or {}
    require(route_resource.get("path") == "resource.json", "route points at wrong resource artifact")
    require(route_resource.get("sha256") == sha(resource_path), "route does not hash-bind resource.json")
    for key in ("report_id", "provider", "qualified", "physical_cpu_ms", "engine_cpu_ms", "controller_cpu_ms"):
        expected = resource.get(key) if key != "controller_cpu_ms" else controller.get("cpu_ms")
        require(route_resource.get(key) == expected, f"route/resource {key} mismatch")

    time_plan = manifest.get("time_plan") or {}
    route_plan = route.get("time_plan") or {}
    require(route_plan.get("plan_id") == time_plan.get("plan_id"), "route/manifest TimePlan mismatch")
    envelope_doc = route.get("envelope") or {}
    budget = route.get("budget") or {}
    require(budget.get("envelope") == envelope_doc, "budget/envelope identity mismatch")
    envelope = ResourceEnvelope.from_config(envelope_doc)

    purpose = budget.get("purpose_totals") or {}
    require(set(purpose) >= {"solver", "verify", "refine", "controller"}, "budget purpose totals missing")
    committed_cpu = 0.0
    committed_gpu = 0.0
    committed_by_purpose = {}
    for name in ("solver", "verify", "refine", "controller"):
        row = purpose[name]
        cpu_value = _number(row.get("reserved_cpu_ms"), f"{name} reserved cpu") + _number(
            row.get("spent_cpu_ms"), f"{name} spent cpu")
        gpu_value = _number(row.get("reserved_gpu_ms"), f"{name} reserved gpu") + _number(
            row.get("spent_gpu_ms"), f"{name} spent gpu")
        committed_by_purpose[name] = (cpu_value, gpu_value)
        committed_cpu += cpu_value
        committed_gpu += gpu_value

    _close(budget.get("committed_cpu_ms"), round(committed_cpu, 3), "budget committed_cpu_ms")
    _close(budget.get("committed_gpu_ms"), round(committed_gpu, 3), "budget committed_gpu_ms")
    within_envelope = committed_cpu <= envelope.cpu_ms + 1e-6 and committed_gpu <= envelope.gpu_ms + 1e-6
    require(budget.get("within_envelope") is within_envelope, "budget within_envelope flag is not derived")

    caps = {
        "solver": (envelope.solver_cpu_ceiling_ms, envelope.solver_gpu_ceiling_ms),
        "verify": (envelope.verification_reserve_ms, envelope.verification_gpu_reserve_ms),
        "refine": (envelope.refinement_reserve_ms, envelope.refinement_gpu_reserve_ms),
        "controller": (envelope.controller_overhead_reserve_ms, 0.0),
    }
    within_partitions = all(
        committed_by_purpose[name][0] <= caps[name][0] + 1e-6
        and committed_by_purpose[name][1] <= caps[name][1] + 1e-6
        for name in caps
    )
    require(budget.get("within_partition_caps") is within_partitions,
            "budget partition flag contradicts purpose totals")

    parsed_anchor = parse_go_request(str(time_plan.get("anchor_go_command", "")))
    limits = {row.get("name"): row.get("value") for row in parsed_anchor.get("limits", [])}
    anchor_bounded = (
        not parsed_anchor.get("unknown_tokens")
        and set(limits) == {"movetime"}
        and type(limits.get("movetime")) is int
        and 0 < limits["movetime"] <= int(time_plan.get("soft_budget_ms", -1))
    )
    anchor_reserved = committed_by_purpose["solver"][0] > 0
    gpu_accounted = committed_gpu <= envelope.gpu_ms + 1e-6
    settlement_complete = budget.get("open_reservations") == 0
    wall_within = _number(budget.get("elapsed_ms"), "budget elapsed_ms") <= envelope.wall_ms + 1e-6
    physical_within = physical <= envelope.cpu_ms + 1e-6
    clock_complete = (manifest.get("clock_outcome") or {}).get("output_within_deadline") is True

    claim = route.get("envelope_claim") or {}
    derived = {
        "anchor_request_bounded": anchor_bounded,
        "anchor_cost_reserved": anchor_reserved,
        "gpu_accounted": gpu_accounted,
        "reservations_within_envelope": within_envelope,
        "specialist_partitions_within_caps": within_partitions,
        "specialist_settlement_complete": settlement_complete,
        "wall_within_envelope": wall_within,
        "physical_measurement_required": bool(settings.get("require_cpu_for_claim") or settings.get("require_gpu_for_claim")),
        "physical_measurement_qualified": expected_qualified,
        "physical_cpu_within_envelope": physical_within,
        "clock_output_complete": clock_complete,
    }
    for key, value in derived.items():
        require(claim.get(key) is value, f"envelope claim {key} contradicts reconstructed evidence")
    expected_claim = all((
        clock_complete, anchor_bounded, anchor_reserved, gpu_accounted,
        within_envelope, within_partitions, settlement_complete, wall_within,
        expected_qualified, physical_within,
    ))
    require(claim.get("claimed") is expected_claim,
            "envelope claimed flag contradicts reconstructed evidence")
    return {
        "qualified": expected_qualified,
        "claimed": expected_claim,
        "physical_cpu_ms": round(physical, 3),
    }


def finite_metrics(summary: dict) -> None:
    require((summary.get("resources") or {}).get("cpu_complete") is True,
            "session CPU coverage is incomplete")
    for metric in summary["search_metrics"]:
        value = metric["cpu_ms_observed"]
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                "missing/nonfinite per-search CPU observation")
    for key in ("reaped_subtree_cpu_ms", "proxy_cpu_ms"):
        value = summary["resources"][key]
        require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                "invalid session CPU")
